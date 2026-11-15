"""Appointment scheduling API and service tests.

Covers the security scoping rules (doctor -> own calendar only, Admin -> any
doctor, nurse -> none), availability/slot generation, conflict handling,
concurrent booking, Europe/Sofia DST conversion, lifecycle transitions, and
the audit events that document them.
"""
from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, ValidationFailed
from app.models import Appointment
from app.services import appointments as appointments_service
from conftest import audit_rows, csrf_headers

pytestmark = pytest.mark.postgres

CLINIC = ZoneInfo("Europe/Sofia")

WORKING_WEEK = {
    "default_duration_minutes": 30,
    "slot_increment_minutes": 30,
    "working_hours": [
        {"weekday": day, "start_time": "09:00:00", "end_time": "12:00:00"}
        for day in range(5)
    ],
    "time_off": [],
}


def _next_weekday(zone: ZoneInfo = CLINIC, *, days_ahead: int = 2) -> date:
    day = datetime.now(zone).date() + timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _utc_at(day: date, hour: int, minute: int = 0) -> datetime:
    return appointments_service.local_to_utc(datetime.combine(day, time(hour, minute)), CLINIC)


async def _put_working_week(client, *, doctor_id: uuid.UUID | None = None) -> dict:
    params = {"doctor_id": str(doctor_id)} if doctor_id is not None else None
    response = await client.put(
        "/api/v1/appointments/availability",
        params=params,
        json=WORKING_WEEK,
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create(
    client,
    *,
    patient_id,
    start_at,
    duration: int = 30,
    doctor_id=None,
    appointment_type=None,
    notes=None,
):
    body = {
        "patient_id": str(patient_id),
        "start_at": start_at.isoformat(),
        "duration_minutes": duration,
    }
    if doctor_id is not None:
        body["doctor_id"] = str(doctor_id)
    if appointment_type is not None:
        body["appointment_type"] = appointment_type
    if notes is not None:
        body["notes"] = notes
    return await client.post("/api/v1/appointments", json=body, headers=csrf_headers(client))


async def _move_appointment(
    db_engine,
    appointment_id,
    *,
    start_at,
    end_at,
    status=None,
    updated_at=None,
):
    values = {"start_at": start_at, "end_at": end_at}
    if status is not None:
        values["status"] = status
    if updated_at is not None:
        values["updated_at"] = updated_at
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        await db.execute(
            update(Appointment)
            .where(Appointment.id == uuid.UUID(str(appointment_id)))
            .values(**values)
        )
        await db.commit()


async def _seed_availability(db, doctor_id: uuid.UUID) -> None:
    await appointments_service.replace_availability(
        db,
        doctor_id=doctor_id,
        default_duration_minutes=30,
        slot_increment_minutes=30,
        working_hours=[
            SimpleNamespace(weekday=day, start_time=time(9, 0), end_time=time(12, 0))
            for day in range(5)
        ],
        time_off=[],
        created_by=doctor_id,
    )
    await db.commit()


async def test_availability_roundtrip_and_slot_generation(
    make_user, auth_client
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)

    created = await _put_working_week(client)
    assert created["timezone"] == "Europe/Sofia"
    assert created["doctor_id"] == str(doctor.id)
    assert created["default_duration_minutes"] == 30
    assert len(created["working_hours"]) == 5

    fetched = await client.get("/api/v1/appointments/availability")
    assert fetched.status_code == 200
    assert fetched.json()["working_hours"] == created["working_hours"]

    # Overlapping intervals for one weekday are refused deterministically.
    overlapping = copy.deepcopy(WORKING_WEEK)
    overlapping["working_hours"].append(
        {"weekday": 0, "start_time": "11:00:00", "end_time": "13:00:00"}
    )
    response = await client.put(
        "/api/v1/appointments/availability",
        json=overlapping,
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"

    # A 09:00-12:00 window with 30-minute slots yields exactly six slots.
    day = _next_weekday()
    slots = await client.get("/api/v1/appointments/slots", params={"date": day.isoformat()})
    assert slots.status_code == 200, slots.text
    payload = slots.json()
    assert [slot["local_start"] for slot in payload["slots"]] == [
        "09:00",
        "09:30",
        "10:00",
        "10:30",
        "11:00",
        "11:30",
    ]
    assert all(slot["available"] for slot in payload["slots"])
    assert payload["slots"][0]["start_at"] == _utc_at(day, 9).isoformat().replace("+00:00", "Z")


async def test_create_marks_slot_booked_and_rejects_conflicts(
    make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _put_working_week(client)
    day = _next_weekday()

    first = await _create(
        client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9, 30)
    )
    assert first.status_code == 201, first.text
    appointment = first.json()
    assert appointment["status"] == "scheduled"
    assert appointment["duration_minutes"] == 30
    assert appointment["patient_mrn"] == clinical_data.patient_mrn
    assert appointment["doctor_id"] == str(doctor.id)

    slots = await client.get("/api/v1/appointments/slots", params={"date": day.isoformat()})
    by_local = {slot["local_start"]: slot for slot in slots.json()["slots"]}
    assert by_local["09:30"]["available"] is False
    assert by_local["09:30"]["reason"] == "booked"
    assert by_local["10:00"]["available"] is True

    overlap = await _create(
        client, patient_id=clinical_data.empty_patient_id, start_at=_utc_at(day, 9, 30)
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "conflict"

    outside = await _create(
        client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 12, 0)
    )
    assert outside.status_code == 400
    assert outside.json()["error"]["code"] == "validation_error"

    past = datetime.now(timezone.utc) - timedelta(days=1)
    past_response = await _create(client, patient_id=clinical_data.patient_id, start_at=past)
    assert past_response.status_code == 400

    window_start = datetime.combine(day, time.min, tzinfo=CLINIC).astimezone(timezone.utc)
    listed = await client.get(
        "/api/v1/appointments",
        params={
            "start": window_start.isoformat(),
            "end": (window_start + timedelta(days=1)).isoformat(),
        },
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [appointment["id"]]


async def test_patient_cannot_be_double_booked_across_doctors(
    make_user, auth_client, clinical_data
) -> None:
    first_doctor = await make_user(roles=("doctor",))
    second_doctor = await make_user(roles=("doctor",))
    first_client = await auth_client(first_doctor)
    second_client = await auth_client(second_doctor)
    await _put_working_week(first_client)
    await _put_working_week(second_client)
    day = _next_weekday()

    created = await _create(
        first_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9, 30)
    )
    assert created.status_code == 201, created.text

    conflict = await _create(
        second_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9, 30)
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"


async def test_doctor_owns_only_their_calendar(
    make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    other_doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    client = await auth_client(doctor)
    other_client = await auth_client(other_doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    await _put_working_week(client)
    day = _next_weekday()

    created = await _create(
        client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9, 30)
    )
    appointment_id = created.json()["id"]

    assert (
        await other_client.get(f"/api/v1/appointments/{appointment_id}")
    ).status_code == 404
    assert (
        await other_client.patch(
            f"/api/v1/appointments/{appointment_id}",
            json={"notes": "not mine"},
            headers=csrf_headers(other_client),
        )
    ).status_code == 404
    assert (
        await other_client.post(
            f"/api/v1/appointments/{appointment_id}/cancel",
            headers=csrf_headers(other_client),
        )
    ).status_code == 404
    # A doctor can never write into another doctor's calendar.
    assert (
        await _create(
            other_client,
            patient_id=clinical_data.patient_id,
            start_at=_utc_at(day, 10, 30),
            doctor_id=doctor.id,
        )
    ).status_code == 403

    # Admin sees and can act on any doctor's calendar.
    assert (
        await admin_client.get(f"/api/v1/appointments/{appointment_id}")
    ).status_code == 200
    assert (
        await admin_client.get(
            "/api/v1/appointments/availability", params={"doctor_id": str(doctor.id)}
        )
    ).status_code == 200
    assert (
        await admin_client.get(
            "/api/v1/appointments/slots",
            params={"doctor_id": str(doctor.id), "date": day.isoformat()},
        )
    ).status_code == 200
    admin_create = await _create(
        admin_client,
        patient_id=clinical_data.empty_patient_id,
        start_at=_utc_at(day, 11, 0),
        doctor_id=doctor.id,
    )
    assert admin_create.status_code == 201, admin_create.text
    # Admin must choose a doctor explicitly for scoped operations.
    assert (
        await admin_client.get("/api/v1/appointments/availability")
    ).status_code == 400

    # Nurse now has read-only clinic-wide appointments; writes stay forbidden.
    nurse_list = await nurse_client.get(
        "/api/v1/appointments",
        params={
            "start": _utc_at(day, 0).isoformat(),
            "end": _utc_at(day, 23, 59).isoformat(),
        },
    )
    assert nurse_list.status_code == 200, nurse_list.text
    nurse_ids = {item["id"] for item in nurse_list.json()["items"]}
    assert appointment_id in nurse_ids
    assert (
        await nurse_client.get(f"/api/v1/appointments/{appointment_id}")
    ).status_code == 200
    assert (await nurse_client.get("/api/v1/appointments/config")).status_code == 200
    assert (
        await _create(
            nurse_client,
            patient_id=clinical_data.patient_id,
            start_at=_utc_at(day, 11, 30),
        )
    ).status_code == 403
    assert (
        await nurse_client.put(
            "/api/v1/appointments/availability",
            json=WORKING_WEEK,
            headers=csrf_headers(nurse_client),
        )
    ).status_code == 403


async def test_lifecycle_transitions_and_audit_events(
    make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _put_working_week(client)
    day = _next_weekday()

    created = await _create(
        client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9, 30)
    )
    assert created.status_code == 201, created.text
    appointment_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/appointments/{appointment_id}",
        json={"notes": "Follow-up visit"},
        headers=csrf_headers(client),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["notes"] == "Follow-up visit"

    rescheduled = await client.patch(
        f"/api/v1/appointments/{appointment_id}",
        json={"start_at": _utc_at(day, 10, 30).isoformat()},
        headers=csrf_headers(client),
    )
    assert rescheduled.status_code == 200, rescheduled.text
    assert rescheduled.json()["start_at"] == _utc_at(day, 10, 30).isoformat().replace(
        "+00:00", "Z"
    )

    cancelled = await client.post(
        f"/api/v1/appointments/{appointment_id}/cancel", headers=csrf_headers(client)
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    # Cancellation is idempotent; a closed appointment cannot be edited.
    again = await client.post(
        f"/api/v1/appointments/{appointment_id}/cancel", headers=csrf_headers(client)
    )
    assert again.status_code == 200
    closed_edit = await client.patch(
        f"/api/v1/appointments/{appointment_id}",
        json={"notes": "too late"},
        headers=csrf_headers(client),
    )
    assert closed_edit.status_code == 409
    assert closed_edit.json()["error"]["code"] == "conflict"

    completed = await _create(
        client, patient_id=clinical_data.empty_patient_id, start_at=_utc_at(day, 9, 0)
    )
    completed_id = completed.json()["id"]
    assert (
        await client.patch(
            f"/api/v1/appointments/{completed_id}",
            json={"status": "completed"},
            headers=csrf_headers(client),
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/appointments/{completed_id}/cancel", headers=csrf_headers(client)
        )
    ).status_code == 409

    emitted = {
        row.action
        for row in await audit_rows(resource_id=str(appointment_id))
    }
    assert {
        "appointment.create",
        "appointment.update",
        "appointment.reschedule",
        "appointment.cancel",
    } <= emitted
    for row in await audit_rows(resource_id=str(appointment_id)):
        if row.action.startswith("appointment."):
            assert row.actor_user_id == doctor.id
            assert row.request_id
            assert row.ip is not None

    availability_events = await audit_rows(resource_id=str(doctor.id))
    assert any(row.action == "appointment.availability.update" for row in availability_events)


async def test_admin_remove_and_five_minute_cancelled_calendar_visibility(
    db_engine, make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    await _put_working_week(doctor_client)
    day = _next_weekday()

    async def create(
        hour: int, minute: int = 0, patient_id=clinical_data.patient_id
    ) -> str:
        response = await _create(
            doctor_client, patient_id=patient_id, start_at=_utc_at(day, hour, minute)
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    manual_id = await create(9)
    auto_id = await create(9, 30, clinical_data.empty_patient_id)
    scheduled_id = await create(10)
    for appointment_id in (manual_id, auto_id):
        cancelled = await doctor_client.post(
            f"/api/v1/appointments/{appointment_id}/cancel",
            headers=csrf_headers(doctor_client),
        )
        assert cancelled.status_code == 200, cancelled.text

    params = {
        "start": _utc_at(day, 0).isoformat(),
        "end": _utc_at(day, 23, 59).isoformat(),
    }
    before = await doctor_client.get("/api/v1/appointments", params=params)
    assert before.status_code == 200
    before_ids = {item["id"] for item in before.json()["items"]}
    assert {manual_id, auto_id, scheduled_id} <= before_ids

    # Only an Admin may remove a cancelled calendar entry.
    for client in (doctor_client, nurse_client):
        denied = await client.post(
            f"/api/v1/appointments/{manual_id}/remove-from-calendar",
            headers=csrf_headers(client),
        )
        assert denied.status_code == 403, denied.text

    # Only a cancelled appointment may be removed.
    not_cancelled = await admin_client.post(
        f"/api/v1/appointments/{scheduled_id}/remove-from-calendar",
        headers=csrf_headers(admin_client),
    )
    assert not_cancelled.status_code == 409
    assert not_cancelled.json()["error"]["code"] == "conflict"

    removed = await admin_client.post(
        f"/api/v1/appointments/{manual_id}/remove-from-calendar",
        headers=csrf_headers(admin_client),
    )
    assert removed.status_code == 204, removed.text
    again = await admin_client.post(
        f"/api/v1/appointments/{manual_id}/remove-from-calendar",
        headers=csrf_headers(admin_client),
    )
    assert again.status_code == 204, again.text

    after_remove = await doctor_client.get("/api/v1/appointments", params=params)
    after_ids = {item["id"] for item in after_remove.json()["items"]}
    assert manual_id not in after_ids
    assert auto_id in after_ids  # still within the 5-minute window
    assert scheduled_id in after_ids

    # The record is retained and remains directly readable.
    retained = await doctor_client.get(f"/api/v1/appointments/{manual_id}")
    assert retained.status_code == 200
    assert retained.json()["status"] == "cancelled"

    # Auto-hide after 5 minutes for cancelled rows only.
    old = datetime.now(timezone.utc) - timedelta(
        minutes=appointments_service.CANCELLED_CALENDAR_VISIBLE_MINUTES + 1
    )
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        await db.execute(
            update(Appointment)
            .where(Appointment.id.in_([uuid.UUID(auto_id), uuid.UUID(scheduled_id)]))
            .values(updated_at=old)
        )
        await db.commit()
    after_expiry = await doctor_client.get("/api/v1/appointments", params=params)
    expiry_ids = {item["id"] for item in after_expiry.json()["items"]}
    assert auto_id not in expiry_ids
    assert scheduled_id in expiry_ids  # non-cancelled rows are never auto-hidden

    emitted = await audit_rows(
        action="appointment.remove_from_calendar", actor_user_id=admin.id
    )
    assert len(emitted) == 1
    assert emitted[0].resource_id == manual_id
    assert emitted[0].request_id is not None


async def test_soft_deleted_patient_hides_appointments(
    make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    doctor_client = await auth_client(doctor)
    admin_client = await auth_client(admin)
    await _put_working_week(doctor_client)
    day = _next_weekday()
    created = await _create(
        doctor_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9)
    )
    assert created.status_code == 201, created.text
    appointment_id = created.json()["id"]

    deleted = await admin_client.delete(
        f"/api/v1/patients/{clinical_data.patient_id}", headers=csrf_headers(admin_client)
    )
    assert deleted.status_code == 204, deleted.text

    listed = await doctor_client.get(
        "/api/v1/appointments",
        params={
            "start": _utc_at(day, 0).isoformat(),
            "end": _utc_at(day, 23, 59).isoformat(),
        },
    )
    assert listed.status_code == 200
    assert appointment_id not in {item["id"] for item in listed.json()["items"]}
    hidden = await doctor_client.get(f"/api/v1/appointments/{appointment_id}")
    assert hidden.status_code == 404


async def test_list_rejects_unbounded_or_invalid_ranges(
    make_user, auth_client
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    now = datetime.now(timezone.utc)

    too_wide = await client.get(
        "/api/v1/appointments",
        params={
            "start": now.isoformat(),
            "end": (now + timedelta(days=63)).isoformat(),
        },
    )
    assert too_wide.status_code == 400

    backwards = await client.get(
        "/api/v1/appointments",
        params={
            "start": (now + timedelta(days=1)).isoformat(),
            "end": now.isoformat(),
        },
    )
    assert backwards.status_code == 400

    bad_status = await client.get(
        "/api/v1/appointments",
        params={
            "start": now.isoformat(),
            "end": (now + timedelta(days=7)).isoformat(),
            "status": "deleted",
        },
    )
    assert bad_status.status_code == 400

    naive = await client.get(
        "/api/v1/appointments",
        params={
            "start": now.replace(tzinfo=None).isoformat(),
            "end": (now + timedelta(days=7)).replace(tzinfo=None).isoformat(),
        },
    )
    assert naive.status_code == 400


def test_europe_sofia_dst_holes_and_folds_are_rejected() -> None:
    # Spring forward: 03:00 EET jumps to 04:00 EEST, so 03:30 does not exist.
    with pytest.raises(ValidationFailed):
        appointments_service.local_to_utc(datetime(2026, 3, 29, 3, 30), CLINIC)
    # Fall back: 04:00 EEST returns to 03:00 EET, so 03:30 happens twice.
    with pytest.raises(ValidationFailed):
        appointments_service.local_to_utc(datetime(2026, 10, 25, 3, 30), CLINIC)

    # Unambiguous local times convert with the correct offset (UTC+2 / UTC+3).
    assert appointments_service.local_to_utc(
        datetime(2026, 3, 29, 1, 30), CLINIC
    ) == datetime(2026, 3, 28, 23, 30, tzinfo=timezone.utc)
    assert appointments_service.local_to_utc(
        datetime(2026, 10, 25, 1, 30), CLINIC
    ) == datetime(2026, 10, 24, 22, 30, tzinfo=timezone.utc)
    assert appointments_service.local_to_utc(
        datetime(2026, 7, 1, 12, 0), CLINIC
    ) == datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    assert appointments_service.local_to_utc(
        datetime(2026, 12, 1, 12, 0), CLINIC
    ) == datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)


async def test_utc_request_times_are_interpreted_in_clinic_timezone(
    make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _put_working_week(client)

    summer_day = date(2027, 7, 5)  # Monday, 10:00 EEST == 07:00 UTC
    summer = await _create(
        client, patient_id=clinical_data.patient_id, start_at=_utc_at(summer_day, 10, 0)
    )
    assert summer.status_code == 201, summer.text
    assert summer.json()["start_at"] == "2027-07-05T07:00:00Z"

    winter_day = date(2027, 1, 5)  # Tuesday, 10:00 EET == 08:00 UTC
    winter = await _create(
        client, patient_id=clinical_data.empty_patient_id, start_at=_utc_at(winter_day, 10, 0)
    )
    assert winter.status_code == 201, winter.text
    assert winter.json()["start_at"] == "2027-01-05T08:00:00Z"


async def test_history_boundaries_normalize_clinic_offsets_across_dst(
    make_user, auth_client, clinical_data
) -> None:
    """History compares UTC while accepting explicitly offset clinic bounds."""
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _put_working_week(client)

    # Monday after the Europe/Sofia spring-forward: 10:00 EEST == 07:00 UTC.
    day = date(2027, 3, 29)
    created = await _create(
        client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 10, 0)
    )
    assert created.status_code == 201, created.text
    assert created.json()["start_at"] == "2027-03-29T07:00:00Z"
    appointment_id = created.json()["id"]
    closed = await client.patch(
        f"/api/v1/appointments/{appointment_id}",
        json={"status": "completed"},
        headers=csrf_headers(client),
    )
    assert closed.status_code == 200, closed.text

    # A range spanning the offset change is normalized to UTC.
    spanning = await client.get(
        "/api/v1/appointments/history",
        params={
            "start": "2027-03-28T00:00:00+02:00",
            "end": "2027-03-30T00:00:00+03:00",
        },
    )
    assert spanning.status_code == 200, spanning.text
    assert appointment_id in {item["id"] for item in spanning.json()["items"]}

    # Start is inclusive: an offset bound exactly at start_at includes the row.
    inclusive = await client.get(
        "/api/v1/appointments/history",
        params={
            "start": "2027-03-29T10:00:00+03:00",
            "end": "2027-03-29T10:01:00+03:00",
        },
    )
    assert inclusive.status_code == 200, inclusive.text
    assert appointment_id in {item["id"] for item in inclusive.json()["items"]}

    # End is exclusive: the same instant as end excludes the row.
    exclusive = await client.get(
        "/api/v1/appointments/history",
        params={
            "start": "2027-03-28T00:00:00+02:00",
            "end": "2027-03-29T10:00:00+03:00",
        },
    )
    assert exclusive.status_code == 200, exclusive.text
    assert appointment_id not in {item["id"] for item in exclusive.json()["items"]}


async def test_active_calendar_expires_at_actual_end_and_history_retains_record(
    db_engine, make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    await _put_working_week(doctor_client)
    day = _next_weekday()

    in_progress = await _create(
        doctor_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9)
    )
    past_unmarked = await _create(
        doctor_client, patient_id=clinical_data.empty_patient_id, start_at=_utc_at(day, 9, 30)
    )
    future = await _create(
        doctor_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 10)
    )
    # 60-minute appointments prove expiry uses each row's real end_at rather
    # than a global/default 30-minute assumption. They live on the next
    # working day so they do not overlap the 30-minute rows above.
    long_day = day + timedelta(days=1)
    while long_day.weekday() >= 5:
        long_day += timedelta(days=1)
    long_in_progress = await _create(
        doctor_client,
        patient_id=clinical_data.patient_id,
        start_at=_utc_at(long_day, 9),
        duration=60,
    )
    long_past = await _create(
        doctor_client,
        patient_id=clinical_data.empty_patient_id,
        start_at=_utc_at(long_day, 10, 30),
        duration=60,
    )
    for response in (in_progress, past_unmarked, future, long_in_progress, long_past):
        assert response.status_code == 201, response.text

    now = datetime.now(timezone.utc)
    await _move_appointment(
        db_engine,
        in_progress.json()["id"],
        start_at=now - timedelta(minutes=10),
        end_at=now + timedelta(minutes=20),
        status="confirmed",
    )
    await _move_appointment(
        db_engine,
        past_unmarked.json()["id"],
        start_at=now - timedelta(minutes=60),
        end_at=now - timedelta(minutes=30),
        status="scheduled",
    )
    await _move_appointment(
        db_engine,
        future.json()["id"],
        start_at=now + timedelta(minutes=60),
        end_at=now + timedelta(minutes=90),
    )
    await _move_appointment(
        db_engine,
        long_in_progress.json()["id"],
        start_at=now - timedelta(minutes=40),
        end_at=now + timedelta(minutes=20),
    )
    await _move_appointment(
        db_engine,
        long_past.json()["id"],
        start_at=now - timedelta(minutes=70),
        end_at=now - timedelta(minutes=10),
    )

    active_params = {
        "start": (now - timedelta(days=1)).isoformat(),
        "end": (now + timedelta(days=1)).isoformat(),
    }
    active_ids = {}
    for label, client in (
        ("doctor", doctor_client),
        ("admin", admin_client),
        ("nurse", nurse_client),
    ):
        response = await client.get("/api/v1/appointments", params=active_params)
        assert response.status_code == 200, (label, response.text)
        ids = {item["id"] for item in response.json()["items"]}
        active_ids[label] = ids
        assert in_progress.json()["id"] in ids, label
        assert future.json()["id"] in ids, label
        assert long_in_progress.json()["id"] in ids, label
        assert past_unmarked.json()["id"] not in ids, label
        assert long_past.json()["id"] not in ids, label

    history_params = {
        "start": (now - timedelta(days=2)).isoformat(),
        "end": (now + timedelta(days=1)).isoformat(),
    }
    for label, client in (
        ("doctor", doctor_client),
        ("admin", admin_client),
        ("nurse", nurse_client),
    ):
        response = await client.get("/api/v1/appointments/history", params=history_params)
        assert response.status_code == 200, (label, response.text)
        items = response.json()["items"]
        ids = {item["id"] for item in items}
        assert past_unmarked.json()["id"] in ids, label
        assert long_past.json()["id"] in ids, label
        assert in_progress.json()["id"] not in ids, label
        assert future.json()["id"] not in ids, label
        assert long_in_progress.json()["id"] not in ids, label
        item = next(item for item in items if item["id"] == past_unmarked.json()["id"])
        assert item["status"] == "scheduled"
        assert item["patient_display_name"] == clinical_data.empty_patient_name

    retained = await doctor_client.get(
        f"/api/v1/appointments/{past_unmarked.json()['id']}"
    )
    assert retained.status_code == 200
    assert retained.json()["status"] == "scheduled"

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        row = await db.get(Appointment, uuid.UUID(past_unmarked.json()["id"]))
    assert row is not None
    assert row.status == "scheduled"


async def test_cancelled_past_appointment_keeps_five_minute_calendar_visibility(
    db_engine, make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    doctor_client = await auth_client(doctor)
    admin_client = await auth_client(admin)
    await _put_working_week(doctor_client)
    day = _next_weekday()
    created = await _create(
        doctor_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9)
    )
    assert created.status_code == 201, created.text
    appointment_id = created.json()["id"]
    cancelled = await doctor_client.post(
        f"/api/v1/appointments/{appointment_id}/cancel",
        headers=csrf_headers(doctor_client),
    )
    assert cancelled.status_code == 200, cancelled.text

    now = datetime.now(timezone.utc)
    active_params = {
        "start": (now - timedelta(days=1)).isoformat(),
        "end": (now + timedelta(days=1)).isoformat(),
    }
    history_params = {
        "start": (now - timedelta(days=2)).isoformat(),
        "end": (now + timedelta(days=1)).isoformat(),
    }
    await _move_appointment(
        db_engine,
        appointment_id,
        start_at=now - timedelta(hours=2),
        end_at=now - timedelta(hours=1),
        updated_at=now,
    )
    active = await doctor_client.get("/api/v1/appointments", params=active_params)
    assert appointment_id in {item["id"] for item in active.json()["items"]}
    history = await doctor_client.get("/api/v1/appointments/history", params=history_params)
    assert appointment_id in {item["id"] for item in history.json()["items"]}

    await _move_appointment(
        db_engine,
        appointment_id,
        start_at=now - timedelta(hours=2),
        end_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=6),
    )
    active = await doctor_client.get("/api/v1/appointments", params=active_params)
    assert appointment_id not in {item["id"] for item in active.json()["items"]}
    history = await doctor_client.get("/api/v1/appointments/history", params=history_params)
    assert appointment_id in {item["id"] for item in history.json()["items"]}

    removed = await admin_client.post(
        f"/api/v1/appointments/{appointment_id}/remove-from-calendar",
        headers=csrf_headers(admin_client),
    )
    assert removed.status_code == 204, removed.text
    history = await doctor_client.get("/api/v1/appointments/history", params=history_params)
    assert appointment_id in {item["id"] for item in history.json()["items"]}
    retained = await doctor_client.get(f"/api/v1/appointments/{appointment_id}")
    assert retained.status_code == 200
    assert retained.json()["status"] == "cancelled"
    assert retained.json()["calendar_removed_at"] is not None


async def test_appointment_status_actions_audit_and_lifecycle(
    make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    await _put_working_week(doctor_client)
    day = _next_weekday()

    first = await _create(
        doctor_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 9)
    )
    second = await _create(
        doctor_client, patient_id=clinical_data.empty_patient_id, start_at=_utc_at(day, 9, 30)
    )
    third = await _create(
        doctor_client, patient_id=clinical_data.patient_id, start_at=_utc_at(day, 10)
    )
    assert first.status_code == second.status_code == third.status_code == 201

    completed = await doctor_client.patch(
        f"/api/v1/appointments/{first.json()['id']}",
        json={"status": "completed"},
        headers=csrf_headers(doctor_client),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    completed_rows = await audit_rows(
        action="appointment.complete", resource_id=first.json()["id"]
    )
    assert len(completed_rows) == 1
    assert completed_rows[0].actor_user_id == doctor.id

    lifecycle = await doctor_client.get(
        f"/api/v1/appointments/{first.json()['id']}/history"
    )
    assert lifecycle.status_code == 200, lifecycle.text
    events = lifecycle.json()
    assert [event["event_type"] for event in events] == ["created", "status_changed"]
    assert events[1]["old_status"] == "scheduled"
    assert events[1]["new_status"] == "completed"
    assert events[1]["actor_user_id"] == str(doctor.id)
    assert events[1]["actor_display_name"]
    assert events[1]["request_id"]

    no_show = await doctor_client.patch(
        f"/api/v1/appointments/{second.json()['id']}",
        json={"status": "no_show"},
        headers=csrf_headers(doctor_client),
    )
    assert no_show.status_code == 200, no_show.text
    assert no_show.json()["status"] == "no_show"
    no_show_rows = await audit_rows(
        action="appointment.no_show", resource_id=second.json()["id"]
    )
    assert len(no_show_rows) == 1

    nurse_denied = await nurse_client.patch(
        f"/api/v1/appointments/{third.json()['id']}",
        json={"status": "completed"},
        headers=csrf_headers(nurse_client),
    )
    assert nurse_denied.status_code == 403

    admin_completed = await admin_client.patch(
        f"/api/v1/appointments/{third.json()['id']}",
        json={"status": "completed"},
        headers=csrf_headers(admin_client),
    )
    assert admin_completed.status_code == 200, admin_completed.text
    assert admin_completed.json()["status"] == "completed"

    closed_again = await doctor_client.patch(
        f"/api/v1/appointments/{first.json()['id']}",
        json={"status": "no_show"},
        headers=csrf_headers(doctor_client),
    )
    assert closed_again.status_code == 409


async def test_history_filters_pagination_and_role_scope(
    db_engine, make_user, auth_client, clinical_data
) -> None:
    doctor_a = await make_user(roles=("doctor",), display_name="000 History Doctor A")
    doctor_b = await make_user(roles=("doctor",), display_name="001 History Doctor B")
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    client_a = await auth_client(doctor_a)
    client_b = await auth_client(doctor_b)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    await _put_working_week(client_a)
    await _put_working_week(client_b)
    day = _next_weekday()

    first = await _create(
        client_a,
        patient_id=clinical_data.patient_id,
        start_at=_utc_at(day, 9),
        appointment_type="General Consultation",
    )
    second = await _create(
        client_a,
        patient_id=clinical_data.empty_patient_id,
        start_at=_utc_at(day, 9, 30),
        appointment_type="Follow-up Visit",
    )
    other = await _create(
        client_b,
        patient_id=clinical_data.empty_patient_id,
        start_at=_utc_at(day, 9),
        appointment_type="General Consultation",
    )
    assert first.status_code == second.status_code == other.status_code == 201

    now = datetime.now(timezone.utc)
    await _move_appointment(
        db_engine,
        first.json()["id"],
        start_at=now - timedelta(days=10),
        end_at=now - timedelta(days=10) + timedelta(minutes=30),
        status="completed",
    )
    await _move_appointment(
        db_engine,
        second.json()["id"],
        start_at=now - timedelta(days=40),
        end_at=now - timedelta(days=40) + timedelta(minutes=30),
        status="scheduled",
    )
    await _move_appointment(
        db_engine,
        other.json()["id"],
        start_at=now - timedelta(days=10),
        end_at=now - timedelta(days=10) + timedelta(minutes=30),
        status="no_show",
    )

    own = await client_a.get("/api/v1/appointments/history")
    assert own.status_code == 200, own.text
    own_ids = {item["id"] for item in own.json()["items"]}
    assert {first.json()["id"], second.json()["id"]} <= own_ids
    assert other.json()["id"] not in own_ids
    assert own.json()["total"] == 2

    past_unmarked = await client_a.get(
        "/api/v1/appointments/history", params={"status": "past_unmarked"}
    )
    assert past_unmarked.status_code == 200
    assert [item["id"] for item in past_unmarked.json()["items"]] == [second.json()["id"]]

    completed = await client_a.get(
        "/api/v1/appointments/history", params={"status": "completed"}
    )
    assert [item["id"] for item in completed.json()["items"]] == [first.json()["id"]]

    by_type = await client_a.get(
        "/api/v1/appointments/history",
        params={"visit_type": "General Consultation"},
    )
    assert [item["id"] for item in by_type.json()["items"]] == [first.json()["id"]]

    by_patient = await client_a.get(
        "/api/v1/appointments/history", params={"q": clinical_data.patient_mrn}
    )
    assert [item["id"] for item in by_patient.json()["items"]] == [first.json()["id"]]

    by_date = await client_a.get(
        "/api/v1/appointments/history",
        params={
            "start": (now - timedelta(days=20)).isoformat(),
            "end": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert [item["id"] for item in by_date.json()["items"]] == [first.json()["id"]]

    page_one = await client_a.get(
        "/api/v1/appointments/history", params={"limit": 1, "offset": 0}
    )
    assert page_one.json()["total"] == 2
    assert len(page_one.json()["items"]) == 1
    page_two = await client_a.get(
        "/api/v1/appointments/history", params={"limit": 1, "offset": 1}
    )
    assert len(page_two.json()["items"]) == 1
    assert page_two.json()["items"][0]["id"] != page_one.json()["items"][0]["id"]

    admin_all = await admin_client.get("/api/v1/appointments/history")
    admin_ids = {item["id"] for item in admin_all.json()["items"]}
    assert {first.json()["id"], second.json()["id"], other.json()["id"]} <= admin_ids

    admin_scoped = await admin_client.get(
        "/api/v1/appointments/history", params={"doctor_id": str(doctor_b.id)}
    )
    assert {item["id"] for item in admin_scoped.json()["items"]} == {other.json()["id"]}

    nurse_all = await nurse_client.get("/api/v1/appointments/history")
    nurse_ids = {item["id"] for item in nurse_all.json()["items"]}
    assert {first.json()["id"], second.json()["id"], other.json()["id"]} <= nurse_ids
    nurse_scoped = await nurse_client.get(
        "/api/v1/appointments/history", params={"doctor_id": str(doctor_a.id)}
    )
    assert {item["id"] for item in nurse_scoped.json()["items"]} == {
        first.json()["id"],
        second.json()["id"],
    }

    other_history = await client_b.get("/api/v1/appointments/history")
    assert {item["id"] for item in other_history.json()["items"]} == {other.json()["id"]}
    denied_detail = await client_a.get(f"/api/v1/appointments/{other.json()['id']}")
    assert denied_detail.status_code == 404
    denied_lifecycle = await client_a.get(
        f"/api/v1/appointments/{other.json()['id']}/history"
    )
    assert denied_lifecycle.status_code == 404


async def test_history_export_is_role_scoped_and_audited(
    db_engine, make_user, auth_client, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    other_doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    other_client = await auth_client(other_doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    await _put_working_week(doctor_client)
    await _put_working_week(other_client)
    day = _next_weekday()

    owned = await _create(
        doctor_client,
        patient_id=clinical_data.patient_id,
        start_at=_utc_at(day, 9),
        appointment_type="General Consultation",
        notes="SENSITIVE-NOT-IN-EXPORT",
    )
    unmarked = await _create(
        doctor_client,
        patient_id=clinical_data.patient_id,
        start_at=_utc_at(day, 9, 30),
        appointment_type="Follow-up Visit",
    )
    other = await _create(
        other_client,
        patient_id=clinical_data.empty_patient_id,
        start_at=_utc_at(day, 9),
        appointment_type="General Consultation",
    )
    assert owned.status_code == unmarked.status_code == other.status_code == 201

    now = datetime.now(timezone.utc)
    for appointment_id, status in (
        (owned.json()["id"], "completed"),
        (unmarked.json()["id"], "scheduled"),
        (other.json()["id"], "no_show"),
    ):
        await _move_appointment(
            db_engine,
            appointment_id,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=10) + timedelta(minutes=30),
            status=status,
        )

    params = {
        "start": (now - timedelta(days=20)).isoformat(),
        "end": (now - timedelta(days=1)).isoformat(),
    }
    exported = await doctor_client.get(
        "/api/v1/appointments/history/export.csv", params=params
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    csv_text = exported.text
    assert clinical_data.patient_name in csv_text
    assert clinical_data.patient_mrn in csv_text
    assert "Completed" in csv_text
    assert "Past — Status not updated" in csv_text
    assert "SENSITIVE-NOT-IN-EXPORT" not in csv_text
    assert clinical_data.empty_patient_name not in csv_text
    assert other.json()["id"] not in csv_text

    admin_export = await admin_client.get(
        "/api/v1/appointments/history/export.csv", params=params
    )
    assert admin_export.status_code == 200
    assert clinical_data.empty_patient_name in admin_export.text
    assert other.json()["id"] in admin_export.text

    nurse_denied = await nurse_client.get(
        "/api/v1/appointments/history/export.csv", params=params
    )
    assert nurse_denied.status_code == 403
    doctor_scoped_other = await doctor_client.get(
        "/api/v1/appointments/history/export.csv",
        params={**params, "doctor_id": str(other_doctor.id)},
    )
    assert doctor_scoped_other.status_code == 403
    missing_range = await doctor_client.get("/api/v1/appointments/history/export.csv")
    assert missing_range.status_code == 400

    too_wide = await doctor_client.get(
        "/api/v1/appointments/history/export.csv",
        params={
            "start": now.isoformat(),
            "end": (now + timedelta(days=367)).isoformat(),
        },
    )
    assert too_wide.status_code == 400
    bad_status = await doctor_client.get(
        "/api/v1/appointments/history/export.csv",
        params={**params, "status": "deleted"},
    )
    assert bad_status.status_code == 400

    exported_rows = await audit_rows(action="appointment.history.export")
    assert len(exported_rows) >= 2
    assert {row.actor_user_id for row in exported_rows} >= {doctor.id, admin.id}
    assert all(row.resource_type == "appointment_history" for row in exported_rows)


async def test_history_validation_bounds_and_naive_timestamps(
    make_user, auth_client
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    now = datetime.now(timezone.utc)
    assert (
        await client.get(
            "/api/v1/appointments/history",
            params={"start": now.isoformat()},
        )
    ).status_code == 400
    assert (
        await client.get(
            "/api/v1/appointments/history",
            params={
                "start": now.isoformat(),
                "end": (now + timedelta(days=367)).isoformat(),
            },
        )
    ).status_code == 400
    assert (
        await client.get(
            "/api/v1/appointments/history",
            params={"status": "deleted"},
        )
    ).status_code == 400
    assert (
        await client.get(
            "/api/v1/appointments/history",
            params={
                "start": now.replace(tzinfo=None).isoformat(),
                "end": (now + timedelta(days=1)).isoformat(),
            },
        )
    ).status_code == 400


async def test_concurrent_overlapping_bookings_only_one_wins(
    db_engine, make_user, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    day = _next_weekday()
    start = _utc_at(day, 9, 30)

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        await _seed_availability(db, doctor.id)

    async def attempt(patient_id: uuid.UUID) -> uuid.UUID | None:
        async with AsyncSession(db_engine, expire_on_commit=False) as db:
            try:
                row = await appointments_service.create_appointment(
                    db,
                    patient_id=patient_id,
                    doctor_id=doctor.id,
                    start_at=start,
                    duration_minutes=30,
                    appointment_type="Consultation",
                    notes=None,
                    status="scheduled",
                    created_by=doctor.id,
                )
                await db.commit()
                return row.id
            except Conflict:
                await db.rollback()
                return None

    results = await asyncio.gather(
        attempt(clinical_data.patient_id),
        attempt(clinical_data.empty_patient_id),
    )
    assert sum(result is not None for result in results) == 1

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        count = await db.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.doctor_id == doctor.id,
                Appointment.status.in_(("scheduled", "confirmed")),
                Appointment.start_at == start,
            )
        )
    assert count == 1
