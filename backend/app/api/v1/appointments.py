"""Authenticated appointment scheduling APIs.

Ownership rule: a doctor can read/write only their own calendar; an Admin can
act across doctors and must choose a doctor for writes/availability/slots.
The acting identity always comes from the verified session, never from the
request body or an AI interpretation.

Static routes (`/appointments/config`, `/availability`, `/slots`) are declared
before the `/{appointment_id}` route so FastAPI does not swallow them as UUIDs.
"""
from __future__ import annotations

import csv
import io
import time
import uuid
from datetime import date as date_type, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import DbSession, Pagination, RequestId, reject_unknown_query
from app.audit.logger import write_audit_event
from app.auth.identity import client_ip
from app.authorization.policy import Principal, require_permission
from app.config import get_settings
from app.errors import (
    Conflict,
    DependencyUnavailable,
    Forbidden,
    NotFound,
    RateLimited,
    ValidationFailed,
)
from app.llm.guardrails import GUARDRAIL_BLOCK_MESSAGE, GuardrailBlocked
from app.llm.provider import LLMRateLimited, LLMRejected, LLMTimeout, LLMUnavailable
from app.logs.service import record_operational_event
from app.schemas.appointment import (
    AppointmentConfigOut,
    AppointmentCreate,
    AppointmentEventOut,
    AppointmentInterpretRequest,
    AppointmentOut,
    AppointmentProposalOut,
    AppointmentUpdate,
    AvailabilityOut,
    AvailabilityWrite,
    PatientSuggestionListOut,
    PatientSuggestionOut,
    PatientSuggestionRequest,
    SlotsOut,
    TimeOffOut,
    TranscriptionCorrectionRequest,
    TranscriptionCorrectionResponse,
    TranscriptionCorrectionSuggestionOut,
    WorkingHoursOut,
)
from app.schemas.common import Page
from app.services import appointment_ai
from app.services import appointments as appointments_service
from app.services import patient_suggestions
from app.services import transcription_corrections
from app.services.access import check_patient_access

router = APIRouter(tags=["appointments"])


def _is_admin(principal: Principal) -> bool:
    return "clinical_admin" in principal.roles


def _is_read_all(principal: Principal) -> bool:
    """Admin and nurse may read the clinic-wide schedule (nurse read-only).

    Doctors remain restricted to their own calendar. Write routes still require
    `appointment:write`, which the nurse role does not hold, so this helper
    never widens a mutation.
    """
    return bool({"clinical_admin", "nurse"} & set(principal.roles))


def _resolve_read_doctor_id(
    principal: Principal, requested: uuid.UUID | None, *, required: bool
) -> uuid.UUID | None:
    """Resolve a doctor for a read route without granting any write authority."""
    if _is_read_all(principal):
        if requested is None and required:
            raise ValidationFailed("doctor_id is required for this appointment action.")
        return requested
    return _resolve_doctor_id(principal, requested, required=required)


def _resolve_doctor_id(
    principal: Principal, requested: uuid.UUID | None, *, required: bool
) -> uuid.UUID | None:
    if _is_admin(principal):
        if requested is None:
            if required:
                raise ValidationFailed("doctor_id is required for this appointment action.")
            return None
        return requested
    actor = principal.user_uuid
    if actor is None:
        raise Forbidden()
    if requested is not None and requested != actor:
        raise Forbidden()
    return actor


