"""Patient/encounter data access (synthetic demo data only).

All queries are parameterized ORM statements, bounded, and exclude soft-deleted
rows. Search terms are matched with escaped ILIKE patterns (wildcards in user
input are treated literally) against the synthetic display name and MRN only.

Time windows and derived ages all read ONE clock: `utc_now()` below. Tests
freeze it (monkeypatch `app.services.patients.utc_now`) to pin "the last N
days" windows and `age_years` derivations deterministically.

Read-model enrichment (docs/API.md "Synthetic patient summary payload"): the
patient/encounter/prescription rows returned here carry the summary details the
REST summary payload and the MCP tools both serialize —
`get_patient` attaches `age_years` (derived from date_of_birth, NEVER stored),
`diagnoses` (bounded, active first) and `latest_note`; `list_active_prescriptions`
attaches each prescription's `items`. The attachments are plain Python
attributes consumed by `app.schemas.patient` via `from_attributes`; no ORM
model or migration is involved.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab import LabResult
from app.models.medication import Prescription
from app.models.patient import ClinicalNote, Diagnosis, Encounter, Patient, PatientAssignment
from app.services.prescriptions import list_prescription_items

# Bounds (docs/API.md: "filter parameters are bounded and validated").
SEARCH_QUERY_MAX_LENGTH = 100
SUMMARY_ENCOUNTER_LIMIT = 5
SUMMARY_LATEST_LABS_PER_TEST = 2
SUMMARY_LATEST_LABS_LIMIT = 20
SUMMARY_ACTIVE_PRESCRIPTIONS_LIMIT = 20
SUMMARY_DIAGNOSIS_LIMIT = 10

# get_my_recent_patients row aggregates (one compact row per patient).
PANEL_DIAGNOSIS_LIMIT = 3
PANEL_LABS_LIMIT = 12
PANEL_PRESCRIPTION_LIMIT = 3


def utc_now() -> datetime:
    """THE clock for time windows and derived ages (see module docstring).

    Single freeze point: monkeypatching this function pins every relative
    window ("the last N days", UTC) and `age_years` computation in tests.
    """
    return datetime.now(timezone.utc)


def age_in_years(dob: date | None, *, now: datetime | None = None) -> int | None:
    """Whole years between `dob` and today (UTC); None when not recorded.

    Derived on every read - never stored (docs/API.md summary payload).
    """
    if dob is None:
        return None
    today = (now if now is not None else utc_now()).date()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if years >= 0 else None


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
    """Patient by id, excluding soft-deleted rows, with summary enrichment.

    Enrichment (module docstring): attaches `age_years`, `diagnoses` (bounded,
    active first) and `latest_note` so every consumer (REST summary handler,
    MCP `get_patient_summary`) serializes the same derived facts from one
    source. Unknown/soft-deleted ids return None (concealment).
    """
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.not_deleted()))
    if patient is None:
        return None
    patient.age_years = age_in_years(patient.date_of_birth)  # derived, never stored
    patient.diagnoses = await list_diagnoses(db, patient_id, limit=SUMMARY_DIAGNOSIS_LIMIT)
    patient.latest_note = await get_latest_note(db, patient_id)
    return patient


async def get_encounter(db: AsyncSession, encounter_id: uuid.UUID) -> Encounter | None:
    """Encounter by id, excluding soft-deleted rows."""
    return await db.scalar(
        select(Encounter).where(Encounter.id == encounter_id, Encounter.not_deleted())
    )


async def list_encounters(db: AsyncSession, patient_id: uuid.UUID, *, limit: int) -> list[Encounter]:
    """Most recent encounters for a patient (bounded).

    Rows carry the per-visit vitals snapshot (`weight_kg`/`height_cm` columns),
    so consecutive rows show the weight trend.
    """
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


async def list_diagnoses(
    db: AsyncSession, patient_id: uuid.UUID, *, limit: int = SUMMARY_DIAGNOSIS_LIMIT
) -> list[Diagnosis]:
    """Problem-list entries for a patient, active first, then most recent (bounded)."""
    return list(
        (
            await db.scalars(
                select(Diagnosis)
                .where(Diagnosis.patient_id == patient_id, Diagnosis.not_deleted())
                .order_by(_active_first(Diagnosis.status), Diagnosis.recorded_at.desc(), Diagnosis.id)
                .limit(limit)
            )
        ).all()
    )


def _active_first(status_column: object) -> object:
    """ORDER BY key: `active` entries before resolved/ruled-out ones."""
    return case((status_column == "active", 0), else_=1)


async def get_latest_note(db: AsyncSession, patient_id: uuid.UUID) -> ClinicalNote | None:
    """Most recent clinical note for a patient (symptoms/assessment/proposed plan)."""
    return await db.scalar(
        select(ClinicalNote)
        .where(ClinicalNote.patient_id == patient_id, ClinicalNote.not_deleted())
        .order_by(ClinicalNote.created_at.desc(), ClinicalNote.id)
        .limit(1)
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
    """Active prescriptions for a patient (bounded), each with its `items` attached."""
    rows = list(
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
    await attach_prescription_items(db, rows)
    return rows


async def attach_prescription_items(db: AsyncSession, prescriptions: list[Prescription]) -> None:
    """Attach each prescription's bounded `items` (what was actually prescribed).

    Consumed by `app.schemas.patient.PrescriptionOut.items` via from_attributes
    (the models have no ORM relationships; the attachment is per-row state).
    """
    if not prescriptions:
        return
    items_by_rx = await list_prescription_items(db, [row.id for row in prescriptions])
    for row in prescriptions:
        row.items = items_by_rx.get(row.id, [])


# ---------------------------------------------------------------------------
# get_my_recent_patients: the practitioner panel read model
# ---------------------------------------------------------------------------
#
# "Went through me" definition (docs/API.md "MCP tool contract"): a patient
# qualifies when, within the UTC window [since, until],
#   (a) PRIMARY - at least one encounter carries `practitioner_id` = the
#       caller, or
#   (b) UNION   - the patient has an active `patient_assignment` (ended_at
#       NULL) to the caller AND had any encounter in the window (with anyone).
# When both match, (a) wins: the patient is represented by their most recent
# encounter WITH the caller; an assignment-only patient is represented by their
# most recent in-window encounter. Rows are ordered by that representing
# encounter, most recent first. The "mine" filter only ever NARROWS what the
# caller may see - visibility itself always goes through
# `services.access.check_patient_access` in the tool layer.


async def list_panel_candidates(
    db: AsyncSession,
    *,
    practitioner_id: uuid.UUID,
    since: datetime,
    until: datetime,
    limit: int,
    offset: int,
) -> list[tuple[Encounter, Patient]]:
    """Page of (representing encounter, patient) pairs for the panel window.

    One row per qualifying patient (see the definition above), representing
    encounter first, most recent first. Soft-deleted encounters and patients
    are excluded here so concealed rows never occupy row slots; the tool layer
    additionally runs the `check_patient_access` seam per row.
    """
    assigned = select(PatientAssignment.patient_id).where(
        PatientAssignment.staff_user_id == practitioner_id,
        PatientAssignment.ended_at.is_(None),
    )
    in_window = and_(Encounter.occurred_at >= since, Encounter.occurred_at <= until)
    qualifies = or_(
        Encounter.practitioner_id == practitioner_id,
        Encounter.patient_id.in_(assigned),
    )
    # Practitioner match wins: it ranks before any other in-window encounter.
    with_me_first = case((Encounter.practitioner_id == practitioner_id, 0), else_=1)
    ranked = (
        select(
            Encounter.id.label("row_id"),
            func.row_number()
            .over(
                partition_by=Encounter.patient_id,
                order_by=(with_me_first, Encounter.occurred_at.desc(), Encounter.id),
            )
            .label("rank"),
        )
        .where(in_window, qualifies, Encounter.not_deleted())
        .subquery()
    )
    result = await db.execute(
        select(Encounter, Patient)
        .join(ranked, Encounter.id == ranked.c.row_id)
        .join(Patient, Patient.id == Encounter.patient_id)
        .where(ranked.c.rank == 1, Patient.not_deleted())
        .order_by(Encounter.occurred_at.desc(), Encounter.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.all())


class PanelDetails(dict):
    """Per-patient panel aggregates keyed by patient id (plain dict subclass)."""


async def load_panel_details(
    db: AsyncSession, patient_ids: list[uuid.UUID]
) -> PanelDetails:
    """Bounded per-patient aggregates for panel rows (one query per kind).

    Returns `{patient_id: {"latest_note", "diagnoses", "key_labs",
    "active_prescriptions", "last_vitals"}}` with each list bounded
    (PANEL_* limits). `key_labs` are the most recent result per test code,
    abnormal first; `last_vitals` is the most recent encounter carrying a
    weight snapshot. Missing kinds are real absences (empty list / None).
    """
    details: PanelDetails = PanelDetails()
    if not patient_ids:
        return details
    for patient_id in patient_ids:
        details[patient_id] = {
            "latest_note": None,
            "diagnoses": [],
            "key_labs": [],
            "active_prescriptions": [],
            "last_vitals": None,
        }

    notes = await _latest_notes_by_patient(db, patient_ids)
    diagnoses = await _diagnoses_by_patient(db, patient_ids, PANEL_DIAGNOSIS_LIMIT)
    labs = await _key_labs_by_patient(db, patient_ids, PANEL_LABS_LIMIT)
    prescriptions = await _active_prescriptions_by_patient(db, patient_ids, PANEL_PRESCRIPTION_LIMIT)
    vitals = await _last_vitals_by_patient(db, patient_ids)
    await attach_prescription_items(db, [rx for rows in prescriptions.values() for rx in rows])

    for patient_id in patient_ids:
        bucket = details[patient_id]
        bucket["latest_note"] = notes.get(patient_id)
        bucket["diagnoses"] = diagnoses.get(patient_id, [])
        bucket["key_labs"] = labs.get(patient_id, [])
        bucket["active_prescriptions"] = prescriptions.get(patient_id, [])
        bucket["last_vitals"] = vitals.get(patient_id)
    return details


async def _latest_notes_by_patient(
    db: AsyncSession, patient_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ClinicalNote]:
    ranked = (
        select(
            ClinicalNote.id.label("row_id"),
            func.row_number()
            .over(
                partition_by=ClinicalNote.patient_id,
                order_by=(ClinicalNote.created_at.desc(), ClinicalNote.id),
            )
            .label("rank"),
        )
        .where(ClinicalNote.patient_id.in_(patient_ids), ClinicalNote.not_deleted())
        .subquery()
    )
    rows = await db.scalars(
        select(ClinicalNote).join(ranked, ClinicalNote.id == ranked.c.row_id).where(ranked.c.rank == 1)
    )
    return {row.patient_id: row for row in rows.all()}


async def _diagnoses_by_patient(
    db: AsyncSession, patient_ids: list[uuid.UUID], per_patient: int
) -> dict[uuid.UUID, list[Diagnosis]]:
    ranked = (
        select(
            Diagnosis.id.label("row_id"),
            Diagnosis.patient_id.label("pid"),
            func.row_number()
            .over(
                partition_by=Diagnosis.patient_id,
                order_by=(_active_first(Diagnosis.status), Diagnosis.recorded_at.desc(), Diagnosis.id),
            )
            .label("rank"),
        )
        .where(Diagnosis.patient_id.in_(patient_ids), Diagnosis.not_deleted())
        .subquery()
    )
    result = await db.execute(
        select(Diagnosis, ranked.c.rank)
        .join(ranked, Diagnosis.id == ranked.c.row_id)
        .where(ranked.c.rank <= per_patient)
        .order_by(ranked.c.rank)
    )
    grouped: dict[uuid.UUID, list[Diagnosis]] = {}
    for row, _rank in result.all():
        grouped.setdefault(row.patient_id, []).append(row)
    return grouped


async def _key_labs_by_patient(
    db: AsyncSession, patient_ids: list[uuid.UUID], per_patient: int
) -> dict[uuid.UUID, list[LabResult]]:
    """Most recent result per test code, abnormal first, bounded per patient."""
    is_abnormal = case(
        (
            or_(
                and_(
                    LabResult.ref_high.is_not(None),
                    LabResult.value_num.is_not(None),
                    LabResult.value_num > LabResult.ref_high,
                ),
                and_(
                    LabResult.ref_low.is_not(None),
                    LabResult.value_num.is_not(None),
                    LabResult.value_num < LabResult.ref_low,
                ),
            ),
            1,
        ),
        else_=0,
    ).label("is_abnormal")
    latest_per_code = (
        select(
            LabResult.id.label("row_id"),
            LabResult.patient_id.label("pid"),
            LabResult.collected_at.label("collected_at"),
            is_abnormal,
            func.row_number()
            .over(
                partition_by=(LabResult.patient_id, LabResult.test_code),
                order_by=LabResult.collected_at.desc(),
            )
            .label("rank_code"),
        )
        .where(LabResult.patient_id.in_(patient_ids), LabResult.not_deleted())
        .subquery()
    )
    bounded = (
        select(
            latest_per_code.c.row_id.label("row_id"),
            func.row_number()
            .over(
                partition_by=latest_per_code.c.pid,
                order_by=(
                    latest_per_code.c.is_abnormal.desc(),
                    latest_per_code.c.collected_at.desc(),
                    latest_per_code.c.row_id,
                ),
            )
            .label("rank_patient"),
        )
        .where(latest_per_code.c.rank_code == 1)
        .subquery()
    )
    result = await db.execute(
        select(LabResult, bounded.c.rank_patient)
        .join(bounded, LabResult.id == bounded.c.row_id)
        .where(bounded.c.rank_patient <= per_patient)
        .order_by(bounded.c.rank_patient, LabResult.collected_at.desc())
    )
    grouped: dict[uuid.UUID, list[LabResult]] = {}
    for row, _rank in result.all():
        grouped.setdefault(row.patient_id, []).append(row)
    return grouped


async def _active_prescriptions_by_patient(
    db: AsyncSession, patient_ids: list[uuid.UUID], per_patient: int
) -> dict[uuid.UUID, list[Prescription]]:
    ranked = (
        select(
            Prescription.id.label("row_id"),
            func.row_number()
            .over(
                partition_by=Prescription.patient_id,
                order_by=(Prescription.start_date.desc(), Prescription.id),
            )
            .label("rank"),
        )
        .where(
            Prescription.patient_id.in_(patient_ids),
            Prescription.status == "active",
            Prescription.not_deleted(),
        )
        .subquery()
    )
    result = await db.execute(
        select(Prescription, ranked.c.rank)
        .join(ranked, Prescription.id == ranked.c.row_id)
        .where(ranked.c.rank <= per_patient)
        .order_by(ranked.c.rank)
    )
    grouped: dict[uuid.UUID, list[Prescription]] = {}
    for row, _rank in result.all():
        grouped.setdefault(row.patient_id, []).append(row)
    return grouped


async def _last_vitals_by_patient(
    db: AsyncSession, patient_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Encounter]:
    ranked = (
        select(
            Encounter.id.label("row_id"),
            func.row_number()
            .over(
                partition_by=Encounter.patient_id,
                order_by=(Encounter.occurred_at.desc(), Encounter.id),
            )
            .label("rank"),
        )
        .where(
            Encounter.patient_id.in_(patient_ids),
            Encounter.weight_kg.is_not(None),
            Encounter.not_deleted(),
        )
        .subquery()
    )
    rows = await db.scalars(
        select(Encounter).join(ranked, Encounter.id == ranked.c.row_id).where(ranked.c.rank == 1)
    )
    return {row.patient_id: row for row in rows.all()}
