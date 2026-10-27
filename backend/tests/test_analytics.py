"""Analytics summary tests (docs/API.md "Analytics", wave U).

Guarantees asserted here:
  1. `analytics:read` enforcement at the API layer: Admin 200, doctor/nurse
     403, anonymous 401 (the MATRIX guard additionally declares the route).
  2. The response shape is EXACT and typed (ints are ints, lists bounded,
     zero-filled, `data_provenance: "synthetic"`).
  3. Every number matches a direct query over the database (real aggregates,
     never hardcoded guesses — fixtures vary between runs).
  4. The abnormal-lab rule is THE MCP rule: cross-checked against the real
     `find_patients_by_lab` lookup tool on a crafted known-abnormal fixture,
     with `status` proven to be a document state and never an abnormality flag.
  5. Each read writes exactly one `analytics.read` audit event (metadata only).
  6. Bounded query count (no N+1) and a latency sanity bound against the
     seeded demo dataset.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import audit_rows, csrf_headers  # noqa: F401
from test_permissions_api import MATRIX

SUMMARY_URL = "/api/v1/analytics/summary"

EXPECTED_KEYS = {
    "generated_at",
    "counts",
    "last_30_days",
    "lab_status_mix",
    "abnormal_lab_rate",
    "top_abnormal_tests",
    "prescriptions_by_status",
    "staff_by_role",
    "audit_activity",
    "ai_usage",
    "data_provenance",
}


async def _direct_count(db, model, *criteria) -> int:
    stmt = select(func.count()).select_from(model)
    if criteria:
        stmt = stmt.where(*criteria)
    return int((await db.scalar(stmt)) or 0)


@pytest.mark.postgres
async def test_analytics_route_is_declared_in_the_permission_matrix() -> None:
    declared = {(case.method, case.template) for case in MATRIX}
    assert ("GET", SUMMARY_URL) in declared


@pytest.mark.postgres
async def test_summary_requires_analytics_read(auth_client, make_user, make_client) -> None:
    anonymous = await make_client()
    response = await anonymous.get(SUMMARY_URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"

    for role in ("doctor", "nurse"):
        client = await auth_client(await make_user(roles=(role,)))
        response = await client.get(SUMMARY_URL)
        assert response.status_code == 403, role
        assert response.json()["error"]["code"] == "forbidden"

    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(SUMMARY_URL)
    assert response.status_code == 200, response.text


@pytest.mark.postgres
async def test_summary_rejects_query_parameters(auth_client, make_user) -> None:
    """The endpoint is ONE call with no options (docs/API.md "Analytics")."""
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    for bad in ("?limit=1", "?days=7", "?extra=1"):
        response = await client.get(f"{SUMMARY_URL}{bad}")
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.postgres
async def test_summary_response_shape_is_exact_and_typed(auth_client, make_user) -> None:
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(SUMMARY_URL)
    assert response.status_code == 200
    body = response.json()

    assert set(body) == EXPECTED_KEYS
    assert body["data_provenance"] == "synthetic"
    assert isinstance(body["generated_at"], str) and body["generated_at"].endswith("Z")
    datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))

    assert set(body["counts"]) == {
        "patients",
        "encounters",
        "labs",
        "prescriptions",
        "active_prescriptions",
        "diagnoses_active",
        "staff_active",
        "ai_conversations",
    }
    assert set(body["last_30_days"]) == {
        "encounters",
        "lab_results",
        "prescriptions_written",
        "ai_asks",
        "logins",
        "permission_denials",
    }
    assert set(body["lab_status_mix"]) == {"final", "preliminary", "pending"}
    assert set(body["abnormal_lab_rate"]) == {"abnormal", "total", "pct"}
    assert set(body["prescriptions_by_status"]) == {
        "active",
        "completed",
        "stopped",
        "cancelled",
    }
    assert set(body["ai_usage"]) == {
        "total_asks",
        "general_asks",
        "patient_asks",
        "top_tools",
    }

    # Every count is a real integer (never a float, never a numeric string).
    for block in ("counts", "last_30_days", "lab_status_mix", "prescriptions_by_status"):
        for key, value in body[block].items():
            assert isinstance(value, int) and not isinstance(value, bool), (block, key)
            assert value >= 0, (block, key)
    rate = body["abnormal_lab_rate"]
    for key in ("abnormal", "total"):
        assert isinstance(rate[key], int) and rate[key] >= 0
    # Documented choice: pct is a rounded DISPLAY number (1 decimal), not a
    # clinical value — every clinical value elsewhere stays Decimal-as-string.
    assert isinstance(rate["pct"], (int, float)) and not isinstance(rate["pct"], bool)
    assert 0.0 <= float(rate["pct"]) <= 100.0
    assert rate["abnormal"] <= rate["total"]

    # Bounded, typed lists.
    assert len(body["top_abnormal_tests"]) <= 8
    for entry in body["top_abnormal_tests"]:
        assert set(entry) == {"test_code", "test_name", "abnormal_count"}
        assert isinstance(entry["abnormal_count"], int) and entry["abnormal_count"] >= 1
    counts_desc = [e["abnormal_count"] for e in body["top_abnormal_tests"]]
    assert counts_desc == sorted(counts_desc, reverse=True)

    assert len(body["audit_activity"]) == 14
    for entry in body["audit_activity"]:
        assert set(entry) == {"day", "logins", "denials", "record_reads"}
        assert isinstance(entry["day"], str) and len(entry["day"]) == 10
        for key in ("logins", "denials", "record_reads"):
            assert isinstance(entry[key], int) and entry[key] >= 0
    days = [entry["day"] for entry in body["audit_activity"]]
    assert days == sorted(days), "oldest first"
    assert days[-1] == datetime.now(timezone.utc).date().isoformat(), "includes today"

    assert len(body["staff_by_role"]) == 3
    assert {entry["role"] for entry in body["staff_by_role"]} == {
        "doctor",
        "nurse",
        "clinical_admin",
    }
    for entry in body["staff_by_role"]:
        assert set(entry) == {"role", "count"}
        assert isinstance(entry["count"], int) and entry["count"] >= 0

    assert len(body["ai_usage"]["top_tools"]) <= 6
    for entry in body["ai_usage"]["top_tools"]:
        assert set(entry) == {"tool", "calls"}
        assert isinstance(entry["calls"], int) and entry["calls"] >= 1
    assert (
        body["ai_usage"]["general_asks"] + body["ai_usage"]["patient_asks"]
        == body["ai_usage"]["total_asks"]
    )


@pytest.mark.postgres
async def test_summary_numbers_match_direct_queries(db_engine, auth_client, make_user) -> None:
    """Real aggregates: every block equals a direct query over the same tables."""
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(SUMMARY_URL)
    assert response.status_code == 200
    body = response.json()

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    window_start = datetime.combine(
        datetime.now(timezone.utc).date() - timedelta(days=13),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    from app.models import (
        AiConversation,
        AuditEvent,
        Diagnosis,
        Encounter,
        LabResult,
        Patient,
        Prescription,
        Role,
        StaffUser,
        UserRole,
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        expected_counts = {
            "patients": await _direct_count(db, Patient, Patient.deleted_at.is_(None)),
            "encounters": await _direct_count(db, Encounter, Encounter.deleted_at.is_(None)),
            "labs": await _direct_count(db, LabResult, LabResult.deleted_at.is_(None)),
            "prescriptions": await _direct_count(
                db, Prescription, Prescription.deleted_at.is_(None)
            ),
            "active_prescriptions": await _direct_count(
                db,
                Prescription,
                Prescription.deleted_at.is_(None),
                Prescription.status == "active",
            ),
            "diagnoses_active": await _direct_count(
                db,
                Diagnosis,
                Diagnosis.deleted_at.is_(None),
                Diagnosis.status == "active",
            ),
            "staff_active": await _direct_count(
                db, StaffUser, StaffUser.deleted_at.is_(None), StaffUser.is_active.is_(True)
            ),
            "ai_conversations": await _direct_count(db, AiConversation),
        }
        assert body["counts"] == expected_counts

        expected_30 = {
            "encounters": await _direct_count(
                db,
                Encounter,
                Encounter.deleted_at.is_(None),
                Encounter.occurred_at >= cutoff,
            ),
            "lab_results": await _direct_count(
                db,
                LabResult,
                LabResult.deleted_at.is_(None),
                LabResult.collected_at >= cutoff,
            ),
            "prescriptions_written": await _direct_count(
                db,
                Prescription,
                Prescription.deleted_at.is_(None),
                Prescription.start_date >= cutoff.date(),
            ),
            "ai_asks": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "ai.ask",
                AuditEvent.outcome == "success",
                AuditEvent.created_at >= cutoff,
            ),
            "logins": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "auth.login.success",
                AuditEvent.created_at >= cutoff,
            ),
            "permission_denials": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "authz.denied",
                AuditEvent.created_at >= cutoff,
            ),
        }
        assert body["last_30_days"] == expected_30

        status_rows = (
            await db.execute(
                select(LabResult.status, func.count())
                .where(LabResult.deleted_at.is_(None))
                .group_by(LabResult.status)
            )
        ).all()
        status_counts = {str(status): int(count) for status, count in status_rows}
        assert body["lab_status_mix"] == {
            key: status_counts.get(key, 0) for key in ("final", "preliminary", "pending")
        }

        rx_rows = (
            await db.execute(
                select(Prescription.status, func.count())
                .where(Prescription.deleted_at.is_(None))
                .group_by(Prescription.status)
            )
        ).all()
        rx_counts = {str(status): int(count) for status, count in rx_rows}
        assert body["prescriptions_by_status"] == {
            key: rx_counts.get(key, 0)
            for key in ("active", "completed", "stopped", "cancelled")
        }

        # Abnormal rule re-implemented independently in plain Python over the
        # raw rows (docs/API.md "MCP tool contract": value_num strictly outside
        # [ref_low, ref_high]; `status` ignored; text-only never abnormal).
        lab_rows = (
            await db.execute(
                select(
                    LabResult.test_code,
                    LabResult.test_name,
                    LabResult.value_num,
                    LabResult.ref_low,
                    LabResult.ref_high,
                ).where(LabResult.deleted_at.is_(None))
            )
        ).all()

        def is_abnormal(value, low, high):
            if value is None:
                return False
            return (high is not None and value > high) or (low is not None and value < low)

        evaluable = [
            row
            for row in lab_rows
            if row.value_num is not None
            and (row.ref_low is not None or row.ref_high is not None)
        ]
        abnormal_rows = [
            row
            for row in lab_rows
            if is_abnormal(row.value_num, row.ref_low, row.ref_high)
        ]
        assert body["abnormal_lab_rate"]["abnormal"] == len(abnormal_rows)
        assert body["abnormal_lab_rate"]["total"] == len(evaluable)
        if evaluable:
            expected_pct = round(100 * len(abnormal_rows) / len(evaluable), 1)
            assert float(body["abnormal_lab_rate"]["pct"]) == pytest.approx(
                expected_pct, abs=0.05
            )
        else:
            assert float(body["abnormal_lab_rate"]["pct"]) == 0.0

        grouped: dict[tuple[str, str], int] = {}
        for row in abnormal_rows:
            key = (str(row.test_code), str(row.test_name))
            grouped[key] = grouped.get(key, 0) + 1
        expected_top = [
            {"test_code": code, "test_name": name, "abnormal_count": count}
            for (code, name), count in sorted(
                grouped.items(), key=lambda item: (-item[1], item[0][0], item[0][1])
            )[:8]
        ]
        assert body["top_abnormal_tests"] == expected_top

        role_rows = (
            await db.execute(
                select(Role.key, func.count(func.distinct(UserRole.user_id)))
                .select_from(UserRole)
                .join(Role, Role.id == UserRole.role_id)
                .join(StaffUser, StaffUser.id == UserRole.user_id)
                .where(StaffUser.deleted_at.is_(None), StaffUser.is_active.is_(True))
                .group_by(Role.key)
            )
        ).all()
        role_counts = {str(key): int(count) for key, count in role_rows}
        assert body["staff_by_role"] == [
            {"role": role, "count": role_counts.get(role, 0)}
            for role in ("doctor", "nurse", "clinical_admin")
        ]

        # Audit chart: totals per series equal direct counts over the window.
        for key, action in (
            ("logins", "auth.login.success"),
            ("denials", "authz.denied"),
        ):
            expected = await _direct_count(
                db, AuditEvent, AuditEvent.action == action, AuditEvent.created_at >= window_start
            )
            assert sum(entry[key] for entry in body["audit_activity"]) == expected
        reads = await _direct_count(
            db,
            AuditEvent,
            AuditEvent.action.in_(
                ("patient.read", "patient.summary.read", "labs.read", "prescriptions.read")
            ),
            AuditEvent.created_at >= window_start,
        )
        assert sum(entry["record_reads"] for entry in body["audit_activity"]) == reads

        # AI usage: successful ai.ask events partition by patient subject.
        total_asks = await _direct_count(
            db, AuditEvent, AuditEvent.action == "ai.ask", AuditEvent.outcome == "success"
        )
        patient_asks = await _direct_count(
            db,
            AuditEvent,
            AuditEvent.action == "ai.ask",
            AuditEvent.outcome == "success",
            AuditEvent.resource_type == "patient",
        )
        assert body["ai_usage"]["total_asks"] == total_asks
        assert body["ai_usage"]["patient_asks"] == patient_asks
        assert body["ai_usage"]["general_asks"] == total_asks - patient_asks


@pytest.mark.postgres
async def test_abnormal_rule_matches_the_mcp_lookup_tool(
    db_engine, clinical_data, make_user, monkeypatch
) -> None:
    """The abnormal definition is THE MCP rule (docs/API.md "MCP tool contract").

    Crafted fixture (unique code per test): abnormal high (document status
    `preliminary` — status is NOT an abnormality flag), abnormal low, normal,
    a text-only result, and a numeric result without reference bounds. The
    analytics deltas must classify exactly like the real
    `find_patients_by_lab(abnormal_only=True)` MCP tool.
    """
    from app.models import LabResult
    from app.services.analytics import build_summary

    token = clinical_data.token
    code = f"AT{token}"
    name = f"AbnormalTest {token}"
    now = datetime.now(timezone.utc)
    ref_low, ref_high = Decimal("3.500"), Decimal("5.100")

    def lab(patient_id, *, value=None, text=None, status, collected, with_refs=True):
        return LabResult(
            id=uuid.uuid4(),
            patient_id=patient_id,
            test_code=code,
            test_name=name,
            value_num=Decimal(value) if value is not None else None,
            value_text=text,
            unit="mmol/L",
            ref_low=ref_low if (with_refs and text is None) else None,
            ref_high=ref_high if (with_refs and text is None) else None,
            status=status,
            collected_at=datetime.fromisoformat(collected).replace(tzinfo=timezone.utc),
        )

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        before = await build_summary(db, now=now)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                lab(
                    clinical_data.patient_id,
                    value="4.500",
                    status="final",
                    collected="2026-10-01T08:00:00",
                ),
                lab(
                    clinical_data.patient_id,
                    value="3.000",
                    status="final",
                    collected="2026-10-03T08:00:00",
                ),
                lab(
                    clinical_data.patient_id,
                    value="5.800",
                    status="preliminary",
                    collected="2026-10-05T08:00:00",
                ),
                lab(
                    clinical_data.empty_patient_id,
                    text="within range",
                    status="final",
                    collected="2026-10-01T08:00:00",
                ),
                lab(
                    clinical_data.empty_patient_id,
                    value="7.700",
                    status="pending",
                    collected="2026-10-02T08:00:00",
                    with_refs=False,
                ),
            ]
        )
        await session.commit()
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        after = await build_summary(db, now=now)

    # Exactly 2 abnormal (high + low; the `preliminary` status one included),
    # 3 evaluable (text-only and bound-less rows are outside the rate).
    delta_abnormal = (
        after["abnormal_lab_rate"]["abnormal"] - before["abnormal_lab_rate"]["abnormal"]
    )
    delta_total = after["abnormal_lab_rate"]["total"] - before["abnormal_lab_rate"]["total"]
    assert delta_abnormal == 2
    assert delta_total == 3

    # Status is the document state only: the mix moves by the crafted rows'
    # statuses, independent of abnormality.
    for status, delta in (("final", 3), ("preliminary", 1), ("pending", 1)):
        assert (
            after["lab_status_mix"][status] - before["lab_status_mix"][status] == delta
        ), status

    # The unique test code is counted exactly once, with exactly 2 abnormal
    # (the list is bounded to the top 8 by count, so widen the bound to see
    # the small crafted fixture alongside the seeded cohort).
    monkeypatch.setattr("app.services.analytics.TOP_ABNORMAL_LIMIT", 500)
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        widened = await build_summary(db, now=now)
    entry = next(
        item for item in widened["top_abnormal_tests"] if item["test_code"] == code
    )
    assert entry == {"test_code": code, "test_name": name, "abnormal_count": 2}

    # --- cross-check against the real MCP lookup tool -----------------------
    from app.authorization.policy import Principal, permissions_for_roles
    from app.mcp.context import ToolContext
    from app.mcp.server import mcp_server

    account = await make_user(roles=("doctor",))
    principal = Principal(
        user_id=str(account.id),
        roles=account.roles,
        is_active=True,
        email=account.email,
        permissions=permissions_for_roles(account.roles),
    )
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        ctx = ToolContext(
            principal=principal, db=session, request_id="test-analytics-mcp", ip="127.0.0.1"
        )
        abnormal = await mcp_server.call_tool(
            "find_patients_by_lab",
            {"test_code": code, "abnormal_only": True, "limit": 50},
            ctx,
        )
        assert abnormal.ok is True
        rows = abnormal.model_payload["data"]["patients"]
        listed = {row["patient_id"]: row["matched"] for row in rows}
        # The abnormal high result (status "preliminary") is found exactly like
        # the analytics aggregate found it; text-only/normal-only never qualify.
        assert str(clinical_data.patient_id) in listed
        assert "high" in listed[str(clinical_data.patient_id)]
        assert str(clinical_data.empty_patient_id) not in listed

        all_rows = await mcp_server.call_tool(
            "find_patients_by_lab",
            {"test_code": code, "abnormal_only": False, "limit": 50},
            ctx,
        )
        everything = {
            row["patient_id"]: row["matched"]
            for row in all_rows.model_payload["data"]["patients"]
        }
        assert str(clinical_data.empty_patient_id) in everything
        assert "high" not in everything[str(clinical_data.empty_patient_id)]
        assert "low" not in everything[str(clinical_data.empty_patient_id)]


@pytest.mark.postgres
async def test_analytics_read_is_audited(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    response = await client.get(SUMMARY_URL)
    assert response.status_code == 200

    rows = await audit_rows(action="analytics.read", actor_user_id=account.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "success"
    assert row.resource_type == "analytics"
    assert row.resource_id is None
    assert row.request_id == response.headers["X-Request-Id"]
    # Metadata only: the audit row never carries payloads or secrets.
    blob = " ".join(
        str(value)
        for value in (row.action, row.resource_type, row.resource_id, row.request_id)
        if value is not None
    )
    assert "Metformin" not in blob and "DEMO-" not in blob and "@" not in blob


@pytest.mark.postgres
async def test_summary_is_zero_filled_and_bounded(auth_client, make_user) -> None:
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    body = (await client.get(SUMMARY_URL)).json()

    activity = body["audit_activity"]
    assert len(activity) == 14
    today = datetime.now(timezone.utc).date()
    expected_days = [
        (today - timedelta(days=13 - index)).isoformat() for index in range(14)
    ]
    assert [entry["day"] for entry in activity] == expected_days
    # Zero-filled: every day is present even with no events at all.
    for entry in activity:
        assert set(entry) == {"day", "logins", "denials", "record_reads"}

    # Closed vocabularies are always fully listed (never partial/missing keys).
    assert set(body["lab_status_mix"]) == {"final", "preliminary", "pending"}
    assert set(body["prescriptions_by_status"]) == {
        "active",
        "completed",
        "stopped",
        "cancelled",
    }
    assert [entry["role"] for entry in body["staff_by_role"]] == [
        "doctor",
        "nurse",
        "clinical_admin",
    ]


@pytest.mark.postgres
async def test_summary_uses_bounded_queries_and_completes_on_the_seeded_dataset(
    db_engine, auth_client, make_user
) -> None:
    """Performance sanity: one bounded pass of grouped aggregates (no N+1).

    The statement count is asserted directly (a handful of aggregate SELECTs);
    the endpoint must answer in one request against the seeded demo dataset
    (45 patients, hundreds of labs/notes) well within an interactive budget.
    """
    from app.services.analytics import build_summary

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        event.listen(db_engine.sync_engine, "before_cursor_execute", _record)
        try:
            started = time.perf_counter()
            payload = await build_summary(db)
            elapsed = time.perf_counter() - started
        finally:
            event.remove(db_engine.sync_engine, "before_cursor_execute", _record)

    assert len(statements) <= 12, f"analytics must not fan out per row: {len(statements)} statements"
    assert elapsed < 5.0, f"build_summary took {elapsed:.2f}s"
    assert payload["counts"]["patients"] >= 1

    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    started = time.perf_counter()
    response = await client.get(SUMMARY_URL)
    request_elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert request_elapsed < 5.0, f"GET {SUMMARY_URL} took {request_elapsed:.2f}s"
