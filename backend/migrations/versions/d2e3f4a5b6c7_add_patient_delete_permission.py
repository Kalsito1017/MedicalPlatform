"""add patient:delete permission

Revision ID: d2e3f4a5b6c7
Revises: c9d8e7f6a5b4
Create Date: 2026-11-09

Admin-only soft-delete permission for patient records. Enforcement remains in
app.authorization.policy; this catalog row is reference/audit data.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO admin.permission (id, key, description)
            VALUES (gen_random_uuid(), 'patient:delete', 'Soft-delete patient records (Admin only).')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM admin.permission WHERE key = 'patient:delete'"))
