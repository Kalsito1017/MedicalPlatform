"""Patient/encounter data access (synthetic demo data only).

All queries are parameterized ORM statements, bounded, and exclude soft-deleted
rows. Search terms are matched with escaped ILIKE patterns (wildcards in user
input are treated literally) against the synthetic display name and MRN only.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab import LabResult
from app.models.medication import Prescription
from app.models.patient import Encounter, Patient

# Bounds (docs/API.md: "filter parameters are bounded and validated").
SEARCH_QUERY_MAX_LENGTH = 100
SUMMARY_ENCOUNTER_LIMIT = 5
SUMMARY_LATEST_LABS_PER_TEST = 2
SUMMARY_LATEST_LABS_LIMIT = 20
SUMMARY_ACTIVE_PRESCRIPTIONS_LIMIT = 20


def like_pattern(term: str) -> str:
    """Literal substring match: escape LIKE wildcards in the user's term."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_patients(
    db: AsyncSession, *, q: str | None, limit: int, offset: int
) -> tuple[list[Patient], int]:
    """Paginated patient list ordered by display name; optional synthetic name/MRN search."""
    filters = [Patient.not_deleted()]
    if q:
        pattern = like_pattern(q)
        filters.append(
            or_(
                Patient.display_name.ilike(pattern, escape="\\"),
                Patient.mrn.ilike(pattern, escape="\\"),
            )
        )
    base = select(Patient).where(*filters)
    total = (await db.scalar(select(func.count()).select_from(base.subquery()))) or 0
    rows = list(
        (await db.scalars(base.order_by(Patient.display_name, Patient.id).limit(limit).offset(offset))).all()
    )
    return rows, int(total)


async def get_patient(db: AsyncSession, patient_id: uuid.UUID) -> Patient | None:
    """Patient by id, excluding soft-deleted rows."""
    return await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.not_deleted()))


async def get_encounter(db: AsyncSession, encounter_id: uuid.UUID) -> Encounter | None:
    """Encounter by id, excluding soft-deleted rows."""
    return await db.scalar(
        select(Encounter).where(Encounter.id == encounter_id, Encounter.not_deleted())
    )


async def list_encounters(db: AsyncSession, patient_id: uuid.UUID, *, limit: int) -> list[Encounter]:
    """Most recent encounters for a patient (bounded)."""
    return list(
        (
            await db.scalars(
                select(Encounter)
                .where(Encounter.patient_id == patient_id, Encounter.not_deleted())
                .order_by(Encounter.occurred_at.desc(), Encounter.id)
                .limit(limit)
            )
        ).all()
    )


async def list_latest_labs(db: AsyncSession, patient_id: uuid.UUID) -> list[LabResult]:
    """Most recent lab results per test code (bounded per test and overall)."""
    ranked = (
        select(
            LabResult.id.label("lab_id"),
            func.row_number()
            .over(partition_by=LabResult.test_code, order_by=LabResult.collected_at.desc())
            .label("rank"),
        )
        .where(LabResult.patient_id == patient_id, LabResult.not_deleted())
        .subquery()
    )
    rows = await db.scalars(
        select(LabResult)
        .join(ranked, LabResult.id == ranked.c.lab_id)
        .where(ranked.c.rank <= SUMMARY_LATEST_LABS_PER_TEST)
        .order_by(LabResult.collected_at.desc(), LabResult.id)
        .limit(SUMMARY_LATEST_LABS_LIMIT)
    )
    return list(rows.all())


async def list_active_prescriptions(
    db: AsyncSession, patient_id: uuid.UUID, *, limit: int = SUMMARY_ACTIVE_PRESCRIPTIONS_LIMIT
) -> list[Prescription]:
    """Active prescriptions for a patient (bounded)."""
    return list(
        (
            await db.scalars(
                select(Prescription)
                .where(
                    Prescription.patient_id == patient_id,
                    Prescription.status == "active",
                    Prescription.not_deleted(),
                )
                .order_by(Prescription.start_date.desc(), Prescription.id)
                .limit(limit)
            )
        ).all()
    )
