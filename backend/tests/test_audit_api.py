"""Audit API tests (docs/SECURITY.md audit design, GET /api/v1/audit).

Read API shape (actor reference + email/display name, filters, ordering,
pagination bounds) and the write-side guarantees: the expected events exist and
no audit row ever contains a password, token, or clinical payload.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent
from conftest import SENTINEL_PASSWORD, audit_rows, csrf_headers

AUDIT_URL = "/api/v1/audit"

KNOWN_ACTIONS = {
    "auth.login.success",
    "auth.login.failure",
    "auth.logout",
    "authz.denied",
    "patient.read",
    "patient.summary.read",
    "labs.read",
    "prescriptions.read",
    "staff.create",
    "staff.update",
    "staff.deactivate",
    "role.grant",
    "role.revoke",
    "session.revoke_all",
    # Phase 7/8 (MCP + LLM agent): AI endpoint and MCP tool-call events. Tool
    # names come from the fixed registry; attempts to call non-existent tools
    # audit under the fixed "mcp.unknown_tool" action (never model input).
    "ai.ask",
    "mcp.search_authorized_patients",
    "mcp.get_patient_summary",
    "mcp.get_patient_lab_results",
    "mcp.get_recent_prescriptions",
    "mcp.find_patients_by_lab",
    "mcp.find_patients_by_medication",
    "mcp.unknown_tool",
}


@pytest.mark.postgres
async def test_audit_requires_audit_read(auth_client, make_user, make_client) -> None:
    anonymous = await make_client()
    assert (await anonymous.get(AUDIT_URL)).status_code == 401

    for role in ("doctor", "nurse"):
        client = await auth_client(await make_user(roles=(role,)))
        response = await client.get(AUDIT_URL)
        assert response.status_code == 403, role

    for role in ("clinical_admin", "tech_admin"):
        client = await auth_client(await make_user(roles=(role,)))
        response = await client.get(AUDIT_URL)
        assert response.status_code == 200, role


@pytest.mark.postgres
async def test_audit_list_shape_ordering_and_actor_join(
    db_engine, auth_client, make_user
) -> None:
    account = await make_user(roles=("clinical_admin",), display_name="Audit Admin")
    client = await auth_client(account)
    created = await client.post(
        "/api/v1/staff",
        json={"email": f"audit-list-{uuid.uuid4().hex[:8]}@example.com", "display_name": "One"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201

    response = await client.get(f"{AUDIT_URL}?limit=50")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] >= 1

    own_events = [item for item in body["items"] if item["actor_user_id"] == str(account.id)]
    assert own_events, "the acting user's events must be listed"
    newest = own_events[0]
    assert set(newest) == {
        "id",
        "actor_user_id",
        "actor_email",
        "actor_display_name",
        "action",
        "resource_type",
        "resource_id",
        "outcome",
        "request_id",
        "ip",
        "created_at",
    }
    assert newest["actor_email"] == account.email
    assert newest["actor_display_name"] == "Audit Admin"
    assert newest["action"] in KNOWN_ACTIONS

    # Newest first.
    created = [item["created_at"] for item in body["items"]]
    assert created == sorted(created, reverse=True)


@pytest.mark.postgres
async def test_audit_filters_and_bounds(auth_client, make_user, clinical_data) -> None:
    account = await make_user(roles=("tech_admin",))
    client = await auth_client(account)
    doctor = await make_user(roles=("doctor",))
    doctor_client = await auth_client(doctor)

    read = await doctor_client.get(f"/api/v1/patients/{clinical_data.patient_id}")
    assert read.status_code == 200

    by_action = await client.get(f"{AUDIT_URL}?action=patient.read&limit=50")
    assert by_action.status_code == 200
    assert by_action.json()["total"] >= 1
    assert all(item["action"] == "patient.read" for item in by_action.json()["items"])

    by_actor = await client.get(f"{AUDIT_URL}?actor_id={doctor.id}&limit=50")
    assert by_actor.json()["total"] >= 1
    assert all(item["actor_user_id"] == str(doctor.id) for item in by_actor.json()["items"])

    by_outcome = await client.get(f"{AUDIT_URL}?outcome=success&limit=50")
    assert all(item["outcome"] == "success" for item in by_outcome.json()["items"])

    empty = await client.get(f"{AUDIT_URL}?action=no.such.action")
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}

    for bad in ("?outcome=nope", "?action=" + "x" * 65, "?actor_id=not-a-uuid", "?limit=101", "?extra=1"):
        response = await client.get(f"{AUDIT_URL}{bad}")
        assert response.status_code == 400, bad


@pytest.mark.postgres
async def test_audit_events_for_auth_authz_reads_and_roles(
    db_engine, auth_client, make_user, make_client, clinical_data
) -> None:
    admin = await make_user(roles=("clinical_admin",))
    target = await make_user(roles=("nurse",))
    admin_client = await auth_client(admin)

    doctor = await make_user(roles=("doctor",))
    login_client = await make_client()
    failure = await login_client.post(
        "/api/v1/auth/login", json={"email": doctor.email, "password": "wrong-password"}
    )
    assert failure.status_code == 401
    success = await login_client.post(
        "/api/v1/auth/login",
        json={"email": doctor.email, "password": doctor.password},
        headers=csrf_headers(login_client),
    )
    assert success.status_code == 200
    read = await login_client.get(f"/api/v1/patients/{clinical_data.patient_id}/labs")
    assert read.status_code == 200
    denied = await login_client.get("/api/v1/staff")  # doctor lacks staff:read
    assert denied.status_code == 403
    grant = await admin_client.post(
        f"/api/v1/staff/{target.id}/roles",
        json={"role": "doctor"},
        headers=csrf_headers(admin_client),
    )
    assert grant.status_code == 200
    revoke = await admin_client.delete(
        f"/api/v1/staff/{target.id}/roles/doctor", headers=csrf_headers(admin_client)
    )
    assert revoke.status_code == 204

    expected = {
        (failure.headers["X-Request-Id"], "auth.login.failure"): 1,
        (success.headers["X-Request-Id"], "auth.login.success"): 1,
        (read.headers["X-Request-Id"], "labs.read"): 1,
        (denied.headers["X-Request-Id"], "authz.denied"): 1,
        (grant.headers["X-Request-Id"], "role.grant"): 1,
        (revoke.headers["X-Request-Id"], "role.revoke"): 1,
    }
    for (request_id, action), count in expected.items():
        rows = await audit_rows(request_id=request_id, action=action)
        assert len(rows) == count, f"{action} via {request_id}"


@pytest.mark.postgres
async def test_audit_rows_never_contain_secrets_or_clinical_payloads(
    db_engine, auth_client, make_user, make_client, clinical_data
) -> None:
    admin = await make_user(roles=("clinical_admin",))
    doctor = await make_user(roles=("doctor",))

    # Exercise the flows that handle the most sensitive input: the sentinel
    # password goes through failed AND successful logins and must never land in
    # an audit row.
    login_client = await make_client()
    failed = await login_client.post(
        "/api/v1/auth/login",
        json={"email": f"ghost-{uuid.uuid4().hex[:8]}@example.com", "password": SENTINEL_PASSWORD},
    )
    assert failed.status_code == 401
    succeeded = await login_client.post(
        "/api/v1/auth/login",
        json={"email": doctor.email, "password": SENTINEL_PASSWORD},
        headers=csrf_headers(login_client),
    )
    assert succeeded.status_code == 200
    session_token = login_client.cookies.get("cap_session")
    assert session_token
    await login_client.get(f"/api/v1/patients/{clinical_data.patient_id}/summary")
    admin_client = await auth_client(admin)
    await admin_client.post(
        "/api/v1/staff",
        json={"email": f"audited-{uuid.uuid4().hex[:8]}@example.com", "display_name": "Audited"},
        headers=csrf_headers(admin_client),
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        rows = list((await session.scalars(select(AuditEvent))).all())

    assert rows, "audit trail must not be empty"
    for row in rows:
        blob = " ".join(
            str(value)
            for value in (
                row.action,
                row.resource_type,
                row.resource_id,
                row.outcome,
                row.request_id,
                row.ip,
            )
            if value is not None
        )
        assert SENTINEL_PASSWORD not in blob
        assert session_token not in blob
        assert row.action in KNOWN_ACTIONS
        # Clinical payload sentinels (names, values) never appear.
        assert clinical_data.patient_name not in blob
        assert "6.100" not in blob
        assert "Metformin" not in blob
