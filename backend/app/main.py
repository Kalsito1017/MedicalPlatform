"""FastAPI application factory.

Covers Phase 4 of PLAN.md: app lifecycle, health endpoints, request ids,
centralized error envelopes (docs/API.md), CORS, body-size limits, and
structured logging. Feature routers are registered as later phases land.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import mimetypes
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import health
from app.api.v1 import api_v1
from app.auth.csrf import CsrfProtectionMiddleware
from app.auth.identity import resolve_principal
from app.authorization import policy
from app.config import (
    VOICE_MAX_UPLOAD_HARD_LIMIT,
    Settings,
    get_settings,
    setup_logging,
)
from app.db.base import dispose_engine
from app.errors import AppError, RateLimited
from app.services.voice import VOICE_TRANSCRIPTION_PATH

logger = logging.getLogger(__name__)

# Inbound X-Request-Id is trusted only when it matches this pattern (<=64 chars).
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_CODE_BY_STATUS: dict[int, str] = {
    400: "validation_error",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
    503: "dependency_unavailable",
}
_DEFAULT_MESSAGE_BY_CODE: dict[str, str] = {
    "validation_error": "Malformed input.",
    "unauthenticated": "Authentication is required.",
    "forbidden": "You do not have permission to perform this action.",
    "not_found": "The requested resource was not found.",
    "conflict": "The request conflicts with the current state of the resource.",
    "rate_limited": "Too many requests. Please retry later.",
    "internal_error": "An unexpected error occurred.",
    "dependency_unavailable": "A required dependency is temporarily unavailable.",
}


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestContextMiddleware:
    """Assigns a safe correlation id to every request and logs request completion.

    An inbound X-Request-Id is honored only if it matches REQUEST_ID_PATTERN;
    otherwise a UUID is generated. The id is echoed on every response and
    included in every log line for the request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        inbound = headers.get("x-request-id")
        request_id = inbound if inbound and REQUEST_ID_PATTERN.fullmatch(inbound) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        status_code = 500
        start = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request completed",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method", ""),
                    # Path only: query strings may contain search terms.
                    "path": scope.get("path", ""),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )


