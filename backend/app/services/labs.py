"""Lab-result data access (synthetic demo data only).

Parameterized, bounded, soft-delete aware. Numeric values stay `Decimal`
through serialization (JSON strings — never floats).
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab import LabResult


async def list_lab_results(
    db: AsyncSession, patient_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[LabResult], int]:
    """Paginated lab results for a patient, most recently collected first."""
    base = select(LabResult).where(
        LabResult.patient_id == patient_id, LabResult.not_deleted()
    )
    total = (await db.scalar(select(func.count()).select_from(base.subquery()))) or 0
    rows = list(
        (
            await db.scalars(
                base.order_by(LabResult.collected_at.desc(), LabResult.id).limit(limit).offset(offset)
            )
        ).all()
    )
    return rows, int(total)
