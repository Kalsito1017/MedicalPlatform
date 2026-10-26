"""CSRF double-submit protection (docs/SECURITY.md, matching the shipped frontend).

Policy (applies to `/api/v1`; the frontend `assets/js/api.js` implements the
matching client half):

- A `csrf_token` cookie (readable by JS, `SameSite=Lax`, `Secure` in
  production/HTTPS) is issued on any GET/HEAD request that arrives without one.
- Every non-GET request under `/api/v1` must carry header `X-CSRF-Token`
  matching the cookie value (constant-time compare) once the cookie exists.
  Mismatch or missing header -> 403 `forbidden` "CSRF validation failed".
- Bootstrap: when no `csrf_token` cookie exists at all, a non-GET request is
  allowed and the cookie is issued (the frontend bootstraps with
  GET /api/v1/auth/me before login; a direct POST without any cookie cannot be
  a CSRF attack yet because the attacker cannot read or set the cookie).
- `GET`, `HEAD`, and `OPTIONS` are safe methods (CORS preflight is answered by
  the CORS middleware before this runs and never sets cookies).

Login and logout are covered like every other non-GET request.
"""
from __future__ import annotations

import json
import logging
import secrets
from http.cookies import SimpleCookie

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
API_PREFIX = "/api/v1"
CSRF_HEADER = "x-csrf-token"
CSRF_FAILURE_MESSAGE = "CSRF validation failed"


def _parse_cookie_header(header: str) -> dict[str, str]:
    cookie = SimpleCookie()
    try:
        cookie.load(header)
    except Exception:  # noqa: BLE001 - malformed cookie header is treated as absent
        return {}
    return {name: morsel.value for name, morsel in cookie.items()}


def _render_set_cookie(name: str, value: str, *, secure: bool) -> bytes:
    """Serialize a cookie exactly like Starlette's Response.set_cookie."""
    probe = StarletteResponse()
    probe.set_cookie(name, value, path="/", secure=secure, httponly=False, samesite="lax")
    for key, val in probe.raw_headers:
        if key == b"set-cookie":
            return val
    return b""  # pragma: no cover - Starlette always emits the header


class CsrfProtectionMiddleware:
    """Pure-ASGI CSRF enforcement + `csrf_token` issuance (see module docstring)."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        headers = Headers(scope=scope)
        cookie_name = self.settings.csrf_cookie_name
        cookie_token = _parse_cookie_header(headers.get("cookie", "")).get(cookie_name)

        issue_token: str | None = None
        if method in SAFE_METHODS:
            if cookie_token is None and method in ("GET", "HEAD"):
                issue_token = secrets.token_urlsafe(32)
        elif path.startswith(API_PREFIX):
            if cookie_token is None:
                # Bootstrap: no cookie exists yet, so nothing can be forged.
                issue_token = secrets.token_urlsafe(32)
            else:
                header_token = headers.get(CSRF_HEADER)
                if header_token is None or not secrets.compare_digest(header_token, cookie_token):
                    await self._reject(scope, receive, send)
                    return

        if issue_token is None:
            await self.app(scope, receive, send)
            return

        set_cookie = _render_set_cookie(
            cookie_name, issue_token, secure=cookie_secure_request(scope, self.settings)
        )

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("Set-Cookie", set_cookie.decode("latin-1"))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id", "") or ""
        logger.warning(
            "CSRF validation failed",
            extra={"request_id": request_id, "path": scope.get("path", "")},
        )
        await self._audit_denial(request_id)
        body = {
            "error": {
                "code": "forbidden",
                "message": CSRF_FAILURE_MESSAGE,
                "request_id": request_id,
                "details": {},
            }
        }
        headers = [(b"content-type", b"application/json")]
        if request_id:
            headers.append((b"x-request-id", request_id.encode("latin-1")))
        await send({"type": "http.response.start", "status": 403, "headers": headers})
        await send({"type": "http.response.body", "body": json.dumps(body).encode("utf-8")})

    async def _audit_denial(self, request_id: str) -> None:
        from app.audit.logger import record_audit_event

        await record_audit_event(
            actor_user_id=None,
            action="authz.denied",
            resource_type="csrf",
            resource_id=None,
            outcome="denied",
            request_id=request_id or None,
        )


def cookie_secure_request(scope: Scope, settings: Settings) -> bool:
    """Cookie `Secure` decision for a raw ASGI scope (see app.auth.cookies)."""
    if settings.session_cookie_secure is not None:
        return settings.session_cookie_secure
    if settings.app_env == "production":
        return True
    return scope.get("scheme", "http") == "https"