class FrontendGZipMiddleware:
    """GZip static frontend responses only (HTML/CSS/JS/SVG), never API JSON.

    Static assets are the large compressible payloads (the stylesheet alone is
    ~96 KB); compressing authenticated JSON adds observable CPU latency on the
    API path and is unnecessary for the small payloads. Paths under /api and
    /health bypass compression entirely.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=500, compresslevel=3)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if not path.startswith(("/api/", "/health/")):
                await self.gzip(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BodyTooLarge(Exception):
    """Raised internally when a request body exceeds the configured limit."""


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than the configured limit.

    The global limit stays at 1 MiB. A strictly bounded per-route allowance
    exists only for the voice transcription upload: the configured value is
    clamped to `VOICE_MAX_UPLOAD_HARD_LIMIT` and the match is exact on method +
    path, so no other route can inherit it.

    Oversized input is reported as 400 validation_error so the status/code pair
    stays within the frozen docs/API.md table.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = 1_048_576,
        *,
        route_limits: dict[tuple[str, str], int] | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.route_limits = {
            (method.upper(), path): min(int(limit), VOICE_MAX_UPLOAD_HARD_LIMIT)
            for (method, path), limit in (route_limits or {}).items()
        }

    def _limit_for(self, scope: Scope) -> int:
        key = (str(scope.get("method", "")).upper(), str(scope.get("path", "")))
        return self.route_limits.get(key, self.max_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope)
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    await self._reject(scope, send, limit=limit)
                    return
            except ValueError:
                await self._reject(scope, send, limit=limit)
                return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        try:
            await self.app(scope, counting_receive, send)
        except BodyTooLarge:
            await self._reject(scope, send, limit=limit)

    async def _reject(self, scope: Scope, send: Send, *, limit: int) -> None:
        request_id = scope.get("state", {}).get("request_id", "") or str(uuid.uuid4())
        body = {
            "error": {
                "code": "validation_error",
                "message": f"Request body exceeds the maximum allowed size of {limit} bytes.",
                "request_id": request_id,
                "details": {},
            }
        }
        response = JSONResponse(status_code=400, content=body)
        await response(scope, receive=lambda: {"type": "http.disconnect"}, send=send)


# ---------------------------------------------------------------------------
# Security headers (Phase 11)
# ---------------------------------------------------------------------------

# Deny-by-default feature policy. The ONLY exception is microphone access for
# the same origin: the appointment voice recorder uses getUserMedia explicitly
# after a user action. Camera, geolocation, payment, and sensor APIs stay off.
PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(), camera=(), display-capture=(), encrypted-media=(), "
    "fullscreen=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(self), midi=(), "
    "payment=(), picture-in-picture=(), usb=(), xr-spatial-tracking=()"
)

# Paths whose CSP may reference the Swagger/ReDoc CDN. Only reachable when the
# docs UI is enabled (never in production): see build_csp().
_DOCS_PATH_PREFIXES = ("/docs", "/redoc", "/openapi.json")

# The buildless frontend uses one small inline bootstrap script per page
# (window.__APP_CONFIG__). Its exact content is hashed at startup, so
# `script-src` stays 'self' + hashes: no 'unsafe-inline', no 'unsafe-eval'.
_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def inline_script_hashes(frontend_dir: Path | None) -> tuple[str, ...]:
    """CSP source expressions for every inline <script> in the frontend pages.

    Computed from the served files on each start, so edits to the bootstrap
    (e.g. a deployment override) are picked up automatically. Empty when the
    frontend is not served.
    """
    if frontend_dir is None:
        return ()
    hashes: list[str] = []
    for html in sorted(frontend_dir.rglob("*.html")):
        text = html.read_text(encoding="utf-8")
        for match in _INLINE_SCRIPT_RE.finditer(text):
            digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
            hashes.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return tuple(dict.fromkeys(hashes))


def build_csp(*, hashes: tuple[str, ...], docs: bool) -> str:
    """Content-Security-Policy for this frontend (see docs/SECURITY.md).

    Strict for every application response: same-origin only, no inline or
    evaluated scripts beyond the hashed page bootstrap, data: URIs only for the
    inline favicon. The docs variant is development-only relaxation for the
    Swagger/ReDoc CDN; production never enables the docs UI.
    """
    if docs:
        return (
            "default-src 'self' https://cdn.jsdelivr.net; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https://cdn.jsdelivr.net; "
            "font-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self'"
        )
    script_src = " ".join(("'self'", *hashes))
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        f"script-src {script_src}; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'"
    )


def request_is_https(scope: Scope) -> bool:
    """True when the client request arrived over HTTPS (directly or via proxy).

    HSTS is emitted only in that case: never on plain-HTTP localhost, where the
    header would be ignored by browsers anyway and only adds confusion.
    """
    if scope.get("scheme") == "https":
        return True
    forwarded = Headers(scope=scope).get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


def security_headers_for(
    settings: Settings,
    *,
    path: str,
    https: bool,
    hashes: tuple[str, ...],
) -> dict[str, str]:
    """The full Phase 11 header set for one response."""
    docs = settings.docs_enabled and path.startswith(_DOCS_PATH_PREFIXES)
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": PERMISSIONS_POLICY,
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Permitted-Cross-Domain-Policies": "none",
        "Content-Security-Policy": build_csp(hashes=hashes, docs=docs),
    }
    if https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


class SecurityHeadersMiddleware:
    """Attaches the Phase 11 security headers to every response.

    Runs just inside RequestContextMiddleware so the headers cover API
    responses, static pages, CORS preflights, and every middleware rejection.
    (Uncaught 500s are emitted outside any middleware; the exception handler in
    this module attaches the same headers explicitly.)
    """

    def __init__(self, app: ASGIApp, settings: Settings, hashes: tuple[str, ...]) -> None:
        self.app = app
        self.settings = settings
        self.hashes = hashes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = security_headers_for(
            self.settings,
            path=scope.get("path", ""),
            https=request_is_https(scope),
            hashes=self.hashes,
        )

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    response_headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


# ---------------------------------------------------------------------------
# Static frontend (single-origin local dev and the Docker image)
# ---------------------------------------------------------------------------

# Top-level path segments that never resolve to frontend files; unknown paths
# below them keep the frozen JSON error envelope (docs/API.md).
_NON_STATIC_PREFIXES = ("api", "health", "docs", "redoc", "openapi.json")

_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/vnd.microsoft.icon",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _wants_html_navigation(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def frontend_response(request: Request) -> Response | None:
    """Resolve a GET/HEAD request against the static frontend, or return None.

    Single-origin serving (local dev and Docker); production hosts the frontend
    on Cloudflare Pages and leaves this unused. Rules:

    - Existing files are served with explicit media types (extension-less paths
      map to ``<name>.html``, so ``/dashboard`` == ``/dashboard.html``).
    - Unknown extension-less paths serve the custom ``404.html`` page with a
      real 404 status for browser navigations (``Accept: text/html``) only. API
      and JSON clients keep the frozen ``404 not_found`` envelope.
    - Unknown paths with a file extension (assets) are never shadowed: 404.
    - Traversal outside the frontend directory and API/health/docs paths never
      resolve to files.
    """
    if request.method not in ("GET", "HEAD"):
        return None
    root = getattr(request.app.state, "frontend_dir", None)
    if root is None:
        return None

    raw = request.scope.get("path", "").lstrip("/")
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if not parts:
        return _file_response(root / "index.html")
    if parts[0] in _NON_STATIC_PREFIXES or ".." in parts:
        return None

    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None  # traversal attempt: fall through to the 404 envelope

    if target.is_file():
        return _file_response(target)
    if not target.suffix:
        html_alias = target.with_suffix(".html")
        if html_alias.is_file():
            return _file_response(html_alias)
        not_found_page = root / "404.html"
        if _wants_html_navigation(request) and not_found_page.is_file():
            return _file_response(not_found_page, status_code=404)
    return None


def _file_response(path: Path, *, status_code: int = 200) -> FileResponse:
    media_type = _MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    # Buildless app: HTML, JS and CSS must revalidate so a normal reload picks
    # up a fix. Images/fonts keep the bounded public cache.
    suffix = path.suffix.lower()
    cache_control = (
        "no-cache" if suffix in {".html", ".js", ".css"} else "public, max-age=3600"
    )
    return FileResponse(
        path,
        media_type=media_type,
        status_code=status_code,
        headers={"Cache-Control": cache_control},
    )


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


async def _record_api_failure(request: Request, *, status: int, code: str) -> None:
    """Best-effort operational log for a failing request (never the payload).

    Only the method, route template, status and safe error code are recorded;
    query strings, headers, bodies and exception text never enter the store.
    """
    from app.logs.service import record_operational_event

    principal = getattr(request.state, "principal", None)
    route = request.scope.get("route")
    route_path = getattr(route, "path", None) or request.url.path
    await record_operational_event(
        event_type="system.rate_limited" if status == 429 else "system.api_error",
        actor_id=getattr(principal, "user_uuid", None),
        request_id=_request_id(request),
        outcome="warning" if status == 429 else "error",
        metadata={
            "method": request.method,
            "route": str(route_path)[:120],
            "http_status": status,
            "error_code": code,
        },
    )


def _security_headers(request: Request) -> dict[str, str]:
    """Phase 11 headers for responses emitted outside the middleware stack."""
    app = request.app
    settings = getattr(app.state, "settings", None)
    if settings is None:  # pragma: no cover - create_app always sets it
        return {}
    return security_headers_for(
        settings,
        path=request.scope.get("path", ""),
        https=request_is_https(request.scope),
        hashes=getattr(app.state, "security_hashes", ()),
    )


def _envelope(code: str, message: str, request_id: str, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Map every failure mode to the frozen error envelope.

    Clients never see stack traces, SQL, credentials, or internal paths; details
    are logged server-side only.
    """

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimited) and exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        if exc.status_code >= 500 or isinstance(exc, RateLimited):
            await _record_api_failure(request, status=exc.status_code, code=exc.code)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, _request_id(request), exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field errors only: submitted values may contain secrets, never echo them.
        fields = [
            {
                "loc": ".".join(str(part) for part in error.get("loc", ())),
                "msg": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content=_envelope(
                "validation_error",
                "Malformed input.",
                _request_id(request),
                {"fields": fields},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        status = exc.status_code
        if status == 404:
            # Single-origin static serving: only unmatched GET/HEAD paths reach
            # here, so later-registered routes are never shadowed.
            static = frontend_response(request)
            if static is not None:
                return static
        code = _CODE_BY_STATUS.get(status, "validation_error" if status < 500 else "internal_error")
        default_message = _DEFAULT_MESSAGE_BY_CODE[code]
        # Stock framework details ("Not Found", ...) are replaced by the contract
        # message; explicit custom details are preserved for 4xx.
        framework_defaults = {"Not Found", "Method Not Allowed"}
        detail = exc.detail if isinstance(exc.detail, str) else ""
        message = detail if detail and detail not in framework_defaults and status < 500 else default_message
        headers = dict(exc.headers) if exc.headers else {}
        return JSONResponse(
            status_code=status,
            content=_envelope(code, message, _request_id(request)),
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Generic to the client; the diagnostic detail stays in server logs.
        # (This response is sent by ServerErrorMiddleware, outside the request-id
        # and security-headers middleware, so those headers are attached here
        # explicitly.)
        request_id = _request_id(request)
        logger.exception("unhandled exception", extra={"request_id": request_id})
        await _record_api_failure(request, status=500, code="internal_error")
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "An unexpected error occurred.", request_id),
            headers={**_security_headers(request), "X-Request-Id": request_id},
        )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


async def _record_startup_event() -> None:
    from app.logs.service import record_operational_event

    await record_operational_event(
        event_type="system.startup",
        outcome="success",
        metadata={"status": "started"},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application. Raises ConfigurationError on bad config."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info("application starting", extra={"app_env": settings.app_env})
        # Fire-and-forget so an unavailable database can never delay startup.
        startup_event = asyncio.create_task(
            _record_startup_event()
        )
        yield
        startup_event.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await startup_event
        await dispose_engine()
        logger.info("application stopped")

    app = FastAPI(
        title="Clinical AI Platform",
        description="Clinical records and AI assistant API.",
        version="0.1.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    frontend_dir = settings.resolved_frontend_dir
    app.state.frontend_dir = frontend_dir
    hashes = inline_script_hashes(frontend_dir)
    app.state.security_hashes = hashes

    register_exception_handlers(app)

    # The ONLY identity source (docs/ARCHITECTURE.md trust boundary 1): the
    # verified server-side session lookup. Deny by default when unwired.
    policy.principal_resolver = resolve_principal

    # Order matters: RequestContext must run outermost so every response,
    # including middleware rejections, carries X-Request-Id. SecurityHeaders
    # runs just inside it so the Phase 11 headers cover every response below.
    # CSRF runs inside CORS so preflight requests are answered before it and so
    # browsers can read the CSRF rejection envelope.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.request_max_body_bytes,
        route_limits={
            ("POST", VOICE_TRANSCRIPTION_PATH): settings.voice_max_upload_bytes,
        },
    )
    app.add_middleware(CsrfProtectionMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware, settings=settings, hashes=hashes)
    # Compress the static frontend (HTML/CSS/JS/SVG) when the client asks for
    # it; /api and /health responses are deliberately left uncompressed.
    app.add_middleware(FrontendGZipMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(api_v1)

    return app


app = create_app()
