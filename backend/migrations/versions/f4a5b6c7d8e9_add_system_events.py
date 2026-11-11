"""add operational system events and logs:read permission

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-11-09

Operational events are separate from the durable security audit trail. They
are bounded by an application retention window and contain only allowlisted
flat metadata (no clinical payloads, request bodies, secrets, audio or text).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'success', 'warning', 'error', 'security')",
            name="ck_system_event_severity",
        ),
        sa.CheckConstraint(
            "category IN ('auth', 'security', 'patients', 'encounters', 'labs', "
            "'prescriptions', 'appointments', 'ai', 'mcp', 'voice', "
            "'administration', 'system')",
            name="ck_system_event_category",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["admin.staff_user.id"],
            name=op.f("fk_system_event_actor_id_staff_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_event")),
        schema="admin",
    )
    for name, columns in (
        ("ix_system_event_created_at", ["created_at"]),
        ("ix_system_event_severity_created", ["severity", "created_at"]),
        ("ix_system_event_category_created", ["category", "created_at"]),
        ("ix_system_event_event_type_created", ["event_type", "created_at"]),
        ("ix_system_event_actor_created", ["actor_id", "created_at"]),
    ):
        op.create_index(name, "system_event", columns, unique=False, schema="admin")
    op.execute(
        sa.text(
            """
            INSERT INTO admin.permission (id, key, description)
            VALUES (gen_random_uuid(), 'logs:read', 'View the Admin system log stream.')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM admin.permission WHERE key = 'logs:read'"))
    for name in (
        "ix_system_event_actor_created",
        "ix_system_event_event_type_created",
        "ix_system_event_category_created",
        "ix_system_event_severity_created",
        "ix_system_event_created_at",
    ):
        op.drop_index(name, table_name="system_event", schema="admin")
    op.drop_table("system_event", schema="admin")
