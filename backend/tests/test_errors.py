"""Error envelope, request id, CORS, and body-limit tests (docs/API.md)."""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.authorization.policy import require_permission
from app.config import load_settings
from app.main import create_app
from app.schemas.common import PaginationParams


class EchoPayload(BaseModel):
    name: str = Field(min_length=1)
    password: str


class TestModel(BaseModel):
    name: str


router = APIRouter()


@router.post("/test/echo")
async def echo(payload: EchoPayload) -> dict:
    return {"ok": True, "name": payload.name}


@router.get("/test/boom")
async def boom() -> dict:
    raise RuntimeError("secret-marker-internal-detail")


@router.get("/test/needs-auth")
async def needs_auth(_principal=Depends(require_permission("patient:read"))) -> dict:
    return {"ok": True}


@router.get("/test/paged")
async def paged(pagination: PaginationParams) -> dict:
    return {"limit": pagination.limit, "offset": pagination.offset}


@pytest_asyncio.fixture
async def app_with_routes():
    application = create_app(load_settings(env_file=None))
    application.include_router(router)
    return application


@pytest_asyncio.fixture
async def route_client(app_with_routes):
    import httpx

    transport = httpx.ASGITransport(app=app_with_routes, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as value:
        yield value


def _assert_envelope(body: dict, code: str, request_id: str) -> None:
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "request_id", "details"}
    assert error["code"] == code
    assert error["request_id"] == request_id
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)


async def test_validation_error_envelope_and_no_input_echo(route_client) -> None:
    response = await route_client.post(
        "/test/echo",
        json={"password": "sentinel-input-value"},
    )
    assert response.status_code == 400
    _assert_envelope(response.json(), "validation_error", response.headers["X-Request-Id"])
    fields = response.json()["error"]["details"]["fields"]
    assert any(field["loc"] == "body.name" for field in fields)
    # Submitted values (may contain secrets) must never be echoed back.
    assert "sentinel-input-value" not in response.text


async def test_404_envelope(route_client) -> None:
    response = await route_client.get("/does/not/exist")
    assert response.status_code == 404
    _assert_envelope(response.json(), "not_found", response.headers["X-Request-Id"])


async def test_500_is_generic_without_stack_trace(route_client) -> None:
    response = await route_client.get("/test/boom")
    assert response.status_code == 500
    _assert_envelope(response.json(), "internal_error", response.headers["X-Request-Id"])
    assert "secret-marker" not in response.text
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text


async def test_unauthenticated_dependency_denies_by_default(route_client) -> None:
    response = await route_client.get("/test/needs-auth")
    assert response.status_code == 401
    _assert_envelope(response.json(), "unauthenticated", response.headers["X-Request-Id"])


async def test_request_id_echoed_when_safe(route_client) -> None:
    response = await route_client.get("/health/live", headers={"X-Request-Id": "abc-123_OK"})
    assert response.headers["X-Request-Id"] == "abc-123_OK"


async def test_unsafe_request_id_is_replaced(route_client) -> None:
    response = await route_client.get("/health/live", headers={"X-Request-Id": "bad id \n injection"})
    assert response.headers["X-Request-Id"] != "bad id \n injection"
    uuid.UUID(response.headers["X-Request-Id"])  # must be a generated UUID


async def test_missing_request_id_is_generated(route_client) -> None:
    response = await route_client.get("/health/live")
    uuid.UUID(response.headers["X-Request-Id"])


@pytest.mark.parametrize(
    "query",
    ["?limit=0", "?limit=101", "?limit=-1", "?offset=-1", "?limit=abc"],
)
async def test_pagination_bounds_validated(route_client, query: str) -> None:
    response = await route_client.get(f"/test/paged{query}")
    assert response.status_code == 400
    _assert_envelope(response.json(), "validation_error", response.headers["X-Request-Id"])


async def test_pagination_defaults_and_bounds(route_client) -> None:
    default = await route_client.get("/test/paged")
    assert default.json() == {"limit": 20, "offset": 0}
    bounded = await route_client.get("/test/paged?limit=100&offset=50")
    assert bounded.json() == {"limit": 100, "offset": 50}


async def test_body_size_limit_envelope() -> None:
    import httpx

    settings = load_settings(env_file=None).model_copy(update={"request_max_body_bytes": 64})
    application = create_app(settings)
    application.include_router(router)
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/test/echo", json={"name": "x", "password": "y" * 500})
    assert response.status_code == 400
    body = response.json()
    _assert_envelope(body, "validation_error", response.headers["X-Request-Id"])
    assert "maximum allowed size" in body["error"]["message"]


async def test_cors_allows_configured_origin_only(client) -> None:
    allowed = await client.get("/health/live", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert allowed.headers.get("access-control-allow-credentials") == "true"

    rejected = await client.get("/health/live", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in rejected.headers
