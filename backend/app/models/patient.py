"""`patients` schema models: patients, encounters, diagnoses, notes, assignments.

Name fields are free UTF-8 text (apostrophes, accents, emoji, RTL all supported)
and must never be validated with character-class restrictions. All data is
synthetic (fictional; the `provenance` column records it for audit
traceability and never serializes into an API payload).

`Patient.blood_type` design decision: stored as TEXT constrained by
`ck_patient_blood_type` to the eight ABO/Rh combinations (`A+` ... `O-`) plus
`unknown`; NULL means "not recorded". Free text was deliberately rejected:
blood group is a closed clinical vocabulary and AI lookups ("what blood type is
patient X") need unambiguous values. A rare group outside the vocabulary (e.g.
Bombay phenotype) must be introduced by a migration that widens the CHECK.

Encounter vitals (`weight_kg`, `height_cm`) are a PER-VISIT snapshot, not a
patient-level attribute: they record what was measured at that visit and so may
drift slightly between visits. They use Numeric (never float) like every other
clinical number.

`ClinicalNote` design decision: ONE ROW PER COMPLETE SOAP-STYLE RECORD (not one
row per SOAP section). Each note carries first-class `symptoms`, `assessment`
and `proposed_plan` columns so "symptoms / proposed treatment" questions are
column lookups instead of text parsing; `body` holds the composed free-text
note. `note_type` classifies the note's origin (`progress_note`, `consult_note`,
`telehealth_note`, `nurse_note`), not its SOAP section.
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
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin

SCHEMA = "patients"


class Patient(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "patient"
    __table_args__ = (
        # Synthetic-only guardrails enforced at the database level (the MRN
        # scheme is the neutral `MRN-` prefix; the provenance column is the
        # audit-side origin label and never appears in a payload).
        CheckConstraint("mrn LIKE 'MRN-%'", name="ck_patient_mrn_synthetic"),
        CheckConstraint("provenance = 'synthetic'", name="ck_patient_provenance_synthetic"),
        CheckConstraint("sex IN ('female', 'male', 'other', 'unknown')", name="ck_patient_sex"),
        # Closed ABO/Rh vocabulary; NULL = not recorded (see module docstring).
        CheckConstraint(
            "blood_type IS NULL OR blood_type IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'unknown')",
            name="ck_patient_blood_type",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mrn: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    given_name: Mapped[str] = mapped_column(Text, nullable=False)
    family_name: Mapped[str] = mapped_column(Text, nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str] = mapped_column(Text, nullable=False)
    blood_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[str] = mapped_column(Text, nullable=False, default="synthetic", server_default="synthetic")


class Encounter(TimestampMixin, SoftDeleteMixin, Base):
    """One visit. Vitals are a per-visit snapshot (see module docstring).

    `practitioner_id` is the clinician the visit was with. It is staff
    attribution metadata (not clinical data), so it is nullable and uses
    ON DELETE SET NULL: removing a staff account keeps the visit record.
    """

    __tablename__ = "encounter"
    __table_args__ = (
        Index("ix_encounter_patient_id_occurred_at", "patient_id", "occurred_at"),
        CheckConstraint("weight_kg IS NULL OR (weight_kg > 0 AND weight_kg < 600)", name="ck_encounter_weight_range"),
        CheckConstraint("height_cm IS NULL OR (height_cm > 0 AND height_cm < 300)", name="ck_encounter_height_range"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="RESTRICT"),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    encounter_type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    practitioner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)


class Diagnosis(TimestampMixin, SoftDeleteMixin, Base):
    """One problem-list entry (ICD-10-ish code + name).

    `status` follows the problem-list lifecycle: `active`, `resolved`, or
    `ruled_out` (excluded after workup). `encounter_id` links the entry to the
    visit where it was recorded; `recorded_at` is when it was documented and
    `onset_date` (date-only, nullable) is when the condition began per history.
    Soft-deleted like every first-class clinical record (rows are never
    hard-deleted); queries must apply `not_deleted()`.
    """

    __tablename__ = "diagnosis"
    __table_args__ = (
        Index("ix_diagnosis_patient_id", "patient_id"),
        CheckConstraint("status IN ('active', 'resolved', 'ruled_out')", name="ck_diagnosis_status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="RESTRICT"),
        nullable=False,
    )
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.encounter.id", ondelete="RESTRICT"),
        nullable=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    onset_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClinicalNote(TimestampMixin, SoftDeleteMixin, Base):
    """One complete SOAP-style note record (see module docstring for the design).

    `symptoms` (subjective complaints), `assessment`, and `proposed_plan` are
    required text columns so AI answers for "symptoms / proposed treatment" are
    structural, not inferred. `proposed_plan` documents what the clinician
    PROPOSED (monitoring, referrals, dose ideas) which may differ from what was
    actually prescribed (see `medications.prescription`). `body` carries the
    composed free-text note and is untrusted record text (never instructions).
    """

    __tablename__ = "clinical_note"
    __table_args__ = (
        Index("ix_clinical_note_patient_id_created_at", "patient_id", "created_at"),
        CheckConstraint(
            "note_type IN ('progress_note', 'consult_note', 'telehealth_note', 'nurse_note')",
            name="ck_clinical_note_type",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="RESTRICT"),
        nullable=False,
    )
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.encounter.id", ondelete="RESTRICT"),
        nullable=True,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    note_type: Mapped[str] = mapped_column(Text, nullable=False)
    symptoms: Mapped[str] = mapped_column(Text, nullable=False)
    assessment: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_plan: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class PatientAssignment(TimestampMixin, Base):
    """Care-team membership (link table, mirrors `admin.user_role` conventions).

    No soft-delete: membership ends through `ended_at` (NULL = ongoing), and
    link rows are never retracted individually. `care_role` is constrained to
    the current care-role vocabulary; extending it is a migration.
    """

    __tablename__ = "patient_assignment"
    __table_args__ = (
        Index("ix_patient_assignment_staff_user_id", "staff_user_id"),
        CheckConstraint("care_role IN ('attending', 'covering')", name="ck_patient_assignment_care_role"),
        # Same patient/staff pair may hold each care role at most once.
        UniqueConstraint(
            "patient_id",
            "staff_user_id",
            "care_role",
            name="uq_patient_assignment_patient_staff_role",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="RESTRICT"),
        nullable=False,
    )
    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("admin.staff_user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    care_role: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
