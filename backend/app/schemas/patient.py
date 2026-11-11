"""Patient, encounter, lab, and prescription response payloads (docs/API.md).

Data provenance is database-internal (audit traceability) and never
serializes into a payload. Numeric values serialize
as JSON strings (Decimal, never float). Name fields are free UTF-8 text and are
returned verbatim.

Summary payload extension (docs/API.md "Synthetic patient summary payload",
2026-10-27): `PatientSummaryOut` is ADDITIVE-ONLY - no existing key was removed
or renamed. The new sections (`blood_type`, `age_years`, `vitals`, `diagnoses`,
`symptoms`/`assessment`/`proposed_plan`, `prescribed_treatment`) are derived in
`_derive_sections` from the enriched rows `app.services` returns (see
`app.services.patients` module docstring), so the REST summary handler and the
MCP `get_patient_summary` tool serialize the same facts. `age_years` is derived
from `date_of_birth` on every read and is never stored.

Transport carriers: `PatientOut.diagnoses` / `PatientOut.latest_note` are
plain read-model attributes attached by `app.services.patients.get_patient`;
they are marked `exclude=True` so they never appear in ANY serialized payload
(patients list/detail stay demographics-only) and exist only to carry the
patient-level facts into `PatientSummaryOut`'s derivation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.schemas.common import Timestamp


class DiagnosisOut(BaseModel):
    """One problem-list entry (docs/API.md: code/name/status, active first)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    status: str
    onset_date: date | None = None


