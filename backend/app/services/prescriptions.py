"""Prescription data access (synthetic demo data only).

Parameterized, bounded, soft-delete aware. Dose amounts stay `Decimal`
through serialization (JSON strings — never floats).
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication import Prescription


async def list_prescriptions(
    db: AsyncSession, patient_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[Prescription], int]:
    """Paginated prescriptions for a patient, newest start date first."""
    base = select(Prescription).where(
        Prescription.patient_id == patient_id, Prescription.not_deleted()
    )
    total = (await db.scalar(select(func.count()).select_from(base.subquery()))) or 0
    rows = list(
        (
            await db.scalars(
                base.order_by(Prescription.start_date.desc(), Prescription.id).limit(limit).offset(offset)
            )
        ).all()
    )
    return rows, int(total)
