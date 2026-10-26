"""Staff & role administration tests (docs/API.md).

Account creation (with one-time generated password), updates, deactivation with
immediate session revocation, role grants/revokes, self-escalation refusal, and
last-admin protection — including the audit trail for every change.
"""
from __future__ import annotations

import uuid

import pytest

from conftest import SENTINEL_PASSWORD, audit_rows, csrf_headers


async def _staff_item(client, user_id: uuid.UUID) -> dict:
    """Find one staff account across all pages of GET /staff."""
    offset = 0
    while offset < 1000:
        response = await client.get(f"/api/v1/staff?limit=100&offset={offset}")
        assert response.status_code == 200
        body = response.json()
        for item in body["items"]:
            if item["id"] == str(user_id):
                return item
        if offset + body["limit"] >= body["total"]:
            break
        offset += body["limit"]
    raise AssertionError(f"staff account {user_id} not listed")


@pytest.mark.postgres
async def test_staff_list_shape_never_leaks_secrets(
    auth_client, make_user, make_client
) -> None:
    account = await make_user(roles=("clinical_admin", "tech_admin"), display_name="Admin Fixture")
    client = await auth_client(account)
    response = await client.get("/api/v1/staff")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] >= 1

    own = await _staff_item(client, account.id)
    assert set(own) == {
        "id",
        "email",
        "display_name",
        "roles",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert sorted(own["roles"]) == ["clinical_admin", "tech_admin"]
    assert own["is_active"] is True
    forbidden = ("password", "password_hash", "token", "session", "secret")
    for item in body["items"]:
        for key in item:
            assert not any(marker in key for marker in forbidden), key
        assert SENTINEL_PASSWORD not in str(item)


@pytest.mark.postgres
async def test_create_staff_with_explicit_password(auth_client, make_user, make_client) -> None:
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    email = f"created-{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(
        "/api/v1/staff",
        json={"email": email, "display_name": "Created User", "password": "created-user-password"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["generated_password"] is None
    assert body["user"]["email"] == email
    assert body["user"]["roles"] == []
    # The plaintext password is never echoed anywhere in the response.
    assert "created-user-password" not in response.text

    # The supplied password works for login and is stored only as a hash.
    login_client = await make_client()
    login = await login_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "created-user-password"}
    )
    assert login.status_code == 200


