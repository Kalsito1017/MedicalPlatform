"""Appointment and availability API schemas.

Appointments are scheduling metadata. Timestamps use the frozen UTC ISO-8601
contract; local wall-clock display is a frontend concern driven by the clinic
timezone returned in `AppointmentConfigOut` / `AvailabilityOut`.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Timestamp

AppointmentStatus = Literal["scheduled", "confirmed", "completed", "cancelled", "no_show"]
AppointmentWriteStatus = Literal["scheduled", "confirmed"]

APPOINTMENT_TYPE_MAX = 100
APPOINTMENT_NOTES_MAX = 2000
TIME_OFF_REASON_MAX = 200
DURATION_MIN = 5
DURATION_MAX = 480
INTERPRET_TEXT_MAX = 2000


def _utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


class AppointmentOut(BaseModel):
    """One appointment with the minimal joined display fields for the calendar."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    doctor_id: UUID
    start_at: Timestamp
    end_at: Timestamp
    duration_minutes: int = Field(ge=1)
    appointment_type: str | None = None
    status: AppointmentStatus
    notes: str | None = None
    patient_display_name: str
    patient_mrn: str
    doctor_display_name: str
    calendar_removed_at: Timestamp | None = None
    created_at: Timestamp
    updated_at: Timestamp


class AppointmentEventOut(BaseModel):
    """One immutable appointment lifecycle event (no clinical payloads)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    appointment_id: UUID
    event_type: Literal[
        "created", "updated", "rescheduled", "status_changed", "calendar_removed"
    ]
    actor_user_id: UUID | None = None
    actor_display_name: str | None = None
    old_start_at: Timestamp | None = None
    old_end_at: Timestamp | None = None
    new_start_at: Timestamp | None = None
    new_end_at: Timestamp | None = None
    old_status: AppointmentStatus | None = None
    new_status: AppointmentStatus | None = None
    request_id: str | None = None
    created_at: Timestamp


class AppointmentCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID | None = None
    start_at: datetime
    duration_minutes: int = Field(ge=DURATION_MIN, le=DURATION_MAX)
    appointment_type: str | None = Field(default=None, max_length=APPOINTMENT_TYPE_MAX)
    notes: str | None = Field(default=None, max_length=APPOINTMENT_NOTES_MAX)
    status: AppointmentWriteStatus = "scheduled"

    _aware_start = field_validator("start_at")(_utc_aware)

    @field_validator("appointment_type", "notes")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AppointmentUpdate(BaseModel):
    start_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=DURATION_MIN, le=DURATION_MAX)
    appointment_type: str | None = Field(default=None, max_length=APPOINTMENT_TYPE_MAX)
    notes: str | None = Field(default=None, max_length=APPOINTMENT_NOTES_MAX)
    status: Literal["scheduled", "confirmed", "completed", "no_show"] | None = None

    _aware_start = field_validator("start_at")(_utc_aware)

    @field_validator("appointment_type", "notes")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class WorkingHoursOut(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time


class WorkingHoursWrite(WorkingHoursOut):
    pass


class TimeOffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    start_at: Timestamp
    end_at: Timestamp
    reason: str | None = None


class TimeOffWrite(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str | None = Field(default=None, max_length=TIME_OFF_REASON_MAX)

    _aware_bounds = field_validator("start_at", "end_at")(_utc_aware)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AvailabilityOut(BaseModel):
    doctor_id: UUID
    timezone: str
    default_duration_minutes: int
    slot_increment_minutes: int
    working_hours: list[WorkingHoursOut]
    time_off: list[TimeOffOut]


class AvailabilityWrite(BaseModel):
    default_duration_minutes: int = Field(default=30, ge=DURATION_MIN, le=240)
    slot_increment_minutes: int = Field(default=30, ge=5, le=60)
    working_hours: list[WorkingHoursWrite] = Field(default_factory=list, max_length=21)
    time_off: list[TimeOffWrite] = Field(default_factory=list, max_length=100)


class SlotOut(BaseModel):
    start_at: Timestamp
    end_at: Timestamp
    local_start: str
    local_end: str
    available: bool
    reason: Literal["available", "past", "booked", "time_off", "outside_hours"] = "available"


class SlotsOut(BaseModel):
    doctor_id: UUID
    date: str
    timezone: str
    slots: list[SlotOut]


class AppointmentConfigOut(BaseModel):
    timezone: str
    max_duration_minutes: int = DURATION_MAX
    max_range_days: int = 62
    history_max_range_days: int = 366


class AppointmentInterpretRequest(BaseModel):
    """One spoken/typed scheduling request. Never stored or audited."""

    text: str = Field(min_length=3, max_length=INTERPRET_TEXT_MAX)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be blank")
        return stripped


class PatientSuggestionRequest(BaseModel):
    """One transcript to check for likely patient-name/MRN corrections."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=3, max_length=INTERPRET_TEXT_MAX)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be blank")
        return stripped


class PatientSuggestionOut(BaseModel):
    """Name/MRN-only suggestion; no patient id or clinical content."""

    display_name: str
    mrn: str
    matched_name_text: str | None = None
    matched_mrn_text: str | None = None


class PatientSuggestionListOut(BaseModel):
    items: list[PatientSuggestionOut] = Field(default_factory=list, max_length=3)


CorrectionLanguage = Literal["bg", "en"]
CorrectionCategory = Literal[
    "appointment_type",
    "date",
    "weekday",
    "time",
    "duration",
    "medical_term",
    "number",
    "other",
]
CorrectionConfidence = Literal["high", "medium", "low"]


class TranscriptionCorrectionRequest(BaseModel):
    """One transcript to check for likely speech-recognition mistakes."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=3, max_length=INTERPRET_TEXT_MAX)
    language: CorrectionLanguage = "bg"

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be blank")
        return stripped


class TranscriptionCorrectionSuggestionOut(BaseModel):
    """One non-persisted correction candidate; the doctor decides."""

    original_text: str = Field(min_length=1, max_length=160)
    suggested_text: str = Field(min_length=1, max_length=200)
    reason: str = Field(
        default="Possible speech recognition mismatch",
        max_length=200,
    )
    confidence: CorrectionConfidence
    category: CorrectionCategory


class TranscriptionCorrectionResponse(BaseModel):
    """Bounded correction analysis. No transcript is stored or echoed beyond it."""

    language: CorrectionLanguage
    has_suggestions: bool
    suggestions: list[TranscriptionCorrectionSuggestionOut] = Field(
        default_factory=list,
        max_length=4,
    )
    analysis_available: bool = True
    model: str | None = None


class ProposedPatientOut(BaseModel):
    id: UUID
    display_name: str
    mrn: str


class AppointmentProposalOut(BaseModel):
    """A non-persisted proposal. Creation requires a separate confirmed POST."""

    patient: ProposedPatientOut | None = None
    patient_candidates: list[ProposedPatientOut] = Field(default_factory=list)
    patient_query: str | None = None
    date: str | None = None
    start_time: str | None = None
    start_at: Timestamp | None = None
    end_at: Timestamp | None = None
    duration_minutes: int = 30
    appointment_type: str | None = None
    notes: str | None = None
    timezone: str
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    can_confirm: bool = False
    model: str
    ai_generated: Literal[True] = True