def _appointment_out(row) -> AppointmentOut:
    appointment, patient_name, patient_mrn, doctor_name = row
    duration = int((appointment.end_at - appointment.start_at).total_seconds() // 60)
    return AppointmentOut(
        id=appointment.id,
        patient_id=appointment.patient_id,
        doctor_id=appointment.doctor_id,
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        duration_minutes=duration,
        appointment_type=appointment.appointment_type,
        status=appointment.status,
        notes=appointment.notes,
        patient_display_name=str(patient_name),
        patient_mrn=str(patient_mrn),
        doctor_display_name=str(doctor_name),
        calendar_removed_at=appointment.calendar_removed_at,
        created_at=appointment.created_at,
        updated_at=appointment.updated_at,
    )


def _appointment_event_out(row) -> AppointmentEventOut:
    event, actor_name = row
    return AppointmentEventOut(
        id=event.id,
        appointment_id=event.appointment_id,
        event_type=event.event_type,
        actor_user_id=event.actor_user_id,
        actor_display_name=str(actor_name) if actor_name else None,
        old_start_at=event.old_start_at,
        old_end_at=event.old_end_at,
        new_start_at=event.new_start_at,
        new_end_at=event.new_end_at,
        old_status=event.old_status,
        new_status=event.new_status,
        request_id=event.request_id,
        created_at=event.created_at,
    )


def _availability_out(doctor_id: uuid.UUID, availability: dict) -> AvailabilityOut:
    return AvailabilityOut(
        doctor_id=doctor_id,
        timezone=availability["timezone"],
        default_duration_minutes=availability["default_duration_minutes"],
        slot_increment_minutes=availability["slot_increment_minutes"],
        working_hours=[
            WorkingHoursOut(
                weekday=row.weekday, start_time=row.start_time, end_time=row.end_time
            )
            for row in availability["working_hours"]
        ],
        time_off=[
            TimeOffOut(
                id=row.id, start_at=row.start_at, end_at=row.end_at, reason=row.reason
            )
            for row in availability["time_off"]
        ],
    )


async def _audit(
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


@router.get("/appointments/config", response_model=AppointmentConfigOut)
async def get_appointment_config(
    principal: Principal = Depends(require_permission("appointment:read")),
) -> AppointmentConfigOut:
    settings = get_settings()
    return AppointmentConfigOut(
        timezone=settings.clinic_timezone,
        max_duration_minutes=appointments_service.MAX_DURATION_MINUTES,
        max_range_days=appointments_service.MAX_RANGE_DAYS,
        history_max_range_days=appointments_service.HISTORY_MAX_RANGE_DAYS,
    )


@router.post(
    "/appointments/patient-suggestions",
    response_model=PatientSuggestionListOut,
)
async def suggest_patients_for_transcript(
    body: PatientSuggestionRequest,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:read")),
) -> PatientSuggestionListOut:
    """Return bounded name/MRN corrections for a voice transcript.

    Deterministic, read-only, and limited to patients the caller can already
    read. Neither the submitted transcript nor the suggestions are logged.
    """
    suggestions = await patient_suggestions.suggest_patients(db, text=body.text)
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="patient.suggest",
        resource_type="patient",
        resource_id=None,
        outcome="success",
    )
    return PatientSuggestionListOut(
        items=[
            PatientSuggestionOut(
                display_name=item.display_name,
                mrn=item.mrn,
                matched_name_text=item.matched_name_text,
                matched_mrn_text=item.matched_mrn_text,
            )
            for item in suggestions
        ]
    )


@router.post(
    "/appointments/transcription-corrections",
    response_model=TranscriptionCorrectionResponse,
)
async def suggest_transcription_corrections(
    body: TranscriptionCorrectionRequest,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("appointment:write")),
) -> TranscriptionCorrectionResponse:
    """Analyze one transcript for likely speech-recognition mistakes.

    Read-only: it never creates or modifies an appointment. The transcript is
    not persisted or logged; only bounded metadata is recorded. The existing
    patient-suggestion endpoint remains the authorized deterministic source for
    patient-name/MRN alternatives.
    """
    started = time.perf_counter()
    try:
        result = await transcription_corrections.suggest_corrections(
            db,
            text=body.text,
            language=body.language,
            principal=principal,
            request_id=request_id,
            settings=get_settings(),
        )
    except RateLimited:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.correction",
            resource_type="appointment_transcript",
            resource_id=None,
            outcome="denied",
        )
        raise

    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="appointment.correction",
        resource_type="appointment_transcript",
        resource_id=None,
        outcome="success",
    )
    await record_operational_event(
        event_type="appointment.correction.checked",
        actor_id=principal.user_uuid,
        request_id=request_id,
        outcome="success" if result.analysis_available else "warning",
        metadata={
            "language": body.language,
            "count": len(result.suggestions),
            "model": result.model or "",
            "status": "available" if result.analysis_available else "unavailable",
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return TranscriptionCorrectionResponse(
        language=result.language,
        has_suggestions=bool(result.suggestions),
        suggestions=[
            TranscriptionCorrectionSuggestionOut(
                original_text=item.original_text,
                suggested_text=item.suggested_text,
                reason=item.reason,
                confidence=item.confidence,
                category=item.category,
            )
            for item in result.suggestions
        ],
        analysis_available=result.analysis_available,
        model=result.model,
    )


@router.post("/appointments/interpret", response_model=AppointmentProposalOut)
async def interpret_appointment(
    body: AppointmentInterpretRequest,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    doctor_id: uuid.UUID | None = Query(default=None),
    _unknown: None = Depends(reject_unknown_query("doctor_id")),
    principal: Principal = Depends(require_permission("appointment:write")),
) -> AppointmentProposalOut:
    """Interpret a spoken/typed request into a proposal; NEVER creates it.

    The model is called with `tools=[]` and cannot name a patient id, doctor
    id, or timestamp. Patient resolution uses the existing read-only
    `search_authorized_patients` MCP tool, and all local->UTC conversion plus
    slot/conflict checks happen server-side. Confirmation is a separate
    `POST /appointments` with the normal authorization and revalidation.
    """
    settings = get_settings()
    scoped = _resolve_doctor_id(principal, doctor_id, required=True)
    assert scoped is not None

    rate_key = principal.user_id or client_ip(request) or "unknown"
    retry_after = appointment_ai.appointment_ai_rate_limiter.retry_after_seconds(rate_key)
    if retry_after is not None:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.ai.propose",
            resource_type="appointment_proposal",
            resource_id=None,
            outcome="denied",
        )
        raise RateLimited(
            "Too many appointment interpretation requests. Please retry later.",
            retry_after=retry_after,
        )
    appointment_ai.appointment_ai_rate_limiter.record(rate_key)

    try:
        proposal = await appointment_ai.interpret_request(
            db,
            text=body.text,
            doctor_id=scoped,
            principal=principal,
            request_id=request_id,
            ip=client_ip(request),
            settings=settings,
        )
    except GuardrailBlocked:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.ai.propose",
            resource_type="appointment_proposal",
            resource_id=None,
            outcome="denied",
        )
        raise Forbidden(GUARDRAIL_BLOCK_MESSAGE) from None
    except LLMRateLimited as exc:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.ai.propose",
            resource_type="appointment_proposal",
            resource_id=None,
            outcome="error",
        )
        raise RateLimited(exc.message, retry_after=exc.retry_after)
    except (LLMUnavailable, LLMTimeout, LLMRejected) as exc:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.ai.propose",
            resource_type="appointment_proposal",
            resource_id=None,
            outcome="error",
        )
        raise DependencyUnavailable(exc.message)
    except DependencyUnavailable:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.ai.propose",
            resource_type="appointment_proposal",
            resource_id=None,
            outcome="error",
        )
        raise

    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="appointment.ai.propose",
        resource_type="appointment_proposal",
        resource_id=None,
        outcome="success",
    )
    return proposal


