"""Shared API conventions from docs/API.md: error envelope, pagination, timestamps.

- Error envelope: {"error": {"code", "message", "request_id", "details"}}
- Pagination response: {"items", "total", "limit", "offset"} with limit 1..100.
- Timestamps serialize as ISO-8601 UTC with a "Z" suffix.
- Decimal fields serialize as JSON strings (never floats) to preserve precision.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, Field, PlainSerializer

T = TypeVar("T")

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


def _serialize_utc_iso(value: datetime) -> str:
    """Convert an aware/naive datetime to the contract format: ISO-8601 UTC 'Z'."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


Timestamp = Annotated[datetime, PlainSerializer(_serialize_utc_iso, return_type=str)]

LimitType = Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)]
OffsetType = Annotated[int, Field(ge=0)]


class PaginationParams(BaseModel):
    """Validated pagination query parameters."""

    limit: LimitType = DEFAULT_PAGE_LIMIT
    offset: OffsetType = 0


class Page(BaseModel, Generic[T]):
    """Paginated list response."""

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_LIMIT)
    offset: int = Field(ge=0)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody
