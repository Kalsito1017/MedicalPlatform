"""Shared FastAPI dependencies.

Extension point (Phase 5 auth agent): add session/identity dependencies here,
e.g. `get_current_principal` wired to `app.authorization.policy.principal_resolver`,
and reuse `app.authorization.policy.require_permission` on every protected route.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.schemas.common import DEFAULT_PAGE_LIMIT, LimitType, OffsetType, PaginationParams

# Re-exported for convenience: `Depends(get_db)` in route signatures.
get_db = get_session

DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_request_id(request: Request) -> str:
    """Correlation id for the current request (set by RequestContextMiddleware)."""
    return getattr(request.state, "request_id", "") or ""


def get_pagination(
    limit: LimitType = DEFAULT_PAGE_LIMIT,
    offset: OffsetType = 0,
) -> PaginationParams:
    """Bounded pagination query parameters (limit 1..100 default 20, offset >= 0)."""
    return PaginationParams(limit=limit, offset=offset)


Pagination = Annotated[PaginationParams, Depends(get_pagination)]
