"""`laboratory` schema models: synthetic lab results, orders, specimens, catalogs.

Numeric result fields use Numeric (never float) to avoid floating-point drift.

Soft-delete conventions match the codebase: first-class clinical records
(`LabResult`, `LabOrder`, `Specimen`) carry `deleted_at`; catalog/reference
tables (`TestCatalog`, `ReferenceRange`) do not — they are reference data and
are corrected in place or by migration.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
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

SCHEMA = "laboratory"


class LabResult(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "lab_result"
    __table_args__ = (
        Index("ix_lab_result_patient_id_collected_at", "patient_id", "collected_at"),
        CheckConstraint(
            "value_num IS NOT NULL OR value_text IS NOT NULL",
            name="ck_lab_result_value_present",
        ),
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
    test_code: Mapped[str] = mapped_column(Text, nullable=False)
    test_name: Mapped[str] = mapped_column(Text, nullable=False)
    value_num: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="final")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resulted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TestCatalog(TimestampMixin, Base):
    """Lab test catalog (reference data — never soft-deleted).

    `code` is the natural key the lab results reference. `ref_low`/`ref_high`
    are the default adult reference bounds; per-result `lab_result.ref_low` /
    `ref_high` remain the authoritative values for that result.
    """

    __tablename__ = "test_catalog"
    __table_args__ = (
        CheckConstraint(
            "ref_low IS NULL OR ref_high IS NULL OR ref_high >= ref_low",
            name="ck_test_catalog_ref_range",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)


class ReferenceRange(TimestampMixin, Base):
    """Advisory demographic reference range for a catalog test (reference data).

    `test_code` is a real foreign key to `test_catalog.code` (the spec called it
    "FK-ish"; the catalog code is unique so a hard FK is simple and safe).
    `sex` (`female`/`male`/`any`, NULL = any) and `age_min_years`/`age_max_years`
    (Numeric — whole or fractional years, never float; NULL = unbounded) narrow
    the range. Labs keep their own `ref_low`/`ref_high` as the authoritative
    per-result values; these rows support lookup/explanation only.
    """

    __tablename__ = "reference_range"
    __table_args__ = (
        Index("ix_reference_range_test_code", "test_code"),
        CheckConstraint("sex IS NULL OR sex IN ('female', 'male', 'any')", name="ck_reference_range_sex"),
        CheckConstraint(
            "age_min_years IS NULL OR age_max_years IS NULL OR age_max_years >= age_min_years",
            name="ck_reference_range_ages",
        ),
        CheckConstraint("ref_high >= ref_low", name="ck_reference_range_ref_order"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_code: Mapped[str] = mapped_column(
        Text,
        ForeignKey(f"{SCHEMA}.test_catalog.code", ondelete="RESTRICT"),
        nullable=False,
    )
    sex: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_min_years: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    age_max_years: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    ref_low: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    ref_high: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)


class LabOrder(TimestampMixin, SoftDeleteMixin, Base):
    """One lab order per ordering event (usually one per visit that has labs).

    `ordering_practitioner_id` is staff attribution metadata: nullable with
    ON DELETE SET NULL (removing a staff account keeps the order), while the
    clinical FKs (patient, encounter) are RESTRICT like all clinical data.
    """

    __tablename__ = "lab_order"
    __table_args__ = (
        Index("ix_lab_order_patient_id_ordered_at", "patient_id", "ordered_at"),
        CheckConstraint(
            "status IN ('ordered', 'in_progress', 'completed', 'cancelled')",
            name="ck_lab_order_status",
        ),
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
    ordering_practitioner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)


class Specimen(TimestampMixin, SoftDeleteMixin, Base):
    """One physical specimen belonging to a lab order.

    `status` tracks the specimen workflow; `collected_at`/`received_at` are
    nullable because a specimen may be ordered but not yet drawn. Times must be
    coherent (received after collected) — enforced by `ck_specimen_times`.
    """

    __tablename__ = "specimen"
    __table_args__ = (
        Index("ix_specimen_lab_order_id", "lab_order_id"),
        CheckConstraint(
            "specimen_type IN ('blood', 'urine', 'swab', 'saliva', 'stool', 'tissue')",
            name="ck_specimen_type",
        ),
        CheckConstraint(
            "status IN ('collected', 'received', 'in_lab', 'resulted', 'rejected')",
            name="ck_specimen_status",
        ),
        CheckConstraint(
            "collected_at IS NULL OR received_at IS NULL OR received_at >= collected_at",
            name="ck_specimen_times",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.lab_order.id", ondelete="RESTRICT"),
        nullable=False,
    )
    specimen_type: Mapped[str] = mapped_column(Text, nullable=False)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
