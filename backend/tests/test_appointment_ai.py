"""Appointment AI interpretation tests: proposals only, deterministic server checks."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import LLMResult, ToolCallRequest
from app.models import Appointment, Patient
from app.services import appointment_ai
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


def _next_weekday(*, days_ahead: int = 2) -> date:
    day = datetime.now(CLINIC).date() + timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _local_start(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=CLINIC)


class FakeProvider:
    model_name = "fake-appointment-model"

    def __init__(self, payload, *, tool_calls=()):
        self.payload = payload
        self.tool_calls = tuple(tool_calls)
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools, max_tokens, timeout, session_id=None):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "session_id": session_id,
            }
        )
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return LLMResult(text=text, model=self.model_name, tool_calls=self.tool_calls)


def _proposal(clinical_data, day: date, *, hour: str = "10:00", **overrides):
    payload = {
        "patient_query": clinical_data.patient_name,
        "date": day.isoformat(),
        "time": hour,
        "duration_minutes": 30,
        "appointment_type": "Consultation",
        "notes": "Voice-scheduled follow-up",
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


async def _set_availability(client, *, doctor_id=None):
    params = {"doctor_id": str(doctor_id)} if doctor_id is not None else None
    response = await client.put(
        "/api/v1/appointments/availability",
        params=params,
        json=WORKING_WEEK,
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text


async def _interpret(client, text: str, *, doctor_id=None):
    params = {"doctor_id": str(doctor_id)} if doctor_id is not None else None
    return await client.post(
        "/api/v1/appointments/interpret",
        params=params,
        json={"text": text},
        headers=csrf_headers(client),
    )


async def test_interpret_proposes_without_creating_and_uses_tools_empty(
    make_user, auth_client, clinical_data, db_engine, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    day = _next_weekday()
    provider = FakeProvider(_proposal(clinical_data, day))
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(
        client, f"Book {clinical_data.patient_name} tomorrow at 10:00 for 30 minutes."
    )
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["ai_generated"] is True
    assert proposal["patient"]["id"] == str(clinical_data.patient_id)
    assert proposal["patient"]["mrn"] == clinical_data.patient_mrn
    assert proposal["date"] == day.isoformat()
    assert proposal["start_time"] == "10:00"
    assert proposal["start_at"] == _local_start(day).astimezone(
        ZoneInfo("UTC")
    ).isoformat().replace("+00:00", "Z")
    assert proposal["duration_minutes"] == 30
    assert proposal["can_confirm"] is True
    assert proposal["missing_fields"] == []
    assert proposal["warnings"] == []
    assert proposal["model"] == "fake-appointment-model"
    assert proposal["timezone"] == "Europe/Sofia"

    # The model received no tools and no patient/doctor ids, plus the clinic anchor.
    call = provider.calls[0]
    assert call["tools"] == []
    assert "Europe/Sofia" in call["system"]
    assert "current clinic-local date" in call["system"]
    assert str(clinical_data.patient_id) not in call["system"]
    assert str(doctor.id) not in call["system"]

    # A proposal is not a write: no appointment exists yet.
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        count = await db.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.doctor_id == doctor.id)
        )
    assert count == 0

    rows = await audit_rows(request_id=response.headers["x-request-id"])
    assert any(
        row.action == "appointment.ai.propose" and row.outcome == "success" for row in rows
    )


async def test_interpret_requires_a_patient_choice_when_ambiguous(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    day = _next_weekday()
    provider = FakeProvider(
        _proposal(clinical_data, day, patient_query="Fixture")
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(client, "Book Fixture tomorrow at 10:00.")
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["patient"] is None
    assert len(proposal["patient_candidates"]) >= 2
    assert proposal["can_confirm"] is False
    assert "patient" in proposal["missing_fields"]
    assert any("Several patients" in warning for warning in proposal["warnings"])


async def test_interpret_resolves_name_and_mrn_together(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    day = _next_weekday()
    provider = FakeProvider(
        _proposal(
            clinical_data,
            day,
            patient_query=f"{clinical_data.patient_name}, {clinical_data.patient_mrn}",
        )
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(
        client,
        f"Book {clinical_data.patient_name}, {clinical_data.patient_mrn}, tomorrow at 10:00.",
    )
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["patient"] is not None
    assert proposal["patient"]["id"] == str(clinical_data.patient_id)
    assert proposal["patient"]["mrn"] == clinical_data.patient_mrn
    assert "patient" not in proposal["missing_fields"]


async def test_interpret_matches_latin_name_to_cyrillic_patient(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    suffix = uuid.uuid4().hex[:6].upper()
    created = await client.post(
        "/api/v1/patients",
        json={
            "given_name": "Елена",
            "family_name": f"Тестова {suffix}",
            "sex": "female",
        },
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    patient = created.json()
    day = _next_weekday()
    provider = FakeProvider(
        _proposal(
            clinical_data,
            day,
            patient_query=f"Elena Testova {suffix}, {patient['mrn']}",
        )
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(
        client,
        f"Book a follow-up for Elena Testova {suffix}, {patient['mrn']}, tomorrow at 10:00.",
    )
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["patient"]["id"] == patient["id"]
    assert proposal["patient"]["mrn"] == patient["mrn"]
    assert proposal["warnings"] == []
    assert proposal["can_confirm"] is True


async def test_interpret_does_not_fuzzy_resolve_a_similar_name(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    day = _next_weekday()
    provider = FakeProvider(
        _proposal(clinical_data, day, patient_query="Fixture Alfa")
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(client, "Book Fixture Alfa tomorrow at 10:00.")
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["patient"] is None
    assert proposal["can_confirm"] is False
    assert "patient" in proposal["missing_fields"]
    assert any("No accessible patient" in warning for warning in proposal["warnings"])


async def test_interpret_requires_verification_when_name_and_mrn_disagree(
    make_user, auth_client, clinical_data, db_engine, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    day = _next_weekday()
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        other_mrn = await db.scalar(
            select(Patient.mrn).where(Patient.id == clinical_data.empty_patient_id)
        )
    assert other_mrn is not None
    provider = FakeProvider(
        _proposal(
            clinical_data,
            day,
            patient_query=f"{clinical_data.patient_name}, {other_mrn}",
        )
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(
        client,
        f"Book {clinical_data.patient_name}, {other_mrn}, tomorrow at 10:00.",
    )
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["patient"] is None
    assert len(proposal["patient_candidates"]) >= 2
    assert proposal["can_confirm"] is False
    assert "patient" in proposal["missing_fields"]
    assert any("different patients" in warning for warning in proposal["warnings"])


async def test_interpret_blocks_when_stated_mrn_matches_no_patient(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    day = _next_weekday()
    provider = FakeProvider(
        _proposal(
            clinical_data,
            day,
            patient_query=f"{clinical_data.patient_name}, MRN-9999",
        )
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(
        client,
        f"Book {clinical_data.patient_name}, MRN-9999, tomorrow at 10:00.",
    )
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["patient"] is None
    assert proposal["patient_candidates"]
    assert proposal["can_confirm"] is False
    assert "patient" in proposal["missing_fields"]
    assert any("MRN did not match" in warning for warning in proposal["warnings"])


async def test_interpret_reports_missing_date_and_time(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    provider = FakeProvider(
        _proposal(clinical_data, _next_weekday(), date=None, time=None)
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(client, f"Book {clinical_data.patient_name} soon.")
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["patient"]["id"] == str(clinical_data.patient_id)
    assert proposal["date"] is None
    assert proposal["start_time"] is None
    assert proposal["start_at"] is None
    assert set(proposal["missing_fields"]) >= {"date", "time"}
    assert proposal["can_confirm"] is False


async def test_interpret_rejects_dst_hole_and_conflicts(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)

    # 03:30 on the spring-forward Sunday does not exist in Europe/Sofia.
    provider = FakeProvider(
        _proposal(clinical_data, date(2027, 3, 28), hour="03:30")
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)
    response = await _interpret(client, f"Book {clinical_data.patient_name} then.")
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["can_confirm"] is False
    assert proposal["start_at"] is None
    assert any("daylight-saving" in warning for warning in proposal["warnings"])

    # A real conflicting appointment makes the proposal unconfirmable, not invalid.
    day = _next_weekday()
    start = _local_start(day).astimezone(ZoneInfo("UTC"))
    created = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": str(clinical_data.patient_id),
            "start_at": start.isoformat(),
            "duration_minutes": 30,
        },
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    provider = FakeProvider(_proposal(clinical_data, day))
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)
    conflict = await _interpret(client, f"Book {clinical_data.patient_name} at 10:00.")
    assert conflict.status_code == 200, conflict.text
    proposal = conflict.json()
    assert proposal["can_confirm"] is False
    assert any("conflicts" in warning for warning in proposal["warnings"])


async def test_interpret_fails_closed_on_bad_model_output(
    make_user, auth_client, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    provider = FakeProvider("I am not JSON")
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(client, "Book an appointment tomorrow.")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    rows = await audit_rows(request_id=response.headers["x-request-id"])
    assert any(
        row.action == "appointment.ai.propose" and row.outcome == "error" for row in rows
    )


async def test_interpret_rejects_model_tool_calls(
    make_user, auth_client, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    await _set_availability(client)
    provider = FakeProvider(
        {"patient_query": "someone", "date": _next_weekday().isoformat(), "time": "10:00"},
        tool_calls=(ToolCallRequest(id="call-1", name="get_patient_summary", arguments={}),),
    )
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(client, "Book an appointment tomorrow.")
    assert response.status_code == 503


async def test_admin_can_propose_for_a_selected_doctor(
    make_user, auth_client, clinical_data, monkeypatch
) -> None:
    admin = await make_user(roles=("clinical_admin",))
    doctor = await make_user(roles=("doctor",))
    admin_client = await auth_client(admin)
    await _set_availability(admin_client, doctor_id=doctor.id)
    day = _next_weekday()
    provider = FakeProvider(_proposal(clinical_data, day))
    monkeypatch.setattr(appointment_ai, "build_provider", lambda settings: provider)

    response = await _interpret(
        admin_client,
        f"Book {clinical_data.patient_name} tomorrow at 10:00.",
        doctor_id=doctor.id,
    )
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["can_confirm"] is True
    assert proposal["patient"]["id"] == str(clinical_data.patient_id)
    # Without a doctor the Admin probe is ambiguous and refused before the model.
    missing_doctor = await _interpret(
        admin_client, f"Book {clinical_data.patient_name} tomorrow at 10:00."
    )
    assert missing_doctor.status_code == 400
