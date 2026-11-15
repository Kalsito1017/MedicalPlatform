"""Appointment scheduling services: availability, slots and conflict-safe writes.

Appointments are stored in UTC. Working hours are clinic-local wall-clock time
(`CLINIC_TIMEZONE`) and time off is UTC. Writes take PostgreSQL advisory locks
for the doctor and patient, re-check overlaps inside the transaction, and only
then insert/update - so two concurrent bookings cannot both succeed.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import Conflict, NotFound, ValidationFailed
from app.models.admin import Role, StaffUser, UserRole
from app.models.patient import Patient
from app.models.scheduling import (
    ACTIVE_APPOINTMENT_STATUSES,
    APPOINTMENT_STATUSES,
    Appointment,
    AppointmentEvent,
    DoctorSchedule,
    TimeOff,
    WorkingHours,
)

DEFAULT_DURATION_MINUTES = 30
DEFAULT_SLOT_INCREMENT_MINUTES = 30
MAX_RANGE_DAYS = 62
HISTORY_MAX_RANGE_DAYS = 366
HISTORY_EXPORT_MAX_ROWS = 5000
PAST_UNMARKED = "past_unmarked"
# Cancelled appointments clutter the calendar but remain in the scheduling
# record and audit history. They disappear from calendar list responses after
# this interval; an Admin may also remove one immediately.
CANCELLED_CALENDAR_VISIBLE_MINUTES = 5
MAX_DURATION_MINUTES = 480
MIN_DURATION_MINUTES = 5
MAX_WORKING_INTERVALS = 21
MAX_TIME_OFF_ROWS = 100


def clinic_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().clinic_timezone)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailed("Appointment timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


def local_to_utc(local_naive: datetime, zone: ZoneInfo) -> datetime:
    """Convert a clinic-local wall time to UTC, rejecting DST holes/ambiguity."""
    aware = local_naive.replace(tzinfo=zone)
    if aware.replace(fold=1).utcoffset() != aware.utcoffset():
        raise ValidationFailed(
            "That local time is ambiguous because of a daylight-saving change; choose another time."
        )
    utc = aware.astimezone(timezone.utc)
    if utc.astimezone(zone).replace(tzinfo=None) != local_naive:
        raise ValidationFailed(
            "That local time does not exist because of a daylight-saving change; choose another time."
        )
    return utc


def utc_to_local(value: datetime, zone: ZoneInfo) -> datetime:
    return _as_utc(value).astimezone(zone)


def _validate_duration(duration_minutes: int) -> None:
    if not MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES:
        raise ValidationFailed(
            f"Appointment duration must be between {MIN_DURATION_MINUTES} and "
            f"{MAX_DURATION_MINUTES} minutes."
        )


def _search_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def appointment_display_status(
    status: str, end_at: datetime, *, now: datetime | None = None
) -> str:
    """Derived, non-persisted history label for an unmarked past visit."""
    if status in ACTIVE_APPOINTMENT_STATUSES and end_at <= (now or utc_now()):
        return PAST_UNMARKED
    return status


async def ensure_doctor(db: AsyncSession, doctor_id: uuid.UUID) -> StaffUser:
    """Return an active, non-deleted user holding the doctor role, or 404."""
    user = await db.scalar(
        select(StaffUser)
        .join(UserRole, UserRole.user_id == StaffUser.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            StaffUser.id == doctor_id,
            StaffUser.is_active.is_(True),
            StaffUser.not_deleted(),
            Role.key == "doctor",
        )
    )
    if user is None:
        raise NotFound()
    return user


async def doctor_display_name(db: AsyncSession, doctor_id: uuid.UUID) -> str:
    name = await db.scalar(select(StaffUser.display_name).where(StaffUser.id == doctor_id))
    return str(name or "Doctor")


async def get_availability(db: AsyncSession, doctor_id: uuid.UUID) -> dict[str, Any]:
    schedule = await db.get(DoctorSchedule, doctor_id)
    hours = list(
        (
            await db.scalars(
                select(WorkingHours)
                .where(WorkingHours.doctor_id == doctor_id)
                .order_by(WorkingHours.weekday, WorkingHours.start_time)
            )
        ).all()
    )
    time_off = list(
        (
            await db.scalars(
                select(TimeOff)
                .where(TimeOff.doctor_id == doctor_id)
                .order_by(TimeOff.start_at, TimeOff.id)
            )
        ).all()
    )
    return {
        "doctor_id": doctor_id,
        "timezone": get_settings().clinic_timezone,
        "default_duration_minutes": schedule.default_duration_minutes if schedule else DEFAULT_DURATION_MINUTES,
        "slot_increment_minutes": schedule.slot_increment_minutes if schedule else DEFAULT_SLOT_INCREMENT_MINUTES,
        "working_hours": hours,
        "time_off": time_off,
    }


def _validate_working_hours(rows: list[Any]) -> list[WorkingHours]:
    if len(rows) > MAX_WORKING_INTERVALS:
        raise ValidationFailed("Too many working-hour intervals.")
    result: list[WorkingHours] = []
    by_day: dict[int, list[WorkingHours]] = {}
    for row in rows:
        weekday = int(row.weekday)
        if not 0 <= weekday <= 6:
            raise ValidationFailed("Weekday must be between 0 and 6.")
        if row.end_time <= row.start_time:
            raise ValidationFailed("Working-hour end time must be after start time.")
        model = WorkingHours(
            doctor_id=uuid.uuid4(),  # replaced by caller
            weekday=weekday,
            start_time=row.start_time,
            end_time=row.end_time,
        )
        by_day.setdefault(weekday, []).append(model)
        result.append(model)
    for items in by_day.values():
        items.sort(key=lambda item: item.start_time)
        for previous, current in zip(items, items[1:], strict=False):
            if current.start_time < previous.end_time:
                raise ValidationFailed("Working-hour intervals for one day must not overlap.")
    return result


async def replace_availability(
    db: AsyncSession,
    *,
    doctor_id: uuid.UUID,
    default_duration_minutes: int,
    slot_increment_minutes: int,
    working_hours: list[Any],
    time_off: list[Any],
    created_by: uuid.UUID | None,
) -> dict[str, Any]:
    if len(time_off) > MAX_TIME_OFF_ROWS:
        raise ValidationFailed("Too many time-off entries.")
    _validate_duration(default_duration_minutes)
    if not 5 <= slot_increment_minutes <= 60:
        raise ValidationFailed("Slot increment must be between 5 and 60 minutes.")
    normalized_hours = _validate_working_hours(working_hours)
    normalized_time_off: list[TimeOff] = []
    for row in time_off:
        start = _as_utc(row.start_at)
        end = _as_utc(row.end_at)
        if end <= start:
            raise ValidationFailed("Time-off end must be after start.")
        normalized_time_off.append(
            TimeOff(
                doctor_id=doctor_id,
                start_at=start,
                end_at=end,
                reason=row.reason,
                created_by=created_by,
            )
        )

    schedule = await db.get(DoctorSchedule, doctor_id)
    if schedule is None:
        schedule = DoctorSchedule(doctor_id=doctor_id)
        db.add(schedule)
    schedule.default_duration_minutes = default_duration_minutes
    schedule.slot_increment_minutes = slot_increment_minutes
    await db.flush()

    await db.execute(delete(WorkingHours).where(WorkingHours.doctor_id == doctor_id))
    await db.execute(delete(TimeOff).where(TimeOff.doctor_id == doctor_id))
    for row in normalized_hours:
        row.doctor_id = doctor_id
        db.add(row)
    for row in normalized_time_off:
        db.add(row)
    await db.flush()
    return await get_availability(db, doctor_id)


def _local_range(value: datetime, zone: ZoneInfo) -> tuple[datetime, datetime]:
    local = utc_to_local(value, zone)
    return local, local


def _working_interval_for(hours: list[WorkingHours], local_start: datetime, local_end: datetime) -> WorkingHours | None:
    for row in hours:
        if row.weekday != local_start.weekday():
            continue
        if local_start.time() >= row.start_time and local_end.time() <= row.end_time:
            if local_start.date() == local_end.date():
                return row
    return None


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


async def _time_off_overlaps(
    db: AsyncSession, doctor_id: uuid.UUID, start: datetime, end: datetime
) -> bool:
    return bool(
        await db.scalar(
            select(func.count())
            .select_from(TimeOff)
            .where(
                TimeOff.doctor_id == doctor_id,
                TimeOff.start_at < end,
                TimeOff.end_at > start,
            )
        )
    )


async def _appointment_overlap(
    db: AsyncSession,
    *,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID | None,
    start: datetime,
    end: datetime,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    filters = [
        Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES),
        Appointment.start_at < end,
        Appointment.end_at > start,
        (Appointment.doctor_id == doctor_id)
        if patient_id is None
        else ((Appointment.doctor_id == doctor_id) | (Appointment.patient_id == patient_id)),
    ]
    if exclude_id is not None:
        filters.append(Appointment.id != exclude_id)
    return bool(await db.scalar(select(func.count()).select_from(Appointment).where(*filters)))


async def _acquire_locks(db: AsyncSession, doctor_id: uuid.UUID, patient_id: uuid.UUID) -> None:
    """Serialize concurrent writes for a doctor and patient without extensions."""
    keys = sorted([f"doctor:{doctor_id}", f"patient:{patient_id}"])
    for key in keys:
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


async def validate_slot(
    db: AsyncSession,
    *,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID | None,
    start_at: datetime,
    end_at: datetime,
    exclude_id: uuid.UUID | None = None,
) -> None:
    start = _as_utc(start_at)
    end = _as_utc(end_at)
    zone = clinic_zone()
    duration = int((end - start).total_seconds() // 60)
    _validate_duration(duration)
    if start <= utc_now() + timedelta(seconds=5):
        raise ValidationFailed("Appointments must be scheduled in the future.")
    if start.date() != end.date() and utc_to_local(start, zone).date() != utc_to_local(end, zone).date():
        raise ValidationFailed("Appointments must end on the same local day.")
    availability = await get_availability(db, doctor_id)
    local_start = utc_to_local(start, zone)
    local_end = utc_to_local(end, zone)
    if _working_interval_for(availability["working_hours"], local_start, local_end) is None:
        raise ValidationFailed("The requested time is outside the doctor's working hours.")
    if await _time_off_overlaps(db, doctor_id, start, end):
        raise Conflict("The doctor is unavailable during that period.")
    if await _appointment_overlap(
        db,
        doctor_id=doctor_id,
        patient_id=patient_id,
        start=start,
        end=end,
        exclude_id=exclude_id,
    ):
        raise Conflict("That time conflicts with an existing appointment.")


async def create_appointment(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    start_at: datetime,
    duration_minutes: int,
    appointment_type: str | None,
    notes: str | None,
    status: str,
    created_by: uuid.UUID | None,
) -> Appointment:
    _validate_duration(duration_minutes)
    start = _as_utc(start_at)
    end = start + timedelta(minutes=duration_minutes)
    if status not in ("scheduled", "confirmed"):
        raise ValidationFailed("A new appointment must be scheduled or confirmed.")
    await ensure_doctor(db, doctor_id)
    if not await db.scalar(select(Patient.id).where(Patient.id == patient_id, Patient.not_deleted())):
        raise NotFound()
    await _acquire_locks(db, doctor_id, patient_id)
    await validate_slot(
        db,
        doctor_id=doctor_id,
        patient_id=patient_id,
        start_at=start,
        end_at=end,
    )
    row = Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        start_at=start,
        end_at=end,
        appointment_type=appointment_type,
        notes=notes,
        status=status,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def get_appointment(db: AsyncSession, appointment_id: uuid.UUID) -> Appointment | None:
    return await db.get(Appointment, appointment_id)


async def update_appointment(
    db: AsyncSession,
    row: Appointment,
    *,
    start_at: datetime | None,
    duration_minutes: int | None,
    appointment_type: str | None,
    notes: str | None,
    status: str | None,
) -> Appointment:
    if row.status in ("cancelled", "completed", "no_show"):
        raise Conflict("This appointment is closed and cannot be modified.")
    old_start = row.start_at
    old_end = row.end_at
    new_start = _as_utc(start_at) if start_at is not None else _as_utc(row.start_at)
    if duration_minutes is not None:
        _validate_duration(duration_minutes)
        new_end = new_start + timedelta(minutes=duration_minutes)
    elif start_at is not None:
        new_end = new_start + (row.end_at - row.start_at)
    else:
        new_end = _as_utc(row.end_at)

    if status is not None:
        if status not in APPOINTMENT_STATUSES or status == "cancelled":
            raise ValidationFailed("Use the cancel endpoint to cancel an appointment.")
        if row.status in ("cancelled", "completed", "no_show"):
            raise Conflict("This appointment is already closed.")
        if row.status == "scheduled" and status == "scheduled":
            pass
        elif row.status == "scheduled" and status not in ("confirmed", "completed", "no_show"):
            raise ValidationFailed("Invalid appointment status transition.")
        elif row.status == "confirmed" and status not in ("completed", "no_show"):
            raise ValidationFailed("Invalid appointment status transition.")

    rescheduled = new_start != old_start or new_end != old_end
    if rescheduled:
        await _acquire_locks(db, row.doctor_id, row.patient_id)
        await validate_slot(
            db,
            doctor_id=row.doctor_id,
            patient_id=row.patient_id,
            start_at=new_start,
            end_at=new_end,
            exclude_id=row.id,
        )
    row.start_at = new_start
    row.end_at = new_end
    if appointment_type is not None:
        row.appointment_type = appointment_type
    if notes is not None:
        row.notes = notes
    if status is not None:
        row.status = status
    await db.flush()
    return row


async def cancel_appointment(db: AsyncSession, row: Appointment) -> bool:
    if row.status == "cancelled":
        return False
    if row.status in ("completed", "no_show"):
        raise Conflict("A completed or no-show appointment cannot be cancelled.")
    row.status = "cancelled"
    await db.flush()
    return True


async def remove_from_calendar(db: AsyncSession, row: Appointment) -> bool:
    """Hide a cancelled appointment from calendar list responses.

    This is a visibility change, not a record deletion. Returns False when the
    appointment was already removed (idempotent); a non-cancelled appointment
    is a conflict because only closed cancelled rows may be hidden this way.
    """
    if row.status != "cancelled":
        raise Conflict("Only a cancelled appointment can be removed from the calendar.")
    if row.calendar_removed_at is not None:
        return False
    row.calendar_removed_at = utc_now()
    await db.flush()
    return True


async def add_appointment_event(
    db: AsyncSession,
    *,
    appointment_id: uuid.UUID,
    event_type: str,
    actor_user_id: uuid.UUID | None,
    request_id: str | None,
    old_start_at: datetime | None = None,
    old_end_at: datetime | None = None,
    new_start_at: datetime | None = None,
    new_end_at: datetime | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
) -> AppointmentEvent:
    """Append one immutable lifecycle event (never clinical notes)."""
    event = AppointmentEvent(
        appointment_id=appointment_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        old_start_at=_as_utc(old_start_at) if old_start_at is not None else None,
        old_end_at=_as_utc(old_end_at) if old_end_at is not None else None,
        new_start_at=_as_utc(new_start_at) if new_start_at is not None else None,
        new_end_at=_as_utc(new_end_at) if new_end_at is not None else None,
        old_status=old_status,
        new_status=new_status,
        request_id=request_id,
    )
    db.add(event)
    await db.flush()
    return event


async def list_appointment_events(
    db: AsyncSession, appointment_id: uuid.UUID, *, limit: int = 100
) -> list[tuple[AppointmentEvent, str | None]]:
    rows = (
        await db.execute(
            select(AppointmentEvent, StaffUser.display_name)
            .outerjoin(StaffUser, StaffUser.id == AppointmentEvent.actor_user_id)
            .where(AppointmentEvent.appointment_id == appointment_id)
            .order_by(AppointmentEvent.created_at, AppointmentEvent.id)
            .limit(limit)
        )
    ).all()
    return [(event, actor_name) for event, actor_name in rows]


async def list_appointments(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    doctor_id: uuid.UUID | None,
    patient_id: uuid.UUID | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Appointment, str, str, str]], int]:
    start = _as_utc(start)
    end = _as_utc(end)
    if end <= start:
        raise ValidationFailed("The appointment range end must be after start.")
    if end - start > timedelta(days=MAX_RANGE_DAYS):
        raise ValidationFailed(f"The appointment range may not exceed {MAX_RANGE_DAYS} days.")
    # Active calendar visibility: scheduled/confirmed rows remain until their
    # actual end_at. Completed/no-show rows are historical; cancelled rows keep
    # their existing five-minute calendar grace period. Explicit Admin removal
    # always hides a cancelled row. Nothing is deleted.
    now = utc_now()
    cancelled_cutoff = now - timedelta(minutes=CANCELLED_CALENDAR_VISIBLE_MINUTES)
    filters = [
        Appointment.start_at < end,
        Appointment.end_at > start,
        Appointment.patient_id.in_(
            select(Patient.id).where(Patient.not_deleted())
        ),
        Appointment.calendar_removed_at.is_(None),
        or_(
            and_(
                Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES),
                Appointment.end_at > now,
            ),
            and_(
                Appointment.status == "cancelled",
                Appointment.updated_at >= cancelled_cutoff,
            ),
        ),
    ]
    if doctor_id is not None:
        filters.append(Appointment.doctor_id == doctor_id)
    if patient_id is not None:
        filters.append(Appointment.patient_id == patient_id)
    if status is not None:
        if status not in APPOINTMENT_STATUSES:
            raise ValidationFailed("Unknown appointment status.")
        filters.append(Appointment.status == status)
    base = (
        select(
            Appointment,
            Patient.display_name.label("patient_display_name"),
            Patient.mrn.label("patient_mrn"),
            StaffUser.display_name.label("doctor_display_name"),
        )
        .join(Patient, Patient.id == Appointment.patient_id)
        .join(StaffUser, StaffUser.id == Appointment.doctor_id)
        .where(*filters)
    )
    total = (
        await db.scalar(
            select(func.count()).select_from(
                select(Appointment.id).where(*filters).subquery()
            )
        )
    ) or 0
    rows = list(
        (
            await db.execute(
                base.order_by(Appointment.start_at, Appointment.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return rows, int(total)


async def list_appointment_history(
    db: AsyncSession,
    *,
    start: datetime | None,
    end: datetime | None,
    doctor_id: uuid.UUID | None,
    patient_id: uuid.UUID | None,
    status: str | None,
    visit_type: str | None,
    query: str | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Appointment, str, str, str]], int]:
    """Bounded historical records, including past unmarked visits.

    The active-calendar 62-day range cap is deliberately not applied here. An
    optional date range is capped separately at 366 days; without it, pagination
    bounds the result. Rows are never removed by this query.
    """
    now = utc_now()
    if (start is None) != (end is None):
        raise ValidationFailed("History start and end must be provided together.")
    if start is not None and end is not None:
        start = _as_utc(start)
        end = _as_utc(end)
        if end <= start:
            raise ValidationFailed("The history range end must be after start.")
        if end - start > timedelta(days=HISTORY_MAX_RANGE_DAYS):
            raise ValidationFailed(
                f"The history range may not exceed {HISTORY_MAX_RANGE_DAYS} days."
            )
    if status is not None and status not in (*APPOINTMENT_STATUSES, PAST_UNMARKED):
        raise ValidationFailed("Unknown appointment status.")
    if visit_type is not None and len(visit_type) > 100:
        raise ValidationFailed("Visit type filter is too long.")
    search = query.strip() if query else None

    filters = [
        Appointment.patient_id.in_(select(Patient.id).where(Patient.not_deleted())),
        or_(
            Appointment.end_at <= now,
            Appointment.status.in_(("completed", "no_show", "cancelled")),
        ),
    ]
    if start is not None and end is not None:
        filters.append(Appointment.start_at >= start)
        filters.append(Appointment.start_at < end)
    if doctor_id is not None:
        filters.append(Appointment.doctor_id == doctor_id)
    if patient_id is not None:
        filters.append(Appointment.patient_id == patient_id)
    if status == PAST_UNMARKED:
        filters.append(Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES))
        filters.append(Appointment.end_at <= now)
    elif status is not None:
        filters.append(Appointment.status == status)
    if visit_type:
        filters.append(Appointment.appointment_type == visit_type)
    if search:
        pattern = _search_pattern(search)
        filters.append(
            or_(
                Patient.display_name.ilike(pattern, escape="\\"),
                Patient.mrn.ilike(pattern, escape="\\"),
            )
        )

    base = (
        select(
            Appointment,
            Patient.display_name.label("patient_display_name"),
            Patient.mrn.label("patient_mrn"),
            StaffUser.display_name.label("doctor_display_name"),
        )
        .join(Patient, Patient.id == Appointment.patient_id)
        .join(StaffUser, StaffUser.id == Appointment.doctor_id)
        .where(*filters)
    )
    total = (
        await db.scalar(
            select(func.count()).select_from(
                select(Appointment.id)
                .join(Patient, Patient.id == Appointment.patient_id)
                .where(*filters)
                .subquery()
            )
        )
    ) or 0
    rows = list(
        (
            await db.execute(
                base.order_by(Appointment.start_at.desc(), Appointment.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return rows, int(total)


async def get_appointment_with_names(
    db: AsyncSession, appointment_id: uuid.UUID
) -> tuple[Appointment, str, str, str] | None:
    row = (
        await db.execute(
            select(
                Appointment,
                Patient.display_name.label("patient_display_name"),
                Patient.mrn.label("patient_mrn"),
                StaffUser.display_name.label("doctor_display_name"),
            )
            .join(Patient, Patient.id == Appointment.patient_id)
            .join(StaffUser, StaffUser.id == Appointment.doctor_id)
            .where(Appointment.id == appointment_id, Patient.not_deleted())
        )
    ).first()
    return row


async def list_slots(
    db: AsyncSession,
    *,
    doctor_id: uuid.UUID,
    local_day: date,
) -> dict[str, Any]:
    availability = await get_availability(db, doctor_id)
    zone = clinic_zone()
    hours = availability["working_hours"]
    duration = int(availability["default_duration_minutes"])
    increment = int(availability["slot_increment_minutes"])
    if duration <= 0 or increment <= 0:
        return {"doctor_id": doctor_id, "date": local_day.isoformat(), "timezone": str(zone), "slots": []}
    day_hours = [row for row in hours if row.weekday == local_day.weekday()]
    appointments = list(
        (
            await db.scalars(
                select(Appointment).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES),
                    Appointment.start_at < datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc),
                    Appointment.end_at > datetime.combine(local_day, time.min, tzinfo=zone).astimezone(timezone.utc),
                )
            )
        ).all()
    )
    time_off = list(
        (
            await db.scalars(
                select(TimeOff).where(
                    TimeOff.doctor_id == doctor_id,
                    TimeOff.start_at < datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc),
                    TimeOff.end_at > datetime.combine(local_day, time.min, tzinfo=zone).astimezone(timezone.utc),
                )
            )
        ).all()
    )
    slots: list[dict[str, Any]] = []
    for interval in day_hours:
        cursor = datetime.combine(local_day, interval.start_time)
        end_limit = datetime.combine(local_day, interval.end_time)
        while cursor + timedelta(minutes=duration) <= end_limit:
            local_start = cursor
            local_end = cursor + timedelta(minutes=duration)
            try:
                start_utc = local_to_utc(local_start, zone)
                end_utc = local_to_utc(local_end, zone)
            except ValidationFailed:
                cursor += timedelta(minutes=increment)
                continue
            reason = "available"
            if start_utc <= utc_now():
                reason = "past"
            elif any(_overlaps(start_utc, end_utc, appt.start_at, appt.end_at) for appt in appointments):
                reason = "booked"
            elif any(_overlaps(start_utc, end_utc, off.start_at, off.end_at) for off in time_off):
                reason = "time_off"
            slots.append(
                {
                    "start_at": start_utc,
                    "end_at": end_utc,
                    "local_start": local_start.strftime("%H:%M"),
                    "local_end": local_end.strftime("%H:%M"),
                    "available": reason == "available",
                    "reason": reason,
                }
            )
            cursor += timedelta(minutes=increment)
    return {
        "doctor_id": doctor_id,
        "date": local_day.isoformat(),
        "timezone": str(zone),
        "slots": slots,
    }
