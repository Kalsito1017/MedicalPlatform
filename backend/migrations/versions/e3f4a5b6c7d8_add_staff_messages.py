"""add internal staff messages and permissions

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-09

Doctor/Admin-only staff-to-staff messages. Messages are own-inbox/own-sent
only; the body is never copied into audit events and no patient payload or
attachment is stored.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("sender_id <> recipient_id", name="ck_message_not_self"),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["admin.staff_user.id"],
            name=op.f("fk_message_recipient_id_staff_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["admin.staff_user.id"],
            name=op.f("fk_message_sender_id_staff_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message")),
        schema="admin",
    )
    op.create_index(
        "ix_message_recipient_created",
        "message",
        ["recipient_id", "created_at"],
        unique=False,
        schema="admin",
    )
    op.create_index(
        "ix_message_sender_created",
        "message",
        ["sender_id", "created_at"],
        unique=False,
        schema="admin",
    )
    op.execute(
        sa.text(
            """
            INSERT INTO admin.permission (id, key, description)
            VALUES
                (gen_random_uuid(), 'message:read', 'View own internal staff messages.'),
                (gen_random_uuid(), 'message:write', 'Send internal staff messages.')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM admin.permission WHERE key IN ('message:read', 'message:write')"
        )
    )
    op.drop_index("ix_message_sender_created", table_name="message", schema="admin")
    op.drop_index("ix_message_recipient_created", table_name="message", schema="admin")
    op.drop_table("message", schema="admin")
