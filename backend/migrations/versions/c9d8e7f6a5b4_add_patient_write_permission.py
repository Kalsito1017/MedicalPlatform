"""add patient:write permission

Revision ID: c9d8e7f6a5b4
Revises: b8e4f1a2c3d5
Create Date: 2026-11-09

The permission catalog mirrors app.authorization.policy. Adding this row keeps
existing databases consistent before the idempotent seed runs. Enforcement is
always from the policy module; this table is reference/audit data.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, None] = "b8e4f1a2c3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO admin.permission (id, key, description)
            VALUES (gen_random_uuid(), 'patient:write', 'Create synthetic patient records.')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM admin.permission WHERE key = 'patient:write'"))
