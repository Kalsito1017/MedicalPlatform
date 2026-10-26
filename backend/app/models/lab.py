"""`laboratory` schema models: synthetic lab results.

Numeric result fields use Numeric (never float) to avoid floating-point drift.
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
