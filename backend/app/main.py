"""FastAPI application factory.

Covers Phase 4 of PLAN.md: app lifecycle, health endpoints, request ids,
centralized error envelopes (docs/API.md), CORS, body-size limits, and
structured logging. Feature routers are registered as later phases land.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import health
from app.api.v1 import api_v1
from app.config import Settings, get_settings, setup_logging
from app.db.base import dispose_engine
from app.errors import AppError, RateLimited

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


class BodyTooLarge(Exception):
    """Raised internally when a request body exceeds the configured limit."""


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than max_bytes with the standard envelope.

    Oversized input is reported as 400 validation_error so the status/code pair
    stays within the frozen docs/API.md table.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(scope, send)
                    return
            except ValueError:
                await self._reject(scope, send)
                return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge()
            return message

        try:
            await self.app(scope, counting_receive, send)
        except BodyTooLarge:
            await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id", "") or str(uuid.uuid4())
        body = {
            "error": {
                "code": "validation_error",
                "message": f"Request body exceeds the maximum allowed size of {self.max_bytes} bytes.",
                "request_id": request_id,
                "details": {},
            }
        }
        response = JSONResponse(status_code=400, content=body)
        await response(scope, receive=lambda: {"type": "http.disconnect"}, send=send)


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


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
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
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
        # middleware, so the header is attached here explicitly.)
        request_id = _request_id(request)
        logger.exception("unhandled exception", extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "An unexpected error occurred.", request_id),
            headers={"X-Request-Id": request_id},
        )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application. Raises ConfigurationError on bad config."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info("application starting", extra={"app_env": settings.app_env})
        yield
        await dispose_engine()
        logger.info("application stopped")

    app = FastAPI(
        title="Clinical AI Platform (synthetic demo)",
        description="Synthetic demo data only. Not for clinical use.",
        version="0.1.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    register_exception_handlers(app)

    # Order matters: RequestContext must run outermost so every response,
    # including middleware rejections, carries X-Request-Id.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.request_max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(api_v1)

    return app


app = create_app()
