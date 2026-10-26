"""`medications` schema models: medication catalog and prescriptions.

Dose amounts use Numeric (never float). medication_name is kept on the
prescription even when medication_id is present, so a prescription stays
meaningful if the catalog entry changes.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin

SCHEMA = "medications"


class Medication(Base):
    __tablename__ = "medication"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    form: Mapped[str | None] = mapped_column(Text, nullable=True)


class Prescription(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "prescription"
    __table_args__ = (
        Index("ix_prescription_patient_id_start_date", "patient_id", "start_date"),
        CheckConstraint(
            "status IN ('active', 'completed', 'stopped', 'cancelled')",
            name="ck_prescription_status",
        ),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_prescription_dates"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.patient.id", ondelete="RESTRICT"),
        nullable=False,
    )
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.encounter.id", ondelete="RESTRICT"),
        nullable=True,
    )
    medication_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.medication.id", ondelete="RESTRICT"),
        nullable=True,
    )
    medication_name: Mapped[str] = mapped_column(Text, nullable=False)
    dose_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    dose_unit: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    prescriber_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="RESTRICT"),
        nullable=True,
    )
