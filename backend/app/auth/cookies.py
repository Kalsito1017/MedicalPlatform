"""Session and CSRF cookie handling (docs/SECURITY.md, docs/API.md).

Cookie attributes:

- Session cookie (`SESSION_COOKIE_NAME`, default `cap_session`): `HttpOnly`
  (JavaScript cannot read it), `SameSite=Lax`, `Path=/`, and `Secure` when
  `SESSION_COOKIE_SECURE=true` or — the default (`null`) — when `APP_ENV` is
  `production` or the request itself arrived over HTTPS. On plain-HTTP local
  development `Secure` is omitted so the cookie works on `http://localhost`.
- CSRF cookie (`csrf_token`): same attributes minus `HttpOnly` — the browser
  must be able to read it for the double-submit check.

Values are opaque secrets; neither cookie is ever logged.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from app.config import Settings

SESSION_COOKIE_PATH = "/"
SAMESITE = "lax"


def cookie_secure(request: Request, settings: Settings) -> bool:
    """Resolve the `Secure` attribute (settings-driven; see module docstring)."""
    if settings.session_cookie_secure is not None:
        return settings.session_cookie_secure
    if settings.app_env == "production":
        return True
    return request.url.scheme == "https"


def set_session_cookie(
    response: Response,
    request: Request,
    settings: Settings,
    raw_token: str,
    *,
    max_age_seconds: int,
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=max_age_seconds,
        path=SESSION_COOKIE_PATH,
        secure=cookie_secure(request, settings),
        httponly=True,
        samesite=SAMESITE,
    )


def clear_session_cookie(response: Response, request: Request, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=SESSION_COOKIE_PATH,
        secure=cookie_secure(request, settings),
        httponly=True,
        samesite=SAMESITE,
    )


def set_csrf_cookie(
    response: Response,
    request: Request,
    settings: Settings,
    token: str,
) -> None:
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        path=SESSION_COOKIE_PATH,
        secure=cookie_secure(request, settings),
        httponly=False,
        samesite=SAMESITE,
    )
