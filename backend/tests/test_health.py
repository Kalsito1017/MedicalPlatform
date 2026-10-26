"""Health endpoint tests (docs/API.md)."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api import health as health_module


def _dead_engine():
    return create_async_engine(
        "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        poolclass=NullPool,
        connect_args={"timeout": 2},
    )


async def test_live_is_200_and_needs_no_database(client, monkeypatch) -> None:
    monkeypatch.setattr(health_module, "get_engine", _dead_engine)
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}
    assert response.headers["X-Request-Id"]


async def test_ready_reports_unavailable_when_db_is_down(client, monkeypatch) -> None:
    monkeypatch.setattr(health_module, "get_engine", _dead_engine)
    response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "dependency_unavailable"
    assert body["error"]["request_id"] == response.headers["X-Request-Id"]
    assert set(body["error"]) == {"code", "message", "request_id", "details"}


@pytest.mark.postgres
async def test_ready_is_200_with_database(client, monkeypatch, migrated_test_db) -> None:
    engine = create_async_engine(migrated_test_db, poolclass=NullPool)

    def _good_engine():
        return engine

    monkeypatch.setattr(health_module, "get_engine", _good_engine)
    try:
        response = await client.get("/health/ready")
    finally:
        await engine.dispose()
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}
