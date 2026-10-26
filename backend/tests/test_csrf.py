"""CSRF double-submit tests (docs/API.md / app.auth.csrf policy).

Covers: GET issues the cookie, bootstrap (no cookie -> non-GET allowed), the
403 on missing/mismatched X-CSRF-Token, matching-header success, cookie
attributes, and audit of CSRF rejections.
"""
from __future__ import annotations

import pytest

from conftest import SENTINEL_PASSWORD, audit_rows, csrf_headers  # noqa: F401


@pytest.mark.postgres
async def test_get_issues_csrf_cookie_on_anonymous_request(make_client) -> None:
    client = await make_client()
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert client.cookies.get("csrf_token"), "GET must issue the csrf_token cookie"
    cookie = next(c for c in response.headers.get_list("set-cookie") if c.startswith("csrf_token="))
    assert "httponly" not in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert "path=/" in cookie.lower()


@pytest.mark.postgres
async def test_bootstrap_non_get_without_cookie_is_allowed(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    # No csrf cookie exists at all: the request cannot be a CSRF attack yet.
    response = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": account.password}
    )
    assert response.status_code == 200
    assert client.cookies.get("csrf_token"), "bootstrap response must issue the cookie"


@pytest.mark.postgres
async def test_post_without_header_but_with_cookie_is_403(api_login, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await api_login(account)
    assert client.cookies.get("csrf_token")
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["message"] == "CSRF validation failed"


@pytest.mark.postgres
async def test_post_with_mismatched_header_is_403(api_login, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await api_login(account)
    response = await client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": "not-the-cookie-value"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "CSRF validation failed"


@pytest.mark.postgres
async def test_post_with_matching_header_succeeds(api_login, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await api_login(account)
    response = await client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 204


@pytest.mark.postgres
async def test_login_is_covered_by_csrf_once_the_cookie_exists(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    bootstrap = await client.get("/api/v1/auth/me")
    assert bootstrap.status_code == 401
    assert client.cookies.get("csrf_token")

    rejected = await client.post(
        "/api/v1/auth/login",
        json={"email": account.email, "password": SENTINEL_PASSWORD},
        headers={"X-CSRF-Token": "forged"},
    )
    assert rejected.status_code == 403

    accepted = await client.post(
        "/api/v1/auth/login",
        json={"email": account.email, "password": account.password},
        headers=csrf_headers(client),
    )
    assert accepted.status_code == 200


@pytest.mark.postgres
async def test_session_cookie_is_httponly(make_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await make_client()
    response = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": account.password}
    )
    assert response.status_code == 200
    session_cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("cap_session=")
    )
    assert "httponly" in session_cookie.lower()
    assert "samesite=lax" in session_cookie.lower()
    assert "path=/" in session_cookie.lower()
    # Development over plain HTTP: Secure is omitted (settings-driven).
    assert "secure" not in session_cookie.lower()


@pytest.mark.postgres
async def test_csrf_rejection_is_audited(db_engine, make_client) -> None:
    client = await make_client()
    await client.get("/api/v1/auth/me")  # bootstrap the cookie
    response = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"})
    assert response.status_code == 403
    request_id = response.headers["X-Request-Id"]
    rows = await audit_rows(request_id=request_id, action="authz.denied")
    assert len(rows) == 1
    assert rows[0].outcome == "denied"
    assert rows[0].resource_type == "csrf"


@pytest.mark.postgres
async def test_bootstrap_issues_cookie_even_on_non_api_get(make_client) -> None:
    client = await make_client()
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert client.cookies.get("csrf_token")


@pytest.mark.postgres
async def test_secure_cookie_flag_is_settings_driven(make_user) -> None:
    import httpx

    from app.config import load_settings
    from app.main import create_app

    account = await make_user(roles=("doctor",))

    async def _login_cookie_header(settings) -> str:
        application = create_app(settings)
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": account.email, "password": account.password},
            )
            assert response.status_code == 200, response.text
            return next(
                c for c in response.headers.get_list("set-cookie") if c.startswith("cap_session=")
            )

    base = load_settings(env_file=None)

    # APP_ENV=production: Secure is on.
    production = await _login_cookie_header(base.model_copy(update={"app_env": "production"}))
    assert "secure" in production.lower()

    # Explicit override wins over the environment.
    forced = await _login_cookie_header(base.model_copy(update={"session_cookie_secure": True}))
    assert "secure" in forced.lower()
    forced_off = await _login_cookie_header(
        base.model_copy(update={"app_env": "production", "session_cookie_secure": False})
    )
    assert "secure" not in forced_off.lower()
