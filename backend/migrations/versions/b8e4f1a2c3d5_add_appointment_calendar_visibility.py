"""add appointment calendar visibility flag

Revision ID: b8e4f1a2c3d5
Revises: a4d7e2c9b681
Create Date: 2026-11-09

Cancelled appointments are retained as scheduling records, but disappear from
the calendar five minutes after cancellation. An Admin may hide one
immediately. `calendar_removed_at` records an explicit removal; the automatic
cutoff uses the cancellation time (`updated_at` on a closed row).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e4f1a2c3d5"
down_revision: Union[str, None] = "a4d7e2c9b681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointment",
        sa.Column("calendar_removed_at", sa.DateTime(timezone=True), nullable=True),
        schema="scheduling",
    )


def downgrade() -> None:
    op.drop_column("appointment", "calendar_removed_at", schema="scheduling")
