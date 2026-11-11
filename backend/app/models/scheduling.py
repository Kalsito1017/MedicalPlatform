"""`scheduling` schema models: doctor availability and appointments.

Appointments are scheduling metadata, not clinical records. Cancellation is a
status transition (rows are retained for the record); there is no soft-delete
column. All timestamps are timezone-aware UTC in PostgreSQL; local wall-clock
interpretation uses the configured clinic timezone at the service boundary.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin

SCHEMA = "scheduling"

APPOINTMENT_STATUSES = ("scheduled", "confirmed", "completed", "cancelled", "no_show")


class DoctorSchedule(TimestampMixin, Base):
    """Per-doctor booking defaults."""

    __tablename__ = "doctor_schedule"
    __table_args__ = (
        CheckConstraint(
            "default_duration_minutes BETWEEN 5 AND 240",
            name="ck_doctor_schedule_duration",
        ),
        CheckConstraint(
            "slot_increment_minutes BETWEEN 5 AND 60",
            name="ck_doctor_schedule_increment",
        ),
        {"schema": SCHEMA},
    )

    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    default_duration_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=30, server_default="30"
    )
    slot_increment_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=30, server_default="30"
    )


class WorkingHours(TimestampMixin, Base):
    """One working interval for one weekday (0=Monday ... 6=Sunday)."""

    __tablename__ = "working_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_working_hours_weekday"),
        CheckConstraint("end_time > start_time", name="ck_working_hours_order"),
        Index("ix_working_hours_doctor_weekday", "doctor_id", "weekday"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.doctor_schedule.doctor_id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class TimeOff(TimestampMixin, Base):
    """A blocked period (break, day off, vacation) in UTC."""

    __tablename__ = "time_off"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_time_off_order"),
        Index("ix_time_off_doctor_start", "doctor_id", "start_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.doctor_schedule.doctor_id", ondelete="CASCADE"),
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="SET NULL"),
        nullable=True,
    )


class Appointment(TimestampMixin, Base):
    """One doctor/patient appointment. Cancellation is a status, not a delete."""

    __tablename__ = "appointment"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_appointment_order"),
        CheckConstraint(
            "status IN ('scheduled', 'confirmed', 'completed', 'cancelled', 'no_show')",
            name="ck_appointment_status",
        ),
        Index("ix_appointment_doctor_start", "doctor_id", "start_at"),
        Index("ix_appointment_patient_start", "patient_id", "start_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.patient.id", ondelete="RESTRICT"),
        nullable=False,
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    appointment_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scheduled", server_default="scheduled"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Calendar visibility, not record deletion: cancelled appointments are
    # auto-hidden 5 minutes after cancellation, and an Admin may hide one
    # immediately. The row and its audit trail are retained.
    calendar_removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="SET NULL"),
        nullable=True,
    )


# Helpers for service-layer queries (kept next to the model vocabulary).
ACTIVE_APPOINTMENT_STATUSES = ("scheduled", "confirmed")
