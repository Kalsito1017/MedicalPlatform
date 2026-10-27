"""Phase 11 hardening verification: log redaction and audit coverage.

Two guarantees from docs/SECURITY.md are proven end to end here:

1. Logs never contain credentials, session tokens, search terms, or clinical
   payloads — checked against the real JSON log output of real requests.
2. Every event class listed in docs/SECURITY.md "Audit design" is actually
   emitted by the shipped flows (login success/failure, logout, permission
   denials, patient-data reads, MCP tool calls, staff/role changes).
"""
from __future__ import annotations

import io
import logging
import uuid

import pytest

from app.config import JsonLogFormatter
from conftest import SENTINEL_PASSWORD, audit_rows, csrf_headers

# docs/SECURITY.md "Audit design" inventory. Each must be emitted by the
# scenario below; anything missing is a documentation/code mismatch.
SECURITY_DOC_AUDIT_ACTIONS = {
    "auth.login.success",
    "auth.login.failure",
    "auth.logout",
    "authz.denied",
    "patient.read",
    "patient.summary.read",
    "labs.read",
    "prescriptions.read",
    "mcp.search_authorized_patients",
    "mcp.get_patient_summary",
    "mcp.get_patient_lab_results",
    "mcp.get_recent_prescriptions",
    "mcp.find_patients_by_lab",
    "mcp.find_patients_by_medication",
    "mcp.get_my_recent_patients",
    "staff.create",
    "staff.update",
    "staff.deactivate",
    "session.revoke_all",
    "role.grant",
    "role.revoke",
}


@pytest.mark.postgres
async def test_request_logs_never_contain_credentials_or_clinical_payloads(
    make_user, make_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    httpx_level = logging.getLogger("httpx").level
    root.setLevel(logging.INFO)
    # httpx (client library) logs full request URLs at INFO; that is not the
    # application's log output. The app's own request log carries only the path.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        client = await make_client()
        failed = await client.post(
            "/api/v1/auth/login",
            json={"email": doctor.email, "password": "wrong-password-sentinel"},
        )
        assert failed.status_code == 401
        logged_in = await client.post(
            "/api/v1/auth/login",
            json={"email": doctor.email, "password": SENTINEL_PASSWORD},
            headers=csrf_headers(client),
        )
        assert logged_in.status_code == 200
        session_token = client.cookies.get("cap_session")
        assert session_token
        assert (await client.get(f"/api/v1/patients/{clinical_data.patient_id}")).status_code == 200
        assert (
            await client.get("/api/v1/patients?q=sentinel-search-term&limit=25")
        ).status_code == 200
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        logging.getLogger("httpx").setLevel(httpx_level)

    output = stream.getvalue()
    assert '"request completed"' in output, "structured request logs must exist"

    # Credentials and tokens.
    assert SENTINEL_PASSWORD not in output
    assert "wrong-password-sentinel" not in output
    assert session_token not in output
    csrf_token = client.cookies.get("csrf_token")
    assert csrf_token
    assert csrf_token not in output
    # Clinical payloads and query terms (paths are logged, query strings are not).
    assert clinical_data.patient_name not in output
    assert clinical_data.patient_mrn not in output
    assert "sentinel-search-term" not in output


@pytest.mark.postgres
async def test_security_doc_audit_inventory_is_emitted(
    db_engine, make_user, make_client, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))

    # --- Login success/failure and logout --------------------------------
    client = await make_client()
    failure = await client.post(
        "/api/v1/auth/login", json={"email": doctor.email, "password": "wrong-password"}
    )
    assert failure.status_code == 401
    success = await client.post(
        "/api/v1/auth/login",
        json={"email": doctor.email, "password": SENTINEL_PASSWORD},
        headers=csrf_headers(client),
    )
    assert success.status_code == 200

    # --- Patient-data reads ---------------------------------------------
    for path in (
        f"/api/v1/patients?q={clinical_data.token}&limit=25",
        f"/api/v1/patients/{clinical_data.patient_id}",
        f"/api/v1/patients/{clinical_data.patient_id}/summary",
        f"/api/v1/patients/{clinical_data.patient_id}/labs",
        f"/api/v1/patients/{clinical_data.patient_id}/prescriptions",
    ):
        assert (await client.get(path)).status_code == 200, path

    # --- Permission denial ----------------------------------------------
    assert (await client.get("/api/v1/staff")).status_code == 403

    # --- Logout ----------------------------------------------------------
    assert (
        await client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    ).status_code == 204

    # --- Staff and role changes -----------------------------------------
    admin_client = await auth_client(admin)
    created = await admin_client.post(
        "/api/v1/staff",
        json={"email": f"inventory-{uuid.uuid4().hex[:8]}@example.com", "display_name": "Inventory"},
        headers=csrf_headers(admin_client),
    )
    assert created.status_code == 201
    target_id = created.json()["user"]["id"]
    assert (
        await admin_client.patch(
            f"/api/v1/staff/{target_id}",
            json={"display_name": "Inventory Renamed"},
            headers=csrf_headers(admin_client),
        )
    ).status_code == 200
    assert (
        await admin_client.post(
            f"/api/v1/staff/{target_id}/roles",
            json={"role": "doctor"},
            headers=csrf_headers(admin_client),
        )
    ).status_code == 200
    assert (
        await admin_client.delete(
            f"/api/v1/staff/{target_id}/roles/doctor", headers=csrf_headers(admin_client)
        )
    ).status_code == 204
    assert (
        await admin_client.patch(
            f"/api/v1/staff/{target_id}",
            json={"is_active": False},
            headers=csrf_headers(admin_client),
        )
    ).status_code == 200

    # --- MCP tool calls (the seven allowlisted tools) ----------------------
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.authorization.policy import Principal, permissions_for_roles
    from app.mcp.context import ToolContext
    from app.mcp.server import mcp_server

    session = AsyncSession(db_engine, expire_on_commit=False)
    try:
        ctx = ToolContext(
            principal=Principal(
                user_id=str(doctor.id),
                roles=doctor.roles,
                is_active=True,
                email=doctor.email,
                permissions=permissions_for_roles(doctor.roles),
            ),
            db=session,
            request_id="audit-inventory-mcp",
            ip="127.0.0.1",
        )
        patient_id = str(clinical_data.patient_id)
        calls = (
            ("search_authorized_patients", {"query": clinical_data.token, "limit": 25}),
            ("get_patient_summary", {"patient_id": patient_id}),
            ("get_patient_lab_results", {"patient_id": patient_id, "limit": 5}),
            ("get_recent_prescriptions", {"patient_id": patient_id, "limit": 5}),
            ("find_patients_by_lab", {"test_code": "K", "limit": 5}),
            ("find_patients_by_medication", {"medication_name": "Metformin", "limit": 5}),
            ("get_my_recent_patients", {"days": 30, "limit": 5}),
        )
        for name, arguments in calls:
            result = await mcp_server.call_tool(name, arguments, ctx)
            assert result.ok is True, f"{name}: {result.outcome}"
    finally:
        await session.close()

    # --- Every documented event class must exist -------------------------
    emitted = {row.action for row in await audit_rows()}
    missing = SECURITY_DOC_AUDIT_ACTIONS - emitted
    assert not missing, f"documented audit events not emitted: {sorted(missing)}"
