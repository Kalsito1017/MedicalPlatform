"""`patients` schema models: synthetic patients and encounters.

Name fields are free UTF-8 text (apostrophes, accents, emoji, RTL all supported)
and must never be validated with character-class restrictions. All data is
synthetic demo data.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin

SCHEMA = "patients"


class Patient(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "patient"
    __table_args__ = (
        # Synthetic-only guardrails enforced at the database level.
        CheckConstraint("mrn LIKE 'DEMO-%'", name="ck_patient_mrn_synthetic"),
        CheckConstraint("provenance = 'synthetic'", name="ck_patient_provenance_synthetic"),
        CheckConstraint("sex IN ('female', 'male', 'other', 'unknown')", name="ck_patient_sex"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mrn: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    given_name: Mapped[str] = mapped_column(Text, nullable=False)
    family_name: Mapped[str] = mapped_column(Text, nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(Text, nullable=False, default="synthetic", server_default="synthetic")


class Encounter(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "encounter"
    __table_args__ = (
        Index("ix_encounter_patient_id_occurred_at", "patient_id", "occurred_at"),
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
