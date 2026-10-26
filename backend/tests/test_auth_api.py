"""Auth API tests (docs/API.md /auth endpoints): login, logout, /me, lockout.

Uniform failures (no account enumeration), rate limiting with Retry-After,
session rotation on login, server-side revocation on logout, and the exact user
payload shape.
"""
from __future__ import annotations

import re
import uuid

import pytest

from app.auth.ratelimit import login_rate_limiter
from conftest import SENTINEL_PASSWORD, audit_rows, csrf_headers  # noqa: F401

LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"
LOGOUT_URL = "/api/v1/auth/logout"
CORRELATION_ID = "test-correlation-0001"


async def _login(client, email: str, password: str, request_id: str | None = None):
    headers = {"X-Request-Id": request_id} if request_id else {}
    # Mirror the shipped frontend: always echo X-CSRF-Token when the cookie
    # exists (the first request bootstraps the cookie).
    headers.update(csrf_headers(client))
    return await client.post(LOGIN_URL, json={"email": email, "password": password}, headers=headers)


@pytest.mark.postgres
async def test_login_success_sets_cookies_and_returns_payload(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",), display_name="Demo Doctor Fixture")
    client = await make_client()
    response = await _login(client, account.email, account.password)

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"id", "email", "display_name", "roles", "permissions", "is_active"}
    assert payload["email"] == account.email
    assert payload["display_name"] == "Demo Doctor Fixture"
    assert payload["roles"] == ["doctor"]
    assert set(payload["permissions"]) == {"patient:read", "lab:read", "prescription:read"}
    assert payload["is_active"] is True
    assert uuid.UUID(payload["id"]) == account.id

    set_cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith("cap_session=") for c in set_cookies)
    assert any(c.startswith("csrf_token=") for c in set_cookies)
    assert SENTINEL_PASSWORD not in response.text


