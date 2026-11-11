"""Permission enforcement AT THE API LAYER (docs/API.md permission matrix).

Three guarantees, parameterized over every route under /api/v1:

1. `test_every_v1_route_is_declared_in_the_matrix` fails when a new endpoint is
   added without declaring its permission here (deny-by-default harness).
2. Anonymous requests get `401 unauthenticated` on every protected endpoint.
3. Authenticated requests get `2xx` exactly when the matrix grants the
   permission to the caller's role, and `403 forbidden` otherwise.

Explicit role scenarios from PLAN.md Phase 5 acceptance criteria are asserted
in named tests at the bottom of this file.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.authorization.policy import has_permission
from conftest import ALL_ROLE_KEYS, csrf_headers  # noqa: F401

PUBLIC = "public"
AUTHENTICATED = "authenticated"


@dataclass(frozen=True)
class EndpointCase:
    method: str
    template: str
    permission: str  # permission key, PUBLIC, or AUTHENTICATED
    success_status: int
    body_kind: str = "none"  # none | staff_create | staff_update | role_assign
    needs_patient: bool = False
    needs_encounter: bool = False
    needs_target_user: bool = False
    needs_conversation: bool = False
    target_roles: tuple[str, ...] = ()
    role_path: str = "doctor"
    # Appointment probes need role-aware scoping: an Admin must name a doctor,
    # while a Doctor may never name another doctor (the route answers 403).
    query_kind: str | None = None


MATRIX: tuple[EndpointCase, ...] = (
    EndpointCase("POST", "/api/v1/auth/login", PUBLIC, 200, body_kind="login"),
    EndpointCase("POST", "/api/v1/auth/logout", AUTHENTICATED, 204),
    EndpointCase("GET", "/api/v1/auth/me", AUTHENTICATED, 200),
    EndpointCase("GET", "/api/v1/patients", "patient:read", 200),
    EndpointCase(
        "POST", "/api/v1/patients", "patient:write", 201, body_kind="patient_create"
    ),
    EndpointCase(
        "DELETE",
        "/api/v1/patients/{patient_id}",
        "patient:delete",
        204,
        needs_patient=True,
    ),
    EndpointCase("GET", "/api/v1/patients/{patient_id}", "patient:read", 200, needs_patient=True),
    EndpointCase(
        "GET", "/api/v1/patients/{patient_id}/summary", "patient:read", 200, needs_patient=True
    ),
    EndpointCase(
        "GET",
        "/api/v1/patients/{patient_id}/encounters",
        "patient:read",
        200,
        needs_patient=True,
    ),
    EndpointCase(
        "GET", "/api/v1/patients/{patient_id}/labs", "lab:read", 200, needs_patient=True
    ),
    EndpointCase(
        "GET",
        "/api/v1/patients/{patient_id}/labs/trends",
        "lab:read",
        200,
        needs_patient=True,
    ),
    EndpointCase(
        "GET",
        "/api/v1/patients/{patient_id}/prescriptions",
        "prescription:read",
        200,
        needs_patient=True,
    ),
    EndpointCase(
        "GET", "/api/v1/encounters/{encounter_id}", "patient:read", 200, needs_encounter=True
    ),
    EndpointCase("GET", "/api/v1/staff", "staff:read", 200),
    EndpointCase("POST", "/api/v1/staff", "staff:manage", 201, body_kind="staff_create"),
    EndpointCase(
        "PATCH",
        "/api/v1/staff/{user_id}",
        "staff:manage",
        200,
        body_kind="staff_update",
        needs_target_user=True,
    ),
    EndpointCase(
        "POST",
        "/api/v1/staff/{user_id}/roles",
        "role:assign",
        200,
        body_kind="role_assign",
        needs_target_user=True,
        target_roles=("nurse",),
    ),
    EndpointCase(
        "DELETE",
        "/api/v1/staff/{user_id}/roles/{role}",
        "role:assign",
        204,
        needs_target_user=True,
        target_roles=("doctor", "nurse"),
        role_path="doctor",
    ),
    EndpointCase("GET", "/api/v1/audit", "audit:read", 200),
    EndpointCase("GET", "/api/v1/admin/logs", "logs:read", 200),
    EndpointCase("GET", "/api/v1/admin/logs/stream", "logs:read", 200),
    EndpointCase("GET", "/api/v1/messages", "message:read", 200),
    EndpointCase("GET", "/api/v1/messages/counts", "message:read", 200),
    EndpointCase("GET", "/api/v1/messages/recipients", "message:read", 200),
    EndpointCase(
        "POST",
        "/api/v1/messages",
        "message:write",
        201,
        body_kind="message_send",
    ),
    EndpointCase("POST", "/api/v1/messages/{message_id}/read", "message:read", 404),
    # Appointments: doctors are scoped to their own calendar server-side; the
    # write probes use unknown ids, so an authorized caller reaches the route
    # and receives the concealment 404 while a forbidden caller gets 403.
    EndpointCase("GET", "/api/v1/appointments/config", "appointment:read", 200),
    EndpointCase(
        "GET",
        "/api/v1/appointments",
        "appointment:read",
        200,
        query_kind="appointments_list",
    ),
    EndpointCase(
        "POST",
        "/api/v1/appointments",
        "appointment:write",
        404,
        body_kind="appointment_create",
    ),
    # AI interpretation only proposes. With the LLM disabled in tests the
    # authorized path stops at the dependency gate (503); the probe proves the
    # permission split without invoking a model.
    EndpointCase(
        "POST",
        "/api/v1/appointments/interpret",
        "appointment:write",
        503,
        body_kind="appointment_interpret",
        query_kind="interpret",
    ),
    EndpointCase(
        "GET", "/api/v1/appointments/{appointment_id}", "appointment:read", 404
    ),
    EndpointCase(
        "PATCH",
        "/api/v1/appointments/{appointment_id}",
        "appointment:write",
        404,
        body_kind="appointment_update",
    ),
    EndpointCase(
        "POST",
        "/api/v1/appointments/{appointment_id}/cancel",
        "appointment:write",
        404,
    ),
    # Admin-only calendar visibility change. The route itself requires
    # appointment:write + the Admin role; analytics:read is used here as the
    # existing Admin-only matrix marker so Doctor/Nurse probes expect 403.
    EndpointCase(
        "POST",
        "/api/v1/appointments/{appointment_id}/remove-from-calendar",
        "analytics:read",
        404,
    ),
    EndpointCase(
        "GET",
        "/api/v1/appointments/availability",
        "appointment:read",
        200,
        query_kind="doctor_scoped",
    ),
    EndpointCase(
        "PUT",
        "/api/v1/appointments/availability",
        "appointment:manage_availability",
        200,
        body_kind="availability_write",
        query_kind="doctor_scoped",
    ),
    EndpointCase(
        "GET",
        "/api/v1/appointments/slots",
        "appointment:read",
        200,
        query_kind="slots",
    ),
    # Voice scheduling is part of the appointment-write surface. The probe
    # uploads an empty file: authorized callers reach the bounded audio
    # validation (400), while a forbidden role never reads the body (403).
    EndpointCase(
        "POST",
        "/api/v1/voice/transcriptions",
        "appointment:write",
        400,
        body_kind="voice_upload",
    ),
    # Wave U (admin analytics): one bounded read-only overview, Admin only.
    EndpointCase("GET", "/api/v1/analytics/summary", "analytics:read", 200),
    EndpointCase(
        "POST",
        "/api/v1/ai/ask",
        "patient:read",
        200,
        body_kind="ai_ask",
        needs_patient=True,
    ),
    # Conversation history (wave V): per-user private, always scoped to the
    # caller server-side. The delete-by-id probe below targets an id the caller
    # does not own, so its authorized status is the concealment 404 - own-row
    # deletion answering 204 is covered in test_ai_history.
    EndpointCase("GET", "/api/v1/ai/conversations", "patient:read", 200),
    EndpointCase("DELETE", "/api/v1/ai/conversations", "patient:read", 204),
    EndpointCase(
        "DELETE",
        "/api/v1/ai/conversations/{conversation_id}",
        "patient:read",
        404,
        needs_conversation=True,
    ),
)


async def _build_request(
    case: EndpointCase, *, clinical_data, make_user, actor_role: str | None = None
):
    path = case.template
    if case.needs_patient:
        path = path.format(patient_id=clinical_data.patient_id)
    if case.needs_encounter:
        path = path.format(encounter_id=clinical_data.encounter_id)
    if case.needs_target_user:
        target = await make_user(roles=case.target_roles)
        path = path.format(user_id=target.id, role=case.role_path)
    if case.needs_conversation:
        path = path.format(conversation_id=uuid.uuid4())
    if "{appointment_id}" in path:
        path = path.format(appointment_id=uuid.uuid4())
    if "{message_id}" in path:
        path = path.format(message_id=uuid.uuid4())

    now = datetime.now(timezone.utc)
    params: dict[str, str] | None = None
    if case.query_kind == "appointments_list":
        params = {
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now + timedelta(days=30)).isoformat(),
        }
    elif case.query_kind in {"doctor_scoped", "slots", "interpret"}:
        params = {}
        if case.query_kind == "slots":
            params["date"] = (now + timedelta(days=2)).date().isoformat()
        if actor_role in {"clinical_admin", "nurse"}:
            # Admin and read-only nurse may act/read across doctors and must
            # name one for scoped availability/slot probes; a Doctor probe
            # deliberately omits doctor_id so the route scopes to the caller.
            target = await make_user(roles=("doctor",))
            params["doctor_id"] = str(target.id)

    body: dict | None = None
    if case.body_kind == "login":
        body = {"email": f"probe-{uuid.uuid4().hex[:8]}@example.com", "password": "probe-password"}
    elif case.body_kind == "staff_create":
        body = {
            "email": f"created-{uuid.uuid4().hex[:12]}@example.com",
            "display_name": "Matrix Created User",
        }
    elif case.body_kind == "staff_update":
        body = {"display_name": "Matrix Updated Name"}
    elif case.body_kind == "role_assign":
        body = {"role": "doctor"}
    elif case.body_kind == "ai_ask":
        body = {
            "patient_id": str(clinical_data.patient_id),
            "question": "What are the latest lab results for this synthetic patient?",
        }
    elif case.body_kind == "message_send":
        target = await make_user(roles=("doctor",))
        body = {
            "recipient_id": str(target.id),
            "subject": "Matrix message",
            "body": "Matrix-only internal message.",
        }
    elif case.body_kind == "patient_create":
        body = {
            "given_name": "Probe" + uuid.uuid4().hex[:8],
            "family_name": "Patient",
            "sex": "unknown",
        }
    elif case.body_kind == "appointment_create":
        # Unknown patient: an authorized caller reaches the concealment 404,
        # a forbidden caller is stopped by the permission dependency.
        body = {
            "patient_id": str(uuid.uuid4()),
            "start_at": (now + timedelta(days=7)).isoformat(),
            "duration_minutes": 30,
        }
        if actor_role == "clinical_admin":
            body["doctor_id"] = str(uuid.uuid4())
    elif case.body_kind == "appointment_interpret":
        body = {"text": "Book an appointment tomorrow at 10:00 for the patient."}
    elif case.body_kind == "appointment_update":
        body = {}
    elif case.body_kind == "availability_write":
        body = {
            "default_duration_minutes": 30,
            "slot_increment_minutes": 30,
            "working_hours": [],
            "time_off": [],
        }
    return path, body, params


async def _call(
    client,
    case: EndpointCase,
    path: str,
    body: dict | None,
    params: dict[str, str] | None = None,
):
    kwargs = {"headers": csrf_headers(client)}
    if case.body_kind == "voice_upload":
        kwargs["data"] = {"language": "en"}
        kwargs["files"] = {"file": ("empty.webm", b"", "audio/webm")}
    elif body is not None:
        kwargs["json"] = body
    if params is not None:
        kwargs["params"] = params
    return await client.request(case.method, path, **kwargs)


def _iter_route_paths(routes, prefix: str = ""):
    """Yield (method, full_path) for every route, following nested inclusions.

    FastAPI >= 0.162 nests included routers inside wrapper objects that carry
    the mount prefix on `include_context`; older layouts stay flat. The
    traversal normalises both so the guard below keeps working either way.
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None and getattr(included, "routes", None) is not None:
            ctx = getattr(route, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            yield from _iter_route_paths(included.routes, sub_prefix)
            continue
        sub = getattr(route, "routes", None)
        if sub:
            yield from _iter_route_paths(sub, prefix)
            continue
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in getattr(route, "methods", set()) or set():
            if method == "HEAD":
                continue
            yield method, prefix + path


def test_every_v1_route_is_declared_in_the_matrix(app) -> None:
    declared = {(case.method, case.template) for case in MATRIX}
    registered = {
        (method, path)
        for method, path in _iter_route_paths(app.routes)
        if path.startswith("/api/v1")
    }
    assert registered == declared, (
        "every /api/v1 route must be declared in MATRIX with its permission "
        f"(missing: {sorted(registered - declared)}, stale: {sorted(declared - registered)})"
    )


@pytest.mark.postgres
@pytest.mark.parametrize("case", MATRIX, ids=lambda c: f"{c.method}:{c.template}")
async def test_anonymous_requests_get_401(case, clinical_data, make_user, make_client) -> None:
    if case.permission == PUBLIC:
        pytest.skip("public endpoint: covered in test_auth_api")
    if case.template.endswith("/stream"):
        pytest.skip("SSE endpoint covered by dedicated system-log tests")
    client = await make_client()
    path, body, params = await _build_request(
        case, clinical_data=clinical_data, make_user=make_user
    )
    response = await _call(client, case, path, body, params)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.postgres
@pytest.mark.parametrize(
    "case",
    [c for c in MATRIX if c.permission not in (PUBLIC, AUTHENTICATED)],
    ids=lambda c: f"{c.method}:{c.template}",
)
@pytest.mark.parametrize("role", ALL_ROLE_KEYS)
async def test_role_permission_matrix_at_api_layer(
    case, role, clinical_data, make_user, auth_client
) -> None:
    if case.template.endswith("/stream"):
        pytest.skip("SSE endpoint covered by dedicated system-log tests")
    account = await make_user(roles=(role,))
    client = await auth_client(account)
    path, body, params = await _build_request(
        case, clinical_data=clinical_data, make_user=make_user, actor_role=role
    )
    response = await _call(client, case, path, body, params)

    if has_permission([role], case.permission):
        assert response.status_code == case.success_status, response.text
    else:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.postgres
@pytest.mark.parametrize(
    "case",
    [c for c in MATRIX if c.permission == AUTHENTICATED],
    ids=lambda c: f"{c.method}:{c.template}",
)
@pytest.mark.parametrize("role", ALL_ROLE_KEYS)
async def test_authenticated_only_endpoints_accept_every_role(
    case, role, clinical_data, make_user, auth_client
) -> None:
    account = await make_user(roles=(role,))
    client = await auth_client(account)
    path, body, params = await _build_request(
        case, clinical_data=clinical_data, make_user=make_user, actor_role=role
    )
    response = await _call(client, case, path, body, params)
    assert response.status_code == case.success_status, response.text


# --- named PLAN.md Phase 5 acceptance scenarios ----------------------------


@pytest.mark.postgres
async def test_doctor_cannot_access_staff_endpoints(auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    assert (await client.get("/api/v1/staff")).status_code == 403
    create = await client.post(
        "/api/v1/staff",
        json={"email": f"blocked-{uuid.uuid4().hex[:8]}@example.com", "display_name": "Blocked"},
        headers=csrf_headers(client),
    )
    assert create.status_code == 403


@pytest.mark.postgres
async def test_admin_has_full_access_including_clinical_reads(
    auth_client, make_user, clinical_data
) -> None:
    """Behavior change (owner decision, 2026-10-26): the Admin role gained
    clinical reads (and analytics:read) — it now holds every permission."""
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    for path in (
        "/api/v1/patients",
        f"/api/v1/patients/{clinical_data.patient_id}",
        f"/api/v1/patients/{clinical_data.patient_id}/summary",
        f"/api/v1/patients/{clinical_data.patient_id}/labs",
        f"/api/v1/patients/{clinical_data.patient_id}/prescriptions",
        f"/api/v1/encounters/{clinical_data.encounter_id}",
        "/api/v1/staff",
        "/api/v1/audit",
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert "analytics:read" in me.json()["permissions"]


@pytest.mark.postgres
async def test_nurse_reads_clinical_data_but_has_no_admin_panel_access(
    auth_client, make_user, clinical_data
) -> None:
    """Behavior change (owner decision, 2026-10-26): nurse is least privilege —
    clinical reads yes, staff/audit administration no (403)."""
    account = await make_user(roles=("nurse",))
    client = await auth_client(account)
    assert (await client.get("/api/v1/patients")).status_code == 200
    assert (await client.get(f"/api/v1/patients/{clinical_data.patient_id}/labs")).status_code == 200

    # Nurse now has read-only clinic appointments: list/config are readable,
    # every write/availability route remains forbidden.
    now = datetime.now(timezone.utc)
    appointments = await client.get(
        "/api/v1/appointments",
        params={
            "start": now.isoformat(),
            "end": (now + timedelta(days=7)).isoformat(),
        },
    )
    assert appointments.status_code == 200, appointments.text
    assert (await client.get("/api/v1/appointments/config")).status_code == 200
    assert (
        await client.post(
            "/api/v1/appointments",
            json={
                "patient_id": str(clinical_data.patient_id),
                "start_at": (now + timedelta(days=2)).isoformat(),
                "duration_minutes": 30,
            },
            headers=csrf_headers(client),
        )
    ).status_code == 403
    assert (
        await client.put(
            "/api/v1/appointments/availability",
            json={
                "default_duration_minutes": 30,
                "slot_increment_minutes": 30,
                "working_hours": [],
                "time_off": [],
            },
            headers=csrf_headers(client),
        )
    ).status_code == 403

    assert (await client.get("/api/v1/staff")).status_code == 403  # no staff:read
    assert (await client.get("/api/v1/audit")).status_code == 403  # no audit:read

    assert (
        await client.post(
            "/api/v1/staff",
            json={"email": f"nurse-blocked-{uuid.uuid4().hex[:8]}@example.com", "display_name": "X"},
            headers=csrf_headers(client),
        )
    ).status_code == 403
    target = await make_user(roles=("doctor",))
    assert (
        await client.post(
            f"/api/v1/staff/{target.id}/roles",
            json={"role": "clinical_admin"},
            headers=csrf_headers(client),
        )
    ).status_code == 403


@pytest.mark.postgres
async def test_self_role_assignment_is_refused_even_when_it_adds_nothing(
    auth_client, make_user
) -> None:
    # Self role assignment is refused unconditionally (targeting self -> 403),
    # including a grant that would add no new permissions at all.
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    response = await client.post(
        f"/api/v1/staff/{account.id}/roles",
        json={"role": "nurse"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Self role assignment is not permitted"