class ClinicalNoteOut(BaseModel):
    """The structured fields of one SOAP-style note (never the free-text body)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    note_type: str
    created_at: Timestamp
    symptoms: str
    assessment: str
    proposed_plan: str


class PrescriptionItemOut(BaseModel):
    """One line item of a prescription (what was actually prescribed)."""

    model_config = ConfigDict(from_attributes=True)

    sequence_no: int
    medication_name: str
    dose_amount: Decimal
    dose_unit: str


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mrn: str
    display_name: str
    given_name: str
    family_name: str
    date_of_birth: date | None = None
    sex: str
    blood_type: str | None = None
    age_years: int | None = None  # derived from date_of_birth; never stored
    created_at: Timestamp
    updated_at: Timestamp
    # Transport carriers (see module docstring): read-model attributes attached
    # by app.services.patients.get_patient, never serialized anywhere.
    diagnoses: list[DiagnosisOut] = Field(default_factory=list, exclude=True)
    latest_note: ClinicalNoteOut | None = Field(default=None, exclude=True)


PATIENT_NAME_MAX = 200
PATIENT_SEXES = ("female", "male", "other", "unknown")
PATIENT_BLOOD_TYPES = (
    "A+",
    "A-",
    "B+",
    "B-",
    "AB+",
    "AB-",
    "O+",
    "O-",
    "unknown",
)


class PatientCreate(BaseModel):
    """Create a patient record. MRN and provenance are assigned server-side."""

    model_config = ConfigDict(extra="forbid")

    given_name: str = Field(min_length=1, max_length=PATIENT_NAME_MAX)
    family_name: str = Field(min_length=1, max_length=PATIENT_NAME_MAX)
    display_name: str | None = Field(default=None, max_length=PATIENT_NAME_MAX)
    date_of_birth: date | None = None
    sex: Literal["female", "male", "other", "unknown"] = "unknown"
    blood_type: Literal[
        "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown"
    ] | None = None

    @field_validator("given_name", "family_name")
    @classmethod
    def _strip_required_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("date_of_birth")
    @classmethod
    def _not_future(cls, value: date | None) -> date | None:
        if value is not None and value > datetime.now(timezone.utc).date():
            raise ValueError("date of birth cannot be in the future")
        return value


class EncounterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    occurred_at: Timestamp
    encounter_type: str
    summary: str
    weight_kg: Decimal | None = None  # per-visit vitals snapshot
    height_cm: Decimal | None = None
    systolic_bp: int | None = None
    diastolic_bp: int | None = None
    heart_rate: int | None = None
    temperature_c: Decimal | None = None
    oxygen_saturation: int | None = None
    created_at: Timestamp
    updated_at: Timestamp


class LabResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    encounter_id: UUID | None = None
    test_code: str
    test_name: str
    value_num: Decimal | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    status: str
    collected_at: Timestamp
    resulted_at: Timestamp | None = None
    created_at: Timestamp
    updated_at: Timestamp


class LabTrendPointOut(BaseModel):
    """One chart point: a numeric result plus its own-range classification."""

    collected_at: Timestamp
    value_num: Decimal
    flag: Literal["high", "low", "normal", "unclassified"]


class LabTrendSeriesOut(BaseModel):
    """Bounded series for one lab test."""

    test_code: str
    test_name: str
    unit: str | None = None
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    points: list[LabTrendPointOut] = Field(default_factory=list)
    abnormal_count: int = Field(ge=0)


class LabTrendsOut(BaseModel):
    """Chart-ready numeric lab trends for one authorized patient."""

    patient_id: UUID
    window_days: int = Field(ge=1, le=365)
    series: list[LabTrendSeriesOut] = Field(default_factory=list)
    truncated: bool


class PrescriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    encounter_id: UUID | None = None
    medication_id: UUID | None = None
    medication_name: str
    dose_amount: Decimal | None = None
    dose_unit: str
    route: str
    frequency: str
    status: str
    start_date: date
    end_date: date | None = None
    prescriber_id: UUID | None = None
    items: list[PrescriptionItemOut] = Field(default_factory=list)
    created_at: Timestamp
    updated_at: Timestamp


class ProblemOut(BaseModel):
    """One active problem/diagnosis entry (derived from the diagnosis records).

    `scripts/seed_demo_data.py` seeds `patients.diagnosis` rows; the summary
    derives this list from the active ones (`display_name` maps the diagnosis
    `name`), so the docs/API.md summary shape ("encounters, active problems,
    latest labs, active meds") stays stable.
    """

    model_config = ConfigDict(from_attributes=True)

    code: str | None = None
    display_name: str
    status: str = "active"
    onset_date: date | None = None


class VitalsOut(BaseModel):
    """Latest per-visit vitals snapshot plus previous readings for trend cues."""

    weight_kg: Decimal | None = None
    height_cm: Decimal | None = None
    measured_at: Timestamp | None = None
    previous_weight_kg: Decimal | None = None
    previous_measured_at: Timestamp | None = None
    systolic_bp: int | None = None
    diastolic_bp: int | None = None
    heart_rate: int | None = None
    temperature_c: Decimal | None = None
    oxygen_saturation: int | None = None
    previous_systolic_bp: int | None = None
    previous_diastolic_bp: int | None = None
    previous_heart_rate: int | None = None


class TreatmentResponsePointOut(BaseModel):
    """One observed value in a medication-response series."""

    measured_at: Timestamp
    value: Decimal


class TreatmentResponseOut(BaseModel):
    """Observed before/after values for one prescription/metric pair.

    This is a read-model derivation, not a stored clinical outcome and not a
    causal claim. ``trend`` describes the direction of the observed values.
    """

    prescription_id: UUID
    medication: str
    status: str
    started_on: date
    metric_code: str
    metric_name: str
    unit: str | None = None
    baseline: TreatmentResponsePointOut | None = None
    follow_up: TreatmentResponsePointOut | None = None
    current: TreatmentResponsePointOut | None = None
    delta: Decimal | None = None
    trend: Literal["improved", "worsened", "stable", "monitor", "insufficient_data"]
    points: list[TreatmentResponsePointOut] = Field(default_factory=list)


class TreatmentItemOut(BaseModel):
    """One prescribed line item (medication, dose+unit)."""

    sequence_no: int
    medication: str
    dose_amount: Decimal
    dose_unit: str


class TreatmentOut(BaseModel):
    """One active prescription rendered as prescribed treatment.

    `items` is what was ACTUALLY prescribed per line; the top-level fields are
    the prescription's own medication/dose/route/frequency.
    """

    prescription_id: UUID
    medication: str
    dose_amount: Decimal | None = None
    dose_unit: str
    route: str
    frequency: str
    items: list[TreatmentItemOut] = Field(default_factory=list)


class PatientSummaryOut(BaseModel):
    """Synthetic patient summary (docs/API.md "Synthetic patient summary payload").

    Additive-only extension (2026-10-27): every pre-existing key
    (`patient`, `encounters`, `active_problems`, `problems`, `latest_labs`,
    `active_prescriptions`) is unchanged; the new sections are
    derived in `_derive_sections` from the enriched service rows so the REST
    payload and the MCP tool output carry the same facts. Every list is bounded
    by app.services.patients constants. `problems` is a compatibility alias for
    `active_problems` (the shipped frontend reads `summary.problems`).
    """

    patient: PatientOut
    encounters: list[EncounterOut] = Field(default_factory=list)
    active_problems: list[ProblemOut] = Field(default_factory=list)
    latest_labs: list[LabResultOut] = Field(default_factory=list)
    active_prescriptions: list[PrescriptionOut] = Field(default_factory=list)
    # --- additive extension (derived; see _derive_sections) ---
    age_years: int | None = None
    blood_type: str | None = None
    vitals: VitalsOut | None = None
    diagnoses: list[DiagnosisOut] = Field(default_factory=list)
    symptoms: str | None = None
    assessment: str | None = None
    proposed_plan: str | None = None
    prescribed_treatment: list[TreatmentOut] = Field(default_factory=list)
    last_visit: EncounterOut | None = None
    visit_count: int = 0
    medication_responses: list[TreatmentResponseOut] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_sections(self) -> PatientSummaryOut:
        """Fill the derived sections from the enriched sub-models.

        The REST summary handler passes `active_problems=[]` (it predates the
        diagnosis source); when the patient row carries diagnoses, the section
        is re-derived from them so the UI and the AI report the same problems.
        """
        if self.blood_type is None:
            self.blood_type = self.patient.blood_type
        if self.age_years is None:
            self.age_years = self.patient.age_years
        if not self.diagnoses:
            self.diagnoses = list(self.patient.diagnoses)
        if not self.active_problems and self.diagnoses:
            self.active_problems = [
                ProblemOut(
                    code=item.code,
                    display_name=item.name,
                    status=item.status,
                    onset_date=item.onset_date,
                )
                for item in self.diagnoses
                if item.status == "active"
            ]
        note = self.patient.latest_note
        if note is not None:
            if self.symptoms is None:
                self.symptoms = note.symptoms
            if self.assessment is None:
                self.assessment = note.assessment
            if self.proposed_plan is None:
                self.proposed_plan = note.proposed_plan
        if self.vitals is None:
            self.vitals = _vitals_from_encounters(self.encounters)
        if not self.prescribed_treatment and self.active_prescriptions:
            self.prescribed_treatment = [
                TreatmentOut(
                    prescription_id=rx.id,
                    medication=rx.medication_name,
                    dose_amount=rx.dose_amount,
                    dose_unit=rx.dose_unit,
                    route=rx.route,
                    frequency=rx.frequency,
                    items=[
                        TreatmentItemOut(
                            sequence_no=item.sequence_no,
                            medication=item.medication_name,
                            dose_amount=item.dose_amount,
                            dose_unit=item.dose_unit,
                        )
                        for item in rx.items
                    ],
                )
                for rx in self.active_prescriptions
            ]
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def problems(self) -> list[ProblemOut]:
        return self.active_problems


def _vitals_from_encounters(encounters: list[EncounterOut]) -> VitalsOut | None:
    """Latest vitals snapshot + previous weight from newest-first encounters."""
    measured = [
        item for item in encounters
        if item.weight_kg is not None or item.systolic_bp is not None
    ]
    if not measured:
        return None
    latest = measured[0]
    previous = measured[1] if len(measured) > 1 else None
    return VitalsOut(
        weight_kg=latest.weight_kg,
        height_cm=latest.height_cm,
        measured_at=latest.occurred_at,
        previous_weight_kg=previous.weight_kg if previous is not None else None,
        previous_measured_at=previous.occurred_at if previous is not None else None,
        systolic_bp=latest.systolic_bp,
        diastolic_bp=latest.diastolic_bp,
        heart_rate=latest.heart_rate,
        temperature_c=latest.temperature_c,
        oxygen_saturation=latest.oxygen_saturation,
        previous_systolic_bp=previous.systolic_bp if previous is not None else None,
        previous_diastolic_bp=previous.diastolic_bp if previous is not None else None,
        previous_heart_rate=previous.heart_rate if previous is not None else None,
    )
