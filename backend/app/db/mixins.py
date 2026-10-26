"""Reusable SQLAlchemy model mixins.

All timestamps are timezone-aware (timestamptz in PostgreSQL), stored in UTC.
The application converts explicitly to the contract format (ISO-8601, UTC, Z)
at serialization time; see app.schemas.common.Timestamp.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """created_at / updated_at with UTC server defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Soft deletion marker plus a query-filter helper.

    Rows are never hard-deleted from clinical tables; queries must apply
    `not_deleted()` (or `not_deleted_clause`) explicitly to hide deleted rows.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @classmethod
    def not_deleted(cls) -> Any:
        """SQL clause selecting rows that are not soft-deleted."""
        return cls.deleted_at.is_(None)


def not_deleted_clause(model: type[SoftDeleteMixin]) -> Any:
    """Soft-delete filter for any model mixing in SoftDeleteMixin."""
    return model.deleted_at.is_(None)