@pytest.mark.postgres
async def test_wrong_password_and_unknown_email_are_identical_401(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    wrong_password = await _login(
        client, account.email, "definitely-not-the-password", request_id=CORRELATION_ID
    )
    unknown_email = await _login(
        client, "nobody-here@example.com", account.password, request_id=CORRELATION_ID
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["message"] == "Invalid email or password."
    assert wrong_password.json()["error"]["code"] == "unauthenticated"
    assert "definitely-not-the-password" not in wrong_password.text
    assert "nobody-here@example.com" not in unknown_email.text


@pytest.mark.postgres
async def test_inactive_account_gets_uniform_401(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",), is_active=False)
    client = await make_client()
    response = await _login(client, account.email, account.password, request_id=CORRELATION_ID)
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "unauthenticated",
            "message": "Invalid email or password.",
            "request_id": CORRELATION_ID,
            "details": {},
        }
    }


@pytest.mark.postgres
async def test_login_validation_error_never_echoes_values(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    response = await client.post(LOGIN_URL, json={"email": account.email})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    fields = body["error"]["details"]["fields"]
    assert any(field["loc"] == "body.password" for field in fields)
    assert SENTINEL_PASSWORD not in response.text
    assert account.email not in response.text

    forbidden_field = await client.post(
        LOGIN_URL,
        json={"email": account.email, "password": SENTINEL_PASSWORD, "role": "not-a-role"},
        headers=csrf_headers(client),
    )
    assert forbidden_field.status_code == 400
    assert "not-a-role" not in forbidden_field.text


@pytest.mark.postgres
async def test_rate_limit_after_five_failures_with_retry_after(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    for attempt in range(5):
        response = await _login(client, account.email, f"wrong-{attempt}")
        assert response.status_code == 401, f"attempt {attempt + 1} must fail with 401"

    locked = await _login(client, account.email, account.password)
    assert locked.status_code == 429
    body = locked.json()
    assert body["error"]["code"] == "rate_limited"
    retry_after = int(locked.headers["Retry-After"])
    assert 1 <= retry_after <= 15 * 60
    # Even correct credentials are refused while locked out.
    assert "Sentinel" not in locked.text


@pytest.mark.postgres
async def test_successful_login_resets_failure_counter(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    for attempt in range(4):
        assert (await _login(client, account.email, f"wrong-{attempt}")).status_code == 401

    assert (await _login(client, account.email, account.password)).status_code == 200

    for attempt in range(4):
        assert (await _login(client, account.email, f"wrong-again-{attempt}")).status_code == 401
    # The counter restarted after the successful login: no 429 yet.
    assert (await _login(client, account.email, "wrong-5")).status_code == 401


@pytest.mark.postgres
async def test_coarse_per_ip_limit(make_client, make_user, monkeypatch) -> None:
    monkeypatch.setattr(login_rate_limiter, "max_failures_per_ip", 3)
    client = await make_client()
    for attempt in range(3):
        response = await _login(client, f"someone-{attempt}@example.com", "wrong")
        assert response.status_code == 401

    locked = await _login(client, "someone-else@example.com", "wrong")
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) >= 1


@pytest.mark.postgres
async def test_login_lockout_is_audited(db_engine, make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    for attempt in range(5):
        await _login(client, account.email, f"wrong-{attempt}")
    locked = await _login(client, account.email, account.password)
    request_id = locked.headers["X-Request-Id"]
    rows = await audit_rows(request_id=request_id, action="auth.login.failure")
    assert len(rows) == 1
    assert rows[0].outcome == "denied"


@pytest.mark.postgres
async def test_login_success_and_failure_audit_rows(make_client, make_user) -> None:
    account = await make_user(roles=("nurse",))
    client = await make_client()
    failure = await _login(client, account.email, "wrong-password")
    failure_rows = await audit_rows(
        request_id=failure.headers["X-Request-Id"], action="auth.login.failure"
    )
    assert len(failure_rows) == 1
    assert failure_rows[0].actor_user_id == account.id
    assert failure_rows[0].outcome == "denied"

    success = await _login(client, account.email, account.password)
    success_rows = await audit_rows(
        request_id=success.headers["X-Request-Id"], action="auth.login.success"
    )
    assert len(success_rows) == 1
    assert success_rows[0].actor_user_id == account.id
    assert success_rows[0].outcome == "success"
    assert success_rows[0].resource_type == "staff_user"


@pytest.mark.postgres
async def test_me_401_anonymous_and_200_authenticated(make_client, api_login, make_user) -> None:
    anonymous = await make_client()
    response = await anonymous.get(ME_URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"

    account = await make_user(roles=("nurse",))
    client = await api_login(account)
    response = await client.get(ME_URL)
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(account.id)
    assert payload["roles"] == ["nurse"]
    # Nurse is least privilege (owner decision, 2026-10-26): clinical reads
    # only — no staff:read, no audit:read, no admin-panel permissions.
    assert set(payload["permissions"]) == {
        "patient:read",
        "lab:read",
        "prescription:read",
    }


@pytest.mark.postgres
async def test_logout_revokes_session_and_clears_cookie(db_engine, api_login, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await api_login(account)
    raw_token = client.cookies.get("cap_session")
    assert raw_token

    response = await client.post(LOGOUT_URL, headers=csrf_headers(client))
    assert response.status_code == 204
    cleared = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("cap_session=")
    )
    assert re.search(r"max-age=0", cleared.lower())

    rows = await audit_rows(
        request_id=response.headers["X-Request-Id"], action="auth.logout"
    )
    assert len(rows) == 1
    assert rows[0].actor_user_id == account.id

    # The raw token is dead immediately after logout (server-side revocation).
    follow_up = await client.get(ME_URL)
    assert follow_up.status_code == 401


@pytest.mark.postgres
async def test_logout_requires_authentication(make_client) -> None:
    client = await make_client()
    await client.get(ME_URL)  # bootstrap csrf cookie
    response = await client.post(LOGOUT_URL, headers=csrf_headers(client))
    assert response.status_code == 401


@pytest.mark.postgres
async def test_session_cookie_rotates_on_login(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    first = await _login(client, account.email, account.password)
    assert first.status_code == 200
    old_token = client.cookies.get("cap_session")
    assert old_token

    second = await _login(client, account.email, account.password, request_id=CORRELATION_ID)
    assert second.status_code == 200
    new_token = client.cookies.get("cap_session")
    assert new_token != old_token

    # The pre-login session was revoked: the old token no longer resolves.
    stale_client = await make_client()
    stale_client.cookies.set("cap_session", old_token)
    stale = await stale_client.get(ME_URL)
    assert stale.status_code == 401


@pytest.mark.postgres
async def test_manual_cookie_injection_and_expiry_enforced_at_api_layer(
    db_engine, make_client, make_user
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.auth.sessions import create_session

    account = await make_user(roles=("doctor",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        valid_token, _ = await create_session(db, user_id=account.id)
        expired_token, _ = await create_session(db, user_id=account.id, ttl_hours=-1)
        await db.commit()

    # Positive control: a valid raw token authenticates at the API layer.
    valid_client = await make_client()
    valid_client.cookies.set("cap_session", valid_token)
    assert (await valid_client.get(ME_URL)).status_code == 200

    # An expired token is rejected server-side (401), even though it is well-formed.
    expired_client = await make_client()
    expired_client.cookies.set("cap_session", expired_token)
    assert (await expired_client.get(ME_URL)).status_code == 401
