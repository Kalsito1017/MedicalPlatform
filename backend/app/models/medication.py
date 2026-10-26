"""`medications` schema models: medication catalog, prescriptions, and audit trails.

Dose amounts use Numeric (never float). medication_name is kept on the
prescription even when medication_id is present, so a prescription stays
meaningful if the catalog entry changes.

Prescription audit-trail design (both tables ship per the architecture target
and serve different questions):

- `prescription_history` = LIFECYCLE EVENTS ("what happened to this
  prescription?"): coarse-grained moments like created / status_changed /
  dose_changed / stopped, each optionally naming the from/to values.
- `prescription_change` = FIELD-LEVEL DIFFS ("exactly which field changed?"):
  one row per changed field (field, old_value, new_value), e.g. a dose change
  is one history event plus one change row for `dose_amount`.

Both are immutable append-only event logs (like `admin.audit_event`) and are
therefore never soft-deleted. `prescription_item` is a first-class clinical
record of the prescription class and is soft-deleted like the prescription.
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
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
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


class PrescriptionItem(TimestampMixin, SoftDeleteMixin, Base):
    """One line item of a prescription (what was actually prescribed).

    `medication_name` is denormalized like on the parent prescription;
    `dose_amount` uses Numeric (never float) and `sequence_no` orders the
    items within one prescription (e.g. taper steps: item 1 then item 2).
    """

    __tablename__ = "prescription_item"
    __table_args__ = (
        Index("ix_prescription_item_prescription_id", "prescription_id"),
        UniqueConstraint(
            "prescription_id",
            "sequence_no",
            name="uq_prescription_item_prescription_sequence",
        ),
        CheckConstraint("sequence_no >= 1", name="ck_prescription_item_sequence_no"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prescription_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.prescription.id", ondelete="RESTRICT"),
        nullable=False,
    )
    medication_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.medication.id", ondelete="RESTRICT"),
        nullable=True,
    )
    medication_name: Mapped[str] = mapped_column(Text, nullable=False)
    dose_amount: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    dose_unit: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)


class PrescriptionHistory(TimestampMixin, Base):
    """Lifecycle event log for prescriptions (see module docstring for the split).

    Immutable append-only rows, never soft-deleted. `changed_by` is staff
    attribution metadata (nullable, ON DELETE SET NULL).
    """

    __tablename__ = "prescription_history"
    __table_args__ = (
        Index("ix_prescription_history_prescription_id_changed_at", "prescription_id", "changed_at"),
        CheckConstraint(
            "change_type IN ('created', 'status_changed', 'dose_changed', 'stopped', 'cancelled', 'completed', 'restarted')",
            name="ck_prescription_history_change_type",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prescription_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.prescription.id", ondelete="RESTRICT"),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="SET NULL"),
        nullable=True,
    )


class PrescriptionChange(TimestampMixin, Base):
    """Field-level diff log for prescriptions (see module docstring for the split).

    One row per changed field (`field`, `old_value`, `new_value`). Immutable
    append-only rows, never soft-deleted. `changed_by` is staff attribution
    metadata (nullable, ON DELETE SET NULL).
    """

    __tablename__ = "prescription_change"
    __table_args__ = (
        Index("ix_prescription_change_prescription_id_changed_at", "prescription_id", "changed_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prescription_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.prescription.id", ondelete="RESTRICT"),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="SET NULL"),
        nullable=True,
    )