@router.get("/appointments/availability", response_model=AvailabilityOut)
async def get_availability(
    db: DbSession,
    doctor_id: uuid.UUID | None = Query(default=None),
    _unknown: None = Depends(reject_unknown_query("doctor_id")),
    principal: Principal = Depends(require_permission("appointment:read")),
) -> AvailabilityOut:
    scoped = _resolve_read_doctor_id(principal, doctor_id, required=True)
    assert scoped is not None
    await appointments_service.ensure_doctor(db, scoped)
    return _availability_out(scoped, await appointments_service.get_availability(db, scoped))


@router.put("/appointments/availability", response_model=AvailabilityOut)
async def put_availability(
    body: AvailabilityWrite,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    doctor_id: uuid.UUID | None = Query(default=None),
    _unknown: None = Depends(reject_unknown_query("doctor_id")),
    principal: Principal = Depends(require_permission("appointment:manage_availability")),
) -> AvailabilityOut:
    scoped = _resolve_doctor_id(principal, doctor_id, required=True)
    assert scoped is not None
    await appointments_service.ensure_doctor(db, scoped)
    availability = await appointments_service.replace_availability(
        db,
        doctor_id=scoped,
        default_duration_minutes=body.default_duration_minutes,
        slot_increment_minutes=body.slot_increment_minutes,
        working_hours=body.working_hours,
        time_off=body.time_off,
        created_by=principal.user_uuid,
    )
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="appointment.availability.update",
        resource_type="doctor",
        resource_id=str(scoped),
        outcome="success",
    )
    return _availability_out(scoped, availability)


