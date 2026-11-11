"""Prescription data access (synthetic data only).

Parameterized, bounded, soft-delete aware. Dose amounts stay `Decimal`
through serialization (JSON strings — never floats).

Read-model enrichment: `list_prescriptions` and `list_prescription_items`
return what was ACTUALLY prescribed — each prescription row carries its
bounded `items` (medication, dose+unit, sequence) as plain per-row attributes
consumed by `app.schemas.patient.PrescriptionOut.items` via from_attributes
(the models have no ORM relationships). The MCP `get_recent_prescriptions`
and `get_patient_summary` tools and the REST prescriptions endpoint all read
the same rows, so the AI and the UI stay consistent.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication import Prescription, PrescriptionItem

# Bounds (docs/API.md: "filter parameters are bounded and validated").
ITEMS_PER_PRESCRIPTION_LIMIT = 5
ITEMS_TOTAL_LIMIT = 100


async def list_prescription_items(
    db: AsyncSession, prescription_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PrescriptionItem]]:
    """Line items per prescription id (bounded per prescription and overall)."""
    if not prescription_ids:
        return {}
    ranked = (
        select(
            PrescriptionItem.id.label("row_id"),
            PrescriptionItem.prescription_id.label("rx_id"),
            func.row_number()
            .over(
                partition_by=PrescriptionItem.prescription_id,
                order_by=(PrescriptionItem.sequence_no, PrescriptionItem.id),
            )
            .label("rank"),
        )
        .where(PrescriptionItem.prescription_id.in_(prescription_ids), PrescriptionItem.not_deleted())
        .subquery()
    )
    result = await db.execute(
        select(PrescriptionItem, ranked.c.rank)
        .join(ranked, PrescriptionItem.id == ranked.c.row_id)
        .where(ranked.c.rank <= ITEMS_PER_PRESCRIPTION_LIMIT)
        .order_by(ranked.c.rank)
        .limit(ITEMS_TOTAL_LIMIT)
    )
    grouped: dict[uuid.UUID, list[PrescriptionItem]] = {}
    for row, _rank in result.all():
        grouped.setdefault(row.prescription_id, []).append(row)
    return grouped


async def list_prescriptions(
    db: AsyncSession, patient_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[Prescription], int]:
    """Paginated prescriptions for a patient, newest start date first.

    Each row carries its bounded `items` (module docstring).
    """
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
    items_by_rx = await list_prescription_items(db, [row.id for row in rows])
    for row in rows:
        row.items = items_by_rx.get(row.id, [])
    return rows, int(total)