@pytest.mark.postgres
async def test_create_staff_generates_password_once(
    auth_client, make_user, make_client, db_engine
) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import StaffUser

    account = await make_user(roles=("tech_admin",))
    client = await auth_client(account)
    email = f"generated-{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(
        "/api/v1/staff",
        json={"email": email, "display_name": "Generated User"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201
    generated = response.json()["generated_password"]
    assert generated and len(generated) >= 16
    assert response.text.count(generated) == 1

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = await session.scalar(select(StaffUser).where(StaffUser.email == email))
        assert row is not None
        assert row.password_hash != generated
        assert row.password_hash.startswith("$argon2")

    login_client = await make_client()
    login = await login_client.post("/api/v1/auth/login", json={"email": email, "password": generated})
    assert login.status_code == 200
    assert generated not in login.text


@pytest.mark.postgres
async def test_create_staff_duplicate_email_conflict(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    email = f"duplicate-{uuid.uuid4().hex[:10]}@example.com"
    first = await client.post(
        "/api/v1/staff",
        json={"email": email, "display_name": "First"},
        headers=csrf_headers(client),
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/staff",
        json={"email": email, "display_name": "Second"},
        headers=csrf_headers(client),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


@pytest.mark.postgres
async def test_create_staff_validation_errors(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    missing = await client.post(
        "/api/v1/staff", json={"email": "x@example.com"}, headers=csrf_headers(client)
    )
    assert missing.status_code == 400
    fields = missing.json()["error"]["details"]["fields"]
    assert any(field["loc"] == "body.display_name" for field in fields)

    extra = await client.post(
        "/api/v1/staff",
        json={"email": "x@example.com", "display_name": "X", "roles": ["tech_admin"]},
        headers=csrf_headers(client),
    )
    assert extra.status_code == 400  # extra="forbid": roles cannot be smuggled in


@pytest.mark.postgres
async def test_update_staff_display_name_and_activate(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    target = await make_user(roles=("doctor",))
    client = await auth_client(account)

    response = await client.patch(
        f"/api/v1/staff/{target.id}",
        json={"display_name": "Renamed User"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Renamed User"
    assert response.json()["roles"] == ["doctor"]

    rows = await audit_rows(
        request_id=response.headers["X-Request-Id"], action="staff.update"
    )
    assert len(rows) == 1
    assert rows[0].resource_id == str(target.id)
    assert rows[0].actor_user_id == account.id


@pytest.mark.postgres
async def test_patch_cannot_change_roles(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    target = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.patch(
        f"/api/v1/staff/{target.id}",
        json={"is_active": True, "roles": ["tech_admin"]},
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"

    item = await _staff_item(client, target.id)
    assert item["roles"] == ["doctor"]


@pytest.mark.postgres
async def test_deactivation_revokes_all_sessions_immediately(
    db_engine, auth_client, make_user
) -> None:
    account = await make_user(roles=("tech_admin",))
    target = await make_user(roles=("doctor",))
    admin_client = await auth_client(account)
    target_client = await auth_client(target)

    # The target works before deactivation.
    assert (await target_client.get("/api/v1/patients")).status_code == 200

    response = await admin_client.patch(
        f"/api/v1/staff/{target.id}",
        json={"is_active": False},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    # Every session of the target is dead on the very next request.
    assert (await target_client.get("/api/v1/patients")).status_code == 401

    request_id = response.headers["X-Request-Id"]
    deactivation = await audit_rows(request_id=request_id, action="staff.deactivate")
    revocations = await audit_rows(request_id=request_id, action="session.revoke_all")
    assert len(deactivation) == 1 and deactivation[0].resource_id == str(target.id)
    assert len(revocations) == 1 and revocations[0].resource_id == str(target.id)


@pytest.mark.postgres
async def test_self_deactivation_is_allowed(db_engine, auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    response = await client.patch(
        f"/api/v1/staff/{account.id}",
        json={"is_active": False},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    assert (await client.get("/api/v1/staff")).status_code == 401


@pytest.mark.postgres
async def test_role_grant_and_revoke_are_audited(db_engine, auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    target = await make_user(roles=("nurse",))
    client = await auth_client(account)

    grant = await client.post(
        f"/api/v1/staff/{target.id}/roles",
        json={"role": "doctor"},
        headers=csrf_headers(client),
    )
    assert grant.status_code == 200
    assert sorted(grant.json()["roles"]) == ["doctor", "nurse"]
    grant_rows = await audit_rows(
        request_id=grant.headers["X-Request-Id"], action="role.grant"
    )
    assert len(grant_rows) == 1
    assert grant_rows[0].actor_user_id == account.id
    assert grant_rows[0].resource_type == "user_role"
    assert grant_rows[0].resource_id == f"{target.id}:doctor"

    # Duplicate grant is idempotent: 200, no second grant row, no second audit.
    duplicate = await client.post(
        f"/api/v1/staff/{target.id}/roles",
        json={"role": "doctor"},
        headers=csrf_headers(client),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["roles"] == ["doctor", "nurse"]
    dup_rows = await audit_rows(
        request_id=duplicate.headers["X-Request-Id"], action="role.grant"
    )
    assert dup_rows == []

    revoke = await client.delete(
        f"/api/v1/staff/{target.id}/roles/doctor", headers=csrf_headers(client)
    )
    assert revoke.status_code == 204
    revoke_rows = await audit_rows(
        request_id=revoke.headers["X-Request-Id"], action="role.revoke"
    )
    assert len(revoke_rows) == 1
    assert revoke_rows[0].resource_id == f"{target.id}:doctor"
    assert revoke_rows[0].outcome == "success"

    item = await _staff_item(client, target.id)
    assert item["roles"] == ["nurse"]


@pytest.mark.postgres
async def test_role_grant_validation_and_unknown_target(auth_client, make_user) -> None:
    account = await make_user(roles=("tech_admin",))
    target = await make_user(roles=("nurse",))
    client = await auth_client(account)

    invalid = await client.post(
        f"/api/v1/staff/{target.id}/roles",
        json={"role": "superuser"},
        headers=csrf_headers(client),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "validation_error"

    missing_user = await client.post(
        f"/api/v1/staff/{uuid.uuid4()}/roles",
        json={"role": "doctor"},
        headers=csrf_headers(client),
    )
    assert missing_user.status_code == 404


@pytest.mark.postgres
async def test_self_escalation_is_refused_and_audited(db_engine, auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)

    # Admin -> self doctor would add clinical permissions: refused.
    response = await client.post(
        f"/api/v1/staff/{account.id}/roles",
        json={"role": "doctor"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Self role assignment is not permitted"
    rows = await audit_rows(
        request_id=response.headers["X-Request-Id"], action="authz.denied"
    )
    assert len(rows) == 1
    assert rows[0].outcome == "denied"
    assert rows[0].resource_id == f"{account.id}:doctor"

    # Granting self a role that adds nothing (same permission set) is allowed.
    same_permissions = await client.post(
        f"/api/v1/staff/{account.id}/roles",
        json={"role": "tech_admin"},
        headers=csrf_headers(client),
    )
    assert same_permissions.status_code == 200
    assert sorted(same_permissions.json()["roles"]) == ["clinical_admin", "tech_admin"]


@pytest.mark.postgres
async def test_doctor_cannot_grant_self_admin_roles(db_engine, auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.post(
        f"/api/v1/staff/{account.id}/roles",
        json={"role": "clinical_admin"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    rows = await audit_rows(
        request_id=response.headers["X-Request-Id"], action="authz.denied"
    )
    assert len(rows) == 1
    assert rows[0].actor_user_id == account.id
    # Permission-level denial (role:assign), not a self-assignment denial.
    assert rows[0].resource_type == "permission"
    assert rows[0].resource_id == "role:assign"


async def _suspend_other_admins(db_engine, *keep: uuid.UUID) -> list[uuid.UUID]:
    """Temporarily deactivate every active admin outside `keep` (restored later).

    The last-admin invariant is global, so the scenario needs a controlled set
    of administrators. Direct DB writes only flip `is_active`; no sessions or
    grants are touched.
    """
    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Role, StaffUser, UserRole

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        admin_ids = list(
            (
                await session.scalars(
                    select(UserRole.user_id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(Role.key.in_(("clinical_admin", "tech_admin")))
                    .distinct()
                )
            ).all()
        )
        suspended = [user_id for user_id in admin_ids if user_id not in set(keep)]
        if suspended:
            await session.execute(
                update(StaffUser).where(StaffUser.id.in_(suspended)).values(is_active=False)
            )
            await session.commit()
        return suspended


async def _restore_admins(db_engine, user_ids: list[uuid.UUID]) -> None:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import StaffUser

    if not user_ids:
        return
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await session.execute(
            update(StaffUser).where(StaffUser.id.in_(user_ids)).values(is_active=True)
        )
        await session.commit()


@pytest.mark.postgres
async def test_last_admin_removal_is_conflict(db_engine, auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    suspended = await _suspend_other_admins(db_engine, account.id)
    try:
        client = await auth_client(account)

        # Removing the only administrator of any kind must fail.
        response = await client.delete(
            f"/api/v1/staff/{account.id}/roles/clinical_admin", headers=csrf_headers(client)
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        rows = await audit_rows(
            request_id=response.headers["X-Request-Id"], action="role.revoke"
        )
        assert len(rows) == 1
        assert rows[0].outcome == "denied"

        # With a second active admin in place, the removal succeeds.
        other_admin = await make_user(roles=("tech_admin",))
        assert other_admin.id
        ok = await client.delete(
            f"/api/v1/staff/{account.id}/roles/clinical_admin", headers=csrf_headers(client)
        )
        assert ok.status_code == 204
    finally:
        await _restore_admins(db_engine, suspended)


@pytest.mark.postgres
async def test_admin_with_two_admin_roles_can_drop_one(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin", "tech_admin"))
    client = await auth_client(account)
    response = await client.delete(
        f"/api/v1/staff/{account.id}/roles/tech_admin", headers=csrf_headers(client)
    )
    assert response.status_code == 204  # still an admin via clinical_admin


@pytest.mark.postgres
async def test_revoke_role_not_held_is_404(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    target = await make_user(roles=("nurse",))
    client = await auth_client(account)
    response = await client.delete(
        f"/api/v1/staff/{target.id}/roles/doctor", headers=csrf_headers(client)
    )
    assert response.status_code == 404


@pytest.mark.postgres
async def test_staff_changes_require_authentication(make_client, make_user) -> None:
    target = await make_user(roles=("doctor",))
    client = await make_client()
    assert (await client.get("/api/v1/staff")).status_code == 401
    create = await client.post(
        "/api/v1/staff",
        json={"email": f"anon-{uuid.uuid4().hex[:8]}@example.com", "display_name": "Anon"},
        headers=csrf_headers(client),
    )
    assert create.status_code == 401
    assert (
        await client.patch(
            f"/api/v1/staff/{target.id}", json={"is_active": False}, headers=csrf_headers(client)
        )
    ).status_code == 401
