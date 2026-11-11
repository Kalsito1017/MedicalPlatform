"""System logs: registry, audit/operational merge, filters, replay, access."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.audit.logger import write_audit_event
from app.logs.bus import bus
from app.logs.registry import audit_event_spec, sanitize_metadata
from app.logs.service import (
    LogFilters,
    build_audit_event_payload,
    query_events,
    record_operational_event,
    replay_events,
)
from app.main import _record_api_failure
from app.models import SystemEvent

pytestmark = pytest.mark.postgres

LOGS_URL = "/api/v1/admin/logs"


async def _admin(auth_client, make_user):
    account = await make_user(roles=("clinical_admin",))
    return account, await auth_client(account)


async def test_system_logs_are_admin_only(db_engine, auth_client, make_user) -> None:
    for role in ("doctor", "nurse"):
        account = await make_user(roles=(role,))
        client = await auth_client(account)
        assert (await client.get(LOGS_URL)).status_code == 403
        assert (await client.get(f"{LOGS_URL}/stream")).status_code == 403
    admin, client = await _admin(auth_client, make_user)
    response = await client.get(LOGS_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "items",
        "total",
        "limit",
        "offset",
        "counts",
        "start",
        "end",
        "timezone",
    }
    assert set(body["counts"]) == {"total", "info", "success", "warning", "error", "security"}


async def test_registry_and_metadata_redaction() -> None:
    spec, event_type, metadata = audit_event_spec("patient.read", "success", "patient")
    assert spec.category == "patients"
    assert spec.severity == "info"
    assert event_type == "patient.read"
    assert metadata == {}

    encounter_spec, _, _ = audit_event_spec("patient.read", "success", "encounter")
    assert encounter_spec.category == "encounters"

    mcp_spec, mcp_type, mcp_metadata = audit_event_spec(
        "mcp.get_patient_summary", "success", "patient"
    )
    assert mcp_spec.category == "mcp"
    assert mcp_type == "mcp.tool.call"
    assert mcp_metadata == {"tool": "get_patient_summary"}

    clean = sanitize_metadata(
        {
            "language": "en",
            "duration_ms": 321,
            "transcript": "must not persist",
            "patient_name": "must not persist",
            "access_token": "must not persist",
            "nested": {"secret": "nope"},
        }
    )
    assert clean == {"language": "en", "duration_ms": 321}

    denied, _, _ = audit_event_spec("authz.denied", "denied", "permission")
    assert denied.severity == "security"


async def test_audit_live_publish_happens_only_after_commit(db_engine, make_user) -> None:
    doctor = await make_user(roles=("doctor",))
    queue = bus.subscribe()
    try:
        async with AsyncSession(db_engine, expire_on_commit=False) as db:
            event = await write_audit_event(
                db,
                actor_user_id=doctor.id,
                action="patient.read",
                resource_type="patient",
                resource_id=str(uuid.uuid4()),
                outcome="success",
                request_id=f"live-{uuid.uuid4().hex[:8]}",
            )
            await db.commit()
        published = queue.get_nowait()
        assert published["id"] == f"audit:{event.id}"
        assert published["event_type"] == "patient.read"

        while not queue.empty():
            queue.get_nowait()
        async with AsyncSession(db_engine, expire_on_commit=False) as db:
            await write_audit_event(
                db,
                actor_user_id=doctor.id,
                action="patient.read",
                resource_type="patient",
                resource_id=str(uuid.uuid4()),
                outcome="success",
                request_id=f"rollback-{uuid.uuid4().hex[:8]}",
            )
            await db.rollback()
        assert queue.empty(), "rolled-back audit rows must not reach the live stream"
    finally:
        bus.unsubscribe(queue)


async def test_operational_events_are_persisted_bounded_and_redacted(
    db_engine, make_user
) -> None:
    doctor = await make_user(roles=("doctor",))
    token = uuid.uuid4().hex[:8]
    ok = await record_operational_event(
        event_type="voice.transcription.completed",
        actor_id=doctor.id,
        request_id=f"voice-{token}",
        outcome="success",
        metadata={
            "language": "bg",
            "duration_ms": 812,
            "audio_duration_seconds": 7.5,
            "transcript": "never store this",
        },
    )
    assert ok is True
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        row = await db.scalar(
            select(SystemEvent).where(SystemEvent.request_id == f"voice-{token}")
        )
        assert row is not None
        assert row.event_type == "voice.transcription.completed"
        assert row.safe_metadata == {
            "language": "bg",
            "duration_ms": 812,
            "audio_duration_seconds": 7.5,
        }
    with pytest.raises(ValueError):
        await record_operational_event(event_type="arbitrary.command")


async def test_history_merge_filters_counts_and_cursor_replay(
    db_engine, clinical_data, make_user
) -> None:
    doctor = await make_user(roles=("doctor",))
    token = uuid.uuid4().hex[:8]
    request_id = f"history-{token}"
    now = datetime.now(timezone.utc)
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        await write_audit_event(
            db,
            actor_user_id=doctor.id,
            action="patient.read",
            resource_type="patient",
            resource_id=str(clinical_data.patient_id),
            outcome="success",
            request_id=request_id,
        )
        await db.commit()
    assert await record_operational_event(
        event_type="ai.request.completed",
        actor_id=doctor.id,
        request_id=f"ai-{token}",
        outcome="success",
        metadata={"duration_ms": 1420, "model": "deepseek-v4.1-flash"},
    )
    filters = LogFilters(start=now - timedelta(minutes=5), end=now + timedelta(minutes=5))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        page = await query_events(db, filters, limit=100, offset=0)
        ids = [item["id"] for item in page["items"]]
        assert any(item.startswith("audit:") for item in ids)
        assert any(item.startswith("op:") for item in ids)
        assert page["total"] == page["counts"]["total"]
        assert page["counts"]["info"] >= 1
        assert page["counts"]["success"] >= 1

        only_request = await query_events(
            db, LogFilters(start=now - timedelta(minutes=5), end=now + timedelta(minutes=5), request_id=request_id), limit=10, offset=0
        )
        assert only_request["total"] == 1
        assert only_request["items"][0]["event_type"] == "patient.read"

        only_ai = await query_events(
            db,
            LogFilters(
                start=now - timedelta(minutes=5),
                end=now + timedelta(minutes=5),
                category="ai",
            ),
            limit=10,
            offset=0,
        )
        assert only_ai["total"] >= 1
        assert all(item["category"] == "ai" for item in only_ai["items"])

        cursor = only_request["items"][0]["id"]
        replay = await replay_events(
            db,
            LogFilters(start=now - timedelta(minutes=5), end=now + timedelta(minutes=5)),
            cursor,
        )
        assert all((item["timestamp"], item["id"]) > (only_request["items"][0]["timestamp"], cursor) for item in replay)
        assert replay == sorted(replay, key=lambda item: (item["timestamp"], item["id"]))


async def test_audit_event_payload_omits_email_and_clinical_fields() -> None:
    payload = build_audit_event_payload(
        event_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        action="auth.login.failure",
        resource_type="email",
        resource_id="person@example.com",
        request_id="req-redact",
        outcome="denied",
        actor_user_id=None,
    )
    assert payload["resource_reference"] is None
    assert "person@example.com" not in str(payload)
    assert payload["severity"] == "security"


async def test_api_failure_events_are_operational_and_bounded(db_engine) -> None:
    request_id = f"api-error-{uuid.uuid4().hex[:8]}"
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/patients/00000000-0000-0000-0000-000000000000",
        "raw_path": b"/api/v1/patients/00000000-0000-0000-0000-000000000000",
        "query_string": b"secret=must-not-be-stored",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "state": {"request_id": request_id},
    }
    await _record_api_failure(Request(scope), status=500, code="internal_error")
    await _record_api_failure(Request(scope), status=429, code="rate_limited")
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        rows = (
            await db.execute(
                select(SystemEvent)
                .where(SystemEvent.request_id == request_id)
                .order_by(SystemEvent.created_at)
            )
        ).scalars().all()
    assert [row.event_type for row in rows] == ["system.api_error", "system.rate_limited"]
    for row in rows:
        assert row.safe_metadata == {
            "method": "GET",
            "route": "/api/v1/patients/00000000-0000-0000-0000-000000000000",
            "http_status": 500 if row.event_type == "system.api_error" else 429,
            "error_code": "internal_error" if row.event_type == "system.api_error" else "rate_limited",
        }
        assert "secret" not in str(row.safe_metadata)


async def test_email_resource_is_not_searchable_in_system_logs(
    db_engine, make_user
) -> None:
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex[:8]
    attempted = f"secret-{token}@example.com"
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        await write_audit_event(
            db,
            actor_user_id=None,
            action="auth.login.failure",
            resource_type="email",
            resource_id=attempted,
            outcome="denied",
            request_id=f"email-query-{token}",
        )
        await db.commit()
    filters = lambda value: LogFilters(  # noqa: E731
        start=now - timedelta(minutes=5), end=now + timedelta(minutes=5), query=value
    )
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        by_email = await query_events(db, filters(attempted), limit=10, offset=0)
        by_request = await query_events(
            db, filters(f"email-query-{token}"), limit=10, offset=0
        )
    assert by_email["total"] == 0
    assert by_request["total"] == 1
    assert attempted not in str(by_request["items"])
