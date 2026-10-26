"""Health endpoints (docs/API.md, unauthenticated).

- /health/live: process liveness. Never depends on DB/LLM.
- /health/ready: checks the database. The LLM is optional and never gates
  readiness.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.db.base import get_engine
from app.errors import DependencyUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready")
async def ready() -> dict[str, object]:
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - readiness must never raise a 500
        logger.warning(
            "readiness check failed: database unreachable",
            extra={"check": "database", "error_type": type(exc).__name__},
        )
        raise DependencyUnavailable(
            "The database is unavailable.",
            details={"checks": {"database": "unavailable"}},
        ) from None
    return {"status": "ready", "checks": {"database": "ok"}}