@router.get("/appointments/slots", response_model=SlotsOut)
async def get_slots(
    db: DbSession,
    date: str = Query(...),
    doctor_id: uuid.UUID | None = Query(default=None),
    _unknown: None = Depends(reject_unknown_query("date", "doctor_id")),
    principal: Principal = Depends(require_permission("appointment:read")),
) -> SlotsOut:
    scoped = _resolve_read_doctor_id(principal, doctor_id, required=True)
    assert scoped is not None
    try:
        local_day = date_type.fromisoformat(date)
    except ValueError:
        raise ValidationFailed("date must be YYYY-MM-DD.") from None
    today = appointments_service.utc_now().astimezone(appointments_service.clinic_zone()).date()
    if local_day < today or local_day > today + timedelta(days=366):
        raise ValidationFailed("date is outside the supported booking window.")
    await appointments_service.ensure_doctor(db, scoped)
    return SlotsOut.model_validate(
        await appointments_service.list_slots(db, doctor_id=scoped, local_day=local_day)
    )


@router.get("/appointments", response_model=Page[AppointmentOut])
async def list_appointments(
    db: DbSession,
    pagination: Pagination,
    start: datetime = Query(...),
    end: datetime = Query(...),
    doctor_id: uuid.UUID | None = Query(default=None),
    patient_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    _unknown: None = Depends(
        reject_unknown_query(
            "start", "end", "doctor_id", "patient_id", "status", "limit", "offset"
        )
    ),
    principal: Principal = Depends(require_permission("appointment:read")),
) -> Page[AppointmentOut]:
    scoped_doctor = _resolve_read_doctor_id(principal, doctor_id, required=False)
    if patient_id is not None and not await check_patient_access(principal, patient_id, db=db):
        raise NotFound()
    rows, total = await appointments_service.list_appointments(
        db,
        start=start,
        end=end,
        doctor_id=scoped_doctor,
        patient_id=patient_id,
        status=status,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[AppointmentOut](
        items=[_appointment_out(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("/appointments", response_model=AppointmentOut, status_code=201)
async def create_appointment(
    body: AppointmentCreate,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("appointment:write")),
) -> AppointmentOut:
    doctor_id = _resolve_doctor_id(principal, body.doctor_id, required=True)
    assert doctor_id is not None
    if not await check_patient_access(principal, body.patient_id, db=db):
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.denied",
            resource_type="patient",
            resource_id=str(body.patient_id),
            outcome="denied",
        )
        raise NotFound()
    try:
        appointment = await appointments_service.create_appointment(
            db,
            patient_id=body.patient_id,
            doctor_id=doctor_id,
            start_at=body.start_at,
            duration_minutes=body.duration_minutes,
            appointment_type=body.appointment_type,
            notes=body.notes,
            status=body.status,
            created_by=principal.user_uuid,
        )
    except Conflict:
        await record_operational_event(
            event_type="appointment.conflict",
            actor_id=principal.user_uuid,
            resource_type="patient",
            resource_id=str(body.patient_id),
            request_id=request_id,
            outcome="warning",
            metadata={"reason": "slot_conflict"},
        )
        raise
    except ValidationFailed:
        await record_operational_event(
            event_type="appointment.create_rejected",
            actor_id=principal.user_uuid,
            resource_type="patient",
            resource_id=str(body.patient_id),
            request_id=request_id,
            outcome="warning",
            metadata={"reason": "validation"},
        )
        raise
    await appointments_service.add_appointment_event(
        db,
        appointment_id=appointment.id,
        event_type="created",
        actor_user_id=principal.user_uuid,
        request_id=request_id,
        new_start_at=appointment.start_at,
        new_end_at=appointment.end_at,
        new_status=appointment.status,
    )
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="appointment.create",
        resource_type="appointment",
        resource_id=str(appointment.id),
        outcome="success",
    )
    row = await appointments_service.get_appointment_with_names(db, appointment.id)
    assert row is not None
    return _appointment_out(row)


@router.get("/appointments/history", response_model=Page[AppointmentOut])
async def list_appointment_history(
    db: DbSession,
    pagination: Pagination,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    doctor_id: uuid.UUID | None = Query(default=None),
    patient_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=32),
    visit_type: str | None = Query(default=None, max_length=100),
    q: str | None = Query(default=None, max_length=100),
    _unknown: None = Depends(
        reject_unknown_query(
            "start",
            "end",
            "doctor_id",
            "patient_id",
            "status",
            "visit_type",
            "q",
            "limit",
            "offset",
        )
    ),
    principal: Principal = Depends(require_permission("appointment:read")),
) -> Page[AppointmentOut]:
    """Historical appointments, including past visits with no recorded outcome."""
    scoped_doctor = _resolve_read_doctor_id(principal, doctor_id, required=False)
    if patient_id is not None and not await check_patient_access(principal, patient_id, db=db):
        raise NotFound()
    rows, total = await appointments_service.list_appointment_history(
        db,
        start=start,
        end=end,
        doctor_id=scoped_doctor,
        patient_id=patient_id,
        status=status,
        visit_type=visit_type,
        query=q,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[AppointmentOut](
        items=[_appointment_out(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/appointments/history/export.csv")
async def export_appointment_history_csv(
    db: DbSession,
    request: Request,
    request_id: RequestId,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    doctor_id: uuid.UUID | None = Query(default=None),
    patient_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=32),
    visit_type: str | None = Query(default=None, max_length=100),
    q: str | None = Query(default=None, max_length=100),
    _unknown: None = Depends(
        reject_unknown_query(
            "start",
            "end",
            "doctor_id",
            "patient_id",
            "status",
            "visit_type",
            "q",
        )
    ),
    principal: Principal = Depends(require_permission("appointment:export")),
) -> Response:
    """CSV of persisted history for Doctor/Admin; bounded to one selected range."""
    if start is None or end is None:
        raise ValidationFailed("History export requires both start and end.")
    scoped_doctor = _resolve_read_doctor_id(principal, doctor_id, required=False)
    if patient_id is not None and not await check_patient_access(principal, patient_id, db=db):
        raise NotFound()
    rows, total = await appointments_service.list_appointment_history(
        db,
        start=start,
        end=end,
        doctor_id=scoped_doctor,
        patient_id=patient_id,
        status=status,
        visit_type=visit_type,
        query=q,
        limit=appointments_service.HISTORY_EXPORT_MAX_ROWS + 1,
        offset=0,
    )
    if total > appointments_service.HISTORY_EXPORT_MAX_ROWS:
        raise ValidationFailed(
            "The selected history range contains too many appointments; narrow the range."
        )

    zone = appointments_service.clinic_zone()
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "appointment_id",
            "patient_name",
            "patient_mrn",
            "doctor_name",
            "start_at_utc",
            "end_at_utc",
            "start_at_clinic",
            "end_at_clinic",
            "visit_type",
            "duration_minutes",
            "status",
            "display_status",
            "created_at_utc",
            "updated_at_utc",
            "calendar_removed_at_utc",
        ]
    )
    labels = {
        "scheduled": "Scheduled",
        "confirmed": "Confirmed",
        "completed": "Completed",
        "cancelled": "Cancelled",
        "no_show": "No show",
        "past_unmarked": "Past — Status not updated",
    }
    now = appointments_service.utc_now()
    for appointment, patient_name, patient_mrn, doctor_name in rows:
        display = appointments_service.appointment_display_status(
            appointment.status, appointment.end_at, now=now
        )
        writer.writerow(
            [
                appointment.id,
                patient_name,
                patient_mrn,
                doctor_name,
                appointment.start_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                appointment.end_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                appointment.start_at.astimezone(zone).isoformat(),
                appointment.end_at.astimezone(zone).isoformat(),
                appointment.appointment_type or "",
                int((appointment.end_at - appointment.start_at).total_seconds() // 60),
                appointment.status,
                labels.get(display, display.replace("_", " ").title()),
                appointment.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                appointment.updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                appointment.calendar_removed_at.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                if appointment.calendar_removed_at
                else "",
            ]
        )
    filename = (
        "appointment-history-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    )
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="appointment.history.export",
        resource_type="appointment_history",
        resource_id=None,
        outcome="success",
    )
    return Response(
        content=output.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/appointments/{appointment_id}/history", response_model=list[AppointmentEventOut])
async def get_appointment_lifecycle(
    appointment_id: uuid.UUID,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("appointment:read")),
) -> list[AppointmentEventOut]:
    row = await appointments_service.get_appointment_with_names(db, appointment_id)
    if row is None:
        raise NotFound()
    if not _is_read_all(principal) and row[0].doctor_id != principal.user_uuid:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.denied",
            resource_type="appointment",
            resource_id=str(appointment_id),
            outcome="denied",
        )
        raise NotFound()
    events = await appointments_service.list_appointment_events(db, appointment_id)
    return [_appointment_event_out(event) for event in events]


@router.get("/appointments/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    appointment_id: uuid.UUID,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("appointment:read")),
) -> AppointmentOut:
    row = await appointments_service.get_appointment_with_names(db, appointment_id)
    if row is None:
        raise NotFound()
    if not _is_read_all(principal) and row[0].doctor_id != principal.user_uuid:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.denied",
            resource_type="appointment",
            resource_id=str(appointment_id),
            outcome="denied",
        )
        raise NotFound()
    return _appointment_out(row)


