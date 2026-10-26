"""Shared FastAPI dependencies.

Session/identity dependencies live in app.auth.identity (`require_principal`)
and every protected route enforces authorization with
`app.authorization.policy.require_permission`. `reject_unknown_query` enforces
the frozen contract's "unknown filters are a validation_error" rule.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.errors import ValidationFailed
from app.schemas.common import DEFAULT_PAGE_LIMIT, LimitType, OffsetType, PaginationParams

# Re-exported for convenience: `Depends(get_db)` in route signatures.
get_db = get_session

DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_request_id(request: Request) -> str:
    """Correlation id for the current request (set by RequestContextMiddleware)."""
    return getattr(request.state, "request_id", "") or ""


RequestId = Annotated[str, Depends(get_request_id)]


def get_pagination(
    limit: LimitType = DEFAULT_PAGE_LIMIT,
    offset: OffsetType = 0,
) -> PaginationParams:
    """Bounded pagination query parameters (limit 1..100 default 20, offset >= 0)."""
    return PaginationParams(limit=limit, offset=offset)


Pagination = Annotated[PaginationParams, Depends(get_pagination)]


def reject_unknown_query(*allowed: str):
    """Dependency factory: unknown query parameters are a 400 validation_error.

    docs/API.md: "Filter parameters are bounded and validated; unknown filters
    are a validation_error." Field locations are reported, never values.
    """

    allowed_set = frozenset(allowed)

    async def _dependency(request: Request) -> None:
        unknown = sorted(set(request.query_params) - allowed_set)
        if unknown:
            raise ValidationFailed(
                "Malformed input.",
                details={
                    "fields": [
                        {
                            "loc": f"query.{name}",
                            "msg": "Unknown query parameter.",
                            "type": "unknown_parameter",
                        }
                        for name in unknown
                    ]
                },
            )

    return _dependency
