"""Add appointment lifecycle events and history export permission.

Revision ID: f1a2b3c4d5e6
Revises: b6c7d8e9f0a1
Create Date: 2026-11-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "appointment_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("old_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("old_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("old_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'updated', 'rescheduled', 'status_changed', 'calendar_removed')",
            name="ck_appointment_event_type",
        ),
        sa.CheckConstraint(
            "old_status IS NULL OR old_status IN "
            "('scheduled', 'confirmed', 'completed', 'cancelled', 'no_show')",
            name="ck_appointment_event_old_status",
        ),
        sa.CheckConstraint(
            "new_status IS NULL OR new_status IN "
            "('scheduled', 'confirmed', 'completed', 'cancelled', 'no_show')",
            name="ck_appointment_event_new_status",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["scheduling.appointment.id"],
            name=op.f("fk_appointment_event_appointment_id_appointment"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["admin.staff_user.id"],
            name=op.f("fk_appointment_event_actor_user_id_staff_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_event")),
        schema="scheduling",
    )
    op.create_index(
        "ix_appointment_event_appointment_created",
        "appointment_event",
        ["appointment_id", "created_at"],
        unique=False,
        schema="scheduling",
    )
    op.create_index(
        "ix_appointment_start_at",
        "appointment",
        ["start_at"],
        unique=False,
        schema="scheduling",
    )
    op.create_index(
        "ix_appointment_end_at",
        "appointment",
        ["end_at"],
        unique=False,
        schema="scheduling",
    )

    # Baseline lifecycle record for appointments created before this migration.
    op.execute(
        sa.text(
            """
            INSERT INTO scheduling.appointment_event (
                id, appointment_id, event_type, actor_user_id,
                old_start_at, old_end_at, new_start_at, new_end_at,
                old_status, new_status, request_id, created_at
            )
            SELECT
                gen_random_uuid(), a.id, 'created', a.created_by,
                NULL, NULL, a.start_at, a.end_at,
                NULL, a.status, NULL, a.created_at
            FROM scheduling.appointment AS a
            WHERE NOT EXISTS (
                SELECT 1
                FROM scheduling.appointment_event AS e
                WHERE e.appointment_id = a.id
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO admin.permission (id, key, description, created_at, updated_at)
            SELECT
                gen_random_uuid(),
                'appointment:export',
                'Export authorized appointment history.',
                now(),
                now()
            WHERE NOT EXISTS (
                SELECT 1 FROM admin.permission WHERE key = 'appointment:export'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM admin.permission WHERE key = 'appointment:export'")
    )
    op.drop_index(
        "ix_appointment_end_at", table_name="appointment", schema="scheduling"
    )
    op.drop_index(
        "ix_appointment_start_at", table_name="appointment", schema="scheduling"
    )
    op.drop_index(
        "ix_appointment_event_appointment_created",
        table_name="appointment_event",
        schema="scheduling",
    )
    op.drop_table("appointment_event", schema="scheduling")
