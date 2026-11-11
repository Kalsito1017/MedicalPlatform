"""add scheduling schema: doctor availability and appointments

Revision ID: a4d7e2c9b681
Revises: f3b9c2a7e4d1
Create Date: 2026-11-08

Additive foundation for the appointment calendar and voice scheduling flow.
All timestamps are timestamptz (UTC); working-hour wall-clock times are stored
as local TIME values and interpreted with CLINIC_TIMEZONE by the service layer.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4d7e2c9b681"
down_revision: Union[str, None] = "f3b9c2a7e4d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("scheduling", if_not_exists=True))

    op.create_table(
        "doctor_schedule",
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("default_duration_minutes", sa.SmallInteger(), server_default="30", nullable=False),
        sa.Column("slot_increment_minutes", sa.SmallInteger(), server_default="30", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "default_duration_minutes BETWEEN 5 AND 240",
            name="ck_doctor_schedule_duration",
        ),
        sa.CheckConstraint(
            "slot_increment_minutes BETWEEN 5 AND 60",
            name="ck_doctor_schedule_increment",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["admin.staff_user.id"],
            name=op.f("fk_doctor_schedule_doctor_id_staff_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("doctor_id", name=op.f("pk_doctor_schedule")),
        schema="scheduling",
    )

    op.create_table(
        "working_hours",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_working_hours_weekday"),
        sa.CheckConstraint("end_time > start_time", name="ck_working_hours_order"),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["scheduling.doctor_schedule.doctor_id"],
            name=op.f("fk_working_hours_doctor_id_doctor_schedule"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_working_hours")),
        schema="scheduling",
    )
    op.create_index(
        "ix_working_hours_doctor_weekday",
        "working_hours",
        ["doctor_id", "weekday"],
        unique=False,
        schema="scheduling",
    )

    op.create_table(
        "time_off",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("end_at > start_at", name="ck_time_off_order"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["admin.staff_user.id"],
            name=op.f("fk_time_off_created_by_staff_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["scheduling.doctor_schedule.doctor_id"],
            name=op.f("fk_time_off_doctor_id_doctor_schedule"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_off")),
        schema="scheduling",
    )
    op.create_index(
        "ix_time_off_doctor_start",
        "time_off",
        ["doctor_id", "start_at"],
        unique=False,
        schema="scheduling",
    )

    op.create_table(
        "appointment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("appointment_type", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="scheduled", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("end_at > start_at", name="ck_appointment_order"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'confirmed', 'completed', 'cancelled', 'no_show')",
            name="ck_appointment_status",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["admin.staff_user.id"],
            name=op.f("fk_appointment_created_by_staff_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["admin.staff_user.id"],
            name=op.f("fk_appointment_doctor_id_staff_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patient.id"],
            name=op.f("fk_appointment_patient_id_patient"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment")),
        schema="scheduling",
    )
    op.create_index(
        "ix_appointment_doctor_start",
        "appointment",
        ["doctor_id", "start_at"],
        unique=False,
        schema="scheduling",
    )
    op.create_index(
        "ix_appointment_patient_start",
        "appointment",
        ["patient_id", "start_at"],
        unique=False,
        schema="scheduling",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointment_patient_start", table_name="appointment", schema="scheduling"
    )
    op.drop_index(
        "ix_appointment_doctor_start", table_name="appointment", schema="scheduling"
    )
    op.drop_table("appointment", schema="scheduling")
    op.drop_index("ix_time_off_doctor_start", table_name="time_off", schema="scheduling")
    op.drop_table("time_off", schema="scheduling")
    op.drop_index(
        "ix_working_hours_doctor_weekday", table_name="working_hours", schema="scheduling"
    )
    op.drop_table("working_hours", schema="scheduling")
    op.drop_table("doctor_schedule", schema="scheduling")
    op.execute(sa.schema.DropSchema("scheduling", if_exists=True))
