"""Clinical read endpoints (docs/API.md Patients + Encounter).

Every endpoint:
  1. enforces its permission via `require_permission` (401 anonymous / 403 denied),
  2. calls `services.access.check_patient_access` (the ONE patient-access seam),
  3. writes exactly one audit event per request (outcome `success` on 200,
     `denied` on the concealed/missing 404 — never confirm existence with 403),
  4. returns typed, bounded payloads (data provenance stays database-internal).

All data is synthetic (fictional). The only write here is patient creation;
encounters, labs and prescriptions remain read-only on these endpoints.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import DbSession, Pagination, RequestId, reject_unknown_query
from app.audit.logger import write_audit_event
from app.auth.identity import client_ip
from app.authorization.policy import Principal, require_permission
from app.errors import NotFound
from app.schemas.common import Page
from app.schemas.patient import (
    EncounterOut,
    LabResultOut,
    LabTrendsOut,
    PatientCreate,
    PatientOut,
    PatientSummaryOut,
    PrescriptionOut,
)
from app.services import labs as labs_service
from app.services import outcomes as outcomes_service
from app.services import patients as patients_service
from app.services import prescriptions as prescriptions_service
from app.services.access import check_patient_access

router = APIRouter(tags=["patients"])


async def _audit_read(
    db,
    *,
    request: Request,
    request_id: str,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
) -> None:
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()


@router.post("/patients", response_model=PatientOut, status_code=201)
async def create_patient(
    body: PatientCreate,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:write")),
) -> PatientOut:
    """Create a synthetic patient record with a server-assigned MRN."""
    patient = await patients_service.create_patient(
        db,
        given_name=body.given_name,
        family_name=body.family_name,
        display_name=body.display_name,
        date_of_birth=body.date_of_birth,
        sex=body.sex,
        blood_type=body.blood_type,
    )
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="patient.create",
        resource_type="patient",
        resource_id=str(patient.id),
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    return PatientOut.model_validate(patient)


@router.delete("/patients/{patient_id}", status_code=204, response_class=Response)
async def delete_patient(
    patient_id: uuid.UUID,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:delete")),
) -> Response:
    """Admin-only soft delete; child clinical/scheduling history is retained."""
    patient = await patients_service.get_patient_base(db, patient_id)
    if patient is None:
        await write_audit_event(
            db,
            actor_user_id=principal.user_uuid,
            action="patient.delete",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="denied",
            request_id=request_id,
            ip=client_ip(request),
        )
        await db.commit()
        raise NotFound()
    await patients_service.soft_delete_patient(db, patient)
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="patient.delete",
        resource_type="patient",
        resource_id=str(patient_id),
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/patients", response_model=Page[PatientOut])
async def list_patients(
    request: Request,
    db: DbSession,
    pagination: Pagination,
    request_id: RequestId,
    q: str | None = Query(default=None, max_length=patients_service.SEARCH_QUERY_MAX_LENGTH),
    _unknown: None = Depends(reject_unknown_query("q", "limit", "offset")),
    principal: Principal = Depends(require_permission("patient:read")),
) -> Page[PatientOut]:
    """Paginated synthetic patients; `?q=` matches display name or MRN literally."""
    term = q.strip() if q and q.strip() else None
    items, total = await patients_service.list_patients(
        db, q=term, limit=pagination.limit, offset=pagination.offset
    )
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="patient.read",
        resource_type="patient",
        resource_id=None,
        outcome="success",
    )
    return Page[PatientOut](
        items=[PatientOut.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/patients/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: uuid.UUID,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:read")),
) -> PatientOut:
    allowed = await check_patient_access(principal, patient_id, db=db)
    patient = await patients_service.get_patient_base(db, patient_id) if allowed else None
    if patient is None:
        await _audit_read(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="patient.read",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="denied",
        )
        raise NotFound()
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="patient.read",
        resource_type="patient",
        resource_id=str(patient_id),
        outcome="success",
    )
    return PatientOut.model_validate(patient)


@router.get("/patients/{patient_id}/summary", response_model=PatientSummaryOut)
async def get_patient_summary(
    patient_id: uuid.UUID,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:read")),
) -> PatientSummaryOut:
    allowed = await check_patient_access(principal, patient_id, db=db)
    patient = await patients_service.get_patient(db, patient_id) if allowed else None
    if patient is None:
        await _audit_read(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="patient.summary.read",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="denied",
        )
        raise NotFound()
    encounters = await patients_service.list_encounters(
        db, patient_id, limit=patients_service.SUMMARY_ENCOUNTER_LIMIT
    )
    latest_labs = await patients_service.list_latest_labs(db, patient_id)
    active_prescriptions = await patients_service.list_active_prescriptions(db, patient_id)
    visit_count = await patients_service.count_encounters(db, patient_id)
    medication_responses = await outcomes_service.load_treatment_responses(db, patient_id)
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="patient.summary.read",
        resource_type="patient",
        resource_id=str(patient_id),
        outcome="success",
    )
    return PatientSummaryOut(
        patient=PatientOut.model_validate(patient),
        encounters=[EncounterOut.model_validate(item) for item in encounters],
        active_problems=[],  # no problems data source in the synthetic dataset
        latest_labs=[LabResultOut.model_validate(item) for item in latest_labs],
        active_prescriptions=[PrescriptionOut.model_validate(item) for item in active_prescriptions],
        last_visit=EncounterOut.model_validate(encounters[0]) if encounters else None,
        visit_count=visit_count,
        medication_responses=medication_responses,
    )


@router.get("/patients/{patient_id}/encounters", response_model=Page[EncounterOut])
async def get_patient_encounters(
    patient_id: uuid.UUID,
    request: Request,
    db: DbSession,
    pagination: Pagination,
    request_id: RequestId,
    _unknown: None = Depends(reject_unknown_query("limit", "offset")),
    principal: Principal = Depends(require_permission("patient:read")),
) -> Page[EncounterOut]:
    """Complete paginated encounter history for one authorized patient."""
    allowed = await check_patient_access(principal, patient_id, db=db)
    if not allowed:
        await _audit_read(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="patient.read",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="denied",
        )
        raise NotFound()
    items, total = await patients_service.list_encounters_page(
        db, patient_id, limit=pagination.limit, offset=pagination.offset
    )
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="patient.read",
        resource_type="patient",
        resource_id=str(patient_id),
        outcome="success",
    )
    return Page[EncounterOut](
        items=[EncounterOut.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/patients/{patient_id}/labs", response_model=Page[LabResultOut])
async def get_patient_labs(
    patient_id: uuid.UUID,
    request: Request,
    db: DbSession,
    pagination: Pagination,
    request_id: RequestId,
    _unknown: None = Depends(reject_unknown_query("limit", "offset")),
    principal: Principal = Depends(require_permission("lab:read")),
) -> Page[LabResultOut]:
    allowed = await check_patient_access(principal, patient_id, db=db)
    if not allowed:
        await _audit_read(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="labs.read",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="denied",
        )
        raise NotFound()
    items, total = await labs_service.list_lab_results(
        db, patient_id, limit=pagination.limit, offset=pagination.offset
    )
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="labs.read",
        resource_type="patient",
        resource_id=str(patient_id),
        outcome="success",
    )
    return Page[LabResultOut](
        items=[LabResultOut.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/patients/{patient_id}/labs/trends", response_model=LabTrendsOut)
async def get_patient_lab_trends(
    patient_id: uuid.UUID,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    days: int = Query(
        default=labs_service.TREND_DAYS_DEFAULT,
        ge=labs_service.TREND_DAYS_MIN,
        le=labs_service.TREND_DAYS_MAX,
    ),
    tests: str | None = Query(
        default=None,
        max_length=labs_service.TREND_TESTS_QUERY_MAX_LENGTH,
    ),
    _unknown: None = Depends(reject_unknown_query("days", "tests")),
    principal: Principal = Depends(require_permission("lab:read")),
) -> LabTrendsOut:
    """Bounded chart-ready numeric lab trends for one authorized patient."""
    allowed = await check_patient_access(principal, patient_id, db=db)
    if not allowed:
        await _audit_read(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="labs.read",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="denied",
        )
        raise NotFound()

    test_codes = labs_service.parse_trend_test_codes(tests)
    payload = await labs_service.get_lab_trends(
        db,
        patient_id,
        days=days,
        test_codes=test_codes,
    )
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="labs.read",
        resource_type="patient",
        resource_id=str(patient_id),
        outcome="success",
    )
    return LabTrendsOut.model_validate(payload)


@router.get("/patients/{patient_id}/prescriptions", response_model=Page[PrescriptionOut])
async def get_patient_prescriptions(
    patient_id: uuid.UUID,
    request: Request,
    db: DbSession,
    pagination: Pagination,
    request_id: RequestId,
    _unknown: None = Depends(reject_unknown_query("limit", "offset")),
    principal: Principal = Depends(require_permission("prescription:read")),
) -> Page[PrescriptionOut]:
    allowed = await check_patient_access(principal, patient_id, db=db)
    if not allowed:
        await _audit_read(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="prescriptions.read",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="denied",
        )
        raise NotFound()
    items, total = await prescriptions_service.list_prescriptions(
        db, patient_id, limit=pagination.limit, offset=pagination.offset
    )
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="prescriptions.read",
        resource_type="patient",
        resource_id=str(patient_id),
        outcome="success",
    )
    return Page[PrescriptionOut](
        items=[PrescriptionOut.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/encounters/{encounter_id}", response_model=EncounterOut)
async def get_encounter(
    encounter_id: uuid.UUID,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:read")),
) -> EncounterOut:
    """Encounter by id. Missing or concealed -> 404 (never 403: existence is not confirmed)."""
    encounter = await patients_service.get_encounter(db, encounter_id)
    allowed = (
        await check_patient_access(principal, encounter.patient_id, db=db)
        if encounter is not None
        else False
    )
    if encounter is None or not allowed:
        await _audit_read(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="patient.read",
            resource_type="encounter",
            resource_id=str(encounter_id),
            outcome="denied",
        )
        raise NotFound()
    await _audit_read(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="patient.read",
        resource_type="encounter",
        resource_id=str(encounter_id),
        outcome="success",
    )
    return EncounterOut.model_validate(encounter)