@router.patch("/appointments/{appointment_id}", response_model=AppointmentOut)
async def update_appointment(
    appointment_id: uuid.UUID,
    body: AppointmentUpdate,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("appointment:write")),
) -> AppointmentOut:
    row = await appointments_service.get_appointment_with_names(db, appointment_id)
    if row is None or (not _is_admin(principal) and row[0].doctor_id != principal.user_uuid):
        raise NotFound()
    appointment = row[0]
    old_start = appointment.start_at
    old_end = appointment.end_at
    old_status = appointment.status
    old_type = appointment.appointment_type
    old_notes = appointment.notes
    action = "appointment.update"
    try:
        await appointments_service.update_appointment(
            db,
            appointment,
            start_at=body.start_at,
            duration_minutes=body.duration_minutes,
            appointment_type=body.appointment_type,
            notes=body.notes,
            status=body.status,
        )
    except Conflict:
        await record_operational_event(
            event_type="appointment.conflict",
            actor_id=principal.user_uuid,
            resource_type="appointment",
            resource_id=str(appointment_id),
            request_id=request_id,
            outcome="warning",
            metadata={"reason": "conflict"},
        )
        raise

    times_changed = appointment.start_at != old_start or appointment.end_at != old_end
    status_changed = appointment.status != old_status
    details_changed = (
        appointment.appointment_type != old_type or appointment.notes != old_notes
    )
    if times_changed:
        await appointments_service.add_appointment_event(
            db,
            appointment_id=appointment.id,
            event_type="rescheduled",
            actor_user_id=principal.user_uuid,
            request_id=request_id,
            old_start_at=old_start,
            old_end_at=old_end,
            new_start_at=appointment.start_at,
            new_end_at=appointment.end_at,
            old_status=old_status,
            new_status=appointment.status,
        )
    if status_changed:
        await appointments_service.add_appointment_event(
            db,
            appointment_id=appointment.id,
            event_type="status_changed",
            actor_user_id=principal.user_uuid,
            request_id=request_id,
            old_start_at=old_start,
            old_end_at=old_end,
            new_start_at=appointment.start_at,
            new_end_at=appointment.end_at,
            old_status=old_status,
            new_status=appointment.status,
        )
        if appointment.status == "completed":
            action = "appointment.complete"
        elif appointment.status == "no_show":
            action = "appointment.no_show"
        else:
            action = "appointment.update"
    elif times_changed:
        action = "appointment.reschedule"
    if not times_changed and not status_changed and details_changed:
        await appointments_service.add_appointment_event(
            db,
            appointment_id=appointment.id,
            event_type="updated",
            actor_user_id=principal.user_uuid,
            request_id=request_id,
            old_start_at=old_start,
            old_end_at=old_end,
            new_start_at=appointment.start_at,
            new_end_at=appointment.end_at,
            old_status=old_status,
            new_status=appointment.status,
        )
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action=action,
        resource_type="appointment",
        resource_id=str(appointment_id),
        outcome="success",
    )
    fresh = await appointments_service.get_appointment_with_names(db, appointment_id)
    assert fresh is not None
    return _appointment_out(fresh)


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: uuid.UUID,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("appointment:write")),
) -> AppointmentOut:
    row = await appointments_service.get_appointment_with_names(db, appointment_id)
    if row is None or (not _is_admin(principal) and row[0].doctor_id != principal.user_uuid):
        raise NotFound()
    old_status = row[0].status
    changed = await appointments_service.cancel_appointment(db, row[0])
    if changed:
        await appointments_service.add_appointment_event(
            db,
            appointment_id=row[0].id,
            event_type="status_changed",
            actor_user_id=principal.user_uuid,
            request_id=request_id,
            old_status=old_status,
            new_status="cancelled",
        )
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="appointment.cancel",
        resource_type="appointment",
        resource_id=str(appointment_id),
        outcome="success",
    )
    fresh = await appointments_service.get_appointment_with_names(db, appointment_id)
    assert fresh is not None
    return _appointment_out(fresh)


@router.post(
    "/appointments/{appointment_id}/remove-from-calendar",
    status_code=204,
    response_class=Response,
)
async def remove_from_calendar(
    appointment_id: uuid.UUID,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("appointment:write")),
) -> Response:
    """Admin-only visibility change; the cancelled record and audit remain."""
    if not _is_admin(principal):
        raise Forbidden("Only an administrator can remove a cancelled appointment from the calendar.")
    row = await appointments_service.get_appointment_with_names(db, appointment_id)
    if row is None:
        raise NotFound()
    changed = await appointments_service.remove_from_calendar(db, row[0])
    if changed:
        await appointments_service.add_appointment_event(
            db,
            appointment_id=row[0].id,
            event_type="calendar_removed",
            actor_user_id=principal.user_uuid,
            request_id=request_id,
            old_status=row[0].status,
            new_status=row[0].status,
        )
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="appointment.remove_from_calendar",
            resource_type="appointment",
            resource_id=str(appointment_id),
            outcome="success",
        )
    return Response(status_code=204)
