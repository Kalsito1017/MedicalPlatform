"""Patient, encounter, lab, and prescription response payloads (docs/API.md).

All clinical payloads carry "provenance": "synthetic". Numeric values serialize
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

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.schemas.common import Timestamp

ProvenanceLabel = Literal["synthetic"]


class DiagnosisOut(BaseModel):
    """One problem-list entry (docs/API.md: code/name/status, active first)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    status: str
    onset_date: date | None = None
    provenance: ProvenanceLabel = "synthetic"


class ClinicalNoteOut(BaseModel):
    """The structured fields of one SOAP-style note (never the free-text body)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    note_type: str
    created_at: Timestamp
    symptoms: str
    assessment: str
    proposed_plan: str
    provenance: ProvenanceLabel = "synthetic"


class PrescriptionItemOut(BaseModel):
    """One line item of a prescription (what was actually prescribed)."""

    model_config = ConfigDict(from_attributes=True)

    sequence_no: int
    medication_name: str
    dose_amount: Decimal
    dose_unit: str
    provenance: ProvenanceLabel = "synthetic"


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
    provenance: ProvenanceLabel = "synthetic"
    created_at: Timestamp
    updated_at: Timestamp
    # Transport carriers (see module docstring): read-model attributes attached
    # by app.services.patients.get_patient, never serialized anywhere.
    diagnoses: list[DiagnosisOut] = Field(default_factory=list, exclude=True)
    latest_note: ClinicalNoteOut | None = Field(default=None, exclude=True)


class EncounterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    occurred_at: Timestamp
    encounter_type: str
    summary: str
    weight_kg: Decimal | None = None  # per-visit vitals snapshot
    height_cm: Decimal | None = None
    provenance: ProvenanceLabel = "synthetic"
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
    provenance: ProvenanceLabel = "synthetic"
    created_at: Timestamp
    updated_at: Timestamp


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
    provenance: ProvenanceLabel = "synthetic"
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
    provenance: ProvenanceLabel = "synthetic"


class VitalsOut(BaseModel):
    """Latest per-visit vitals snapshot plus the previous weight (trend)."""

    weight_kg: Decimal | None = None
    height_cm: Decimal | None = None
    measured_at: Timestamp | None = None
    previous_weight_kg: Decimal | None = None
    previous_measured_at: Timestamp | None = None
    provenance: ProvenanceLabel = "synthetic"


class TreatmentItemOut(BaseModel):
    """One prescribed line item (medication, dose+unit)."""

    sequence_no: int
    medication: str
    dose_amount: Decimal
    dose_unit: str
    provenance: ProvenanceLabel = "synthetic"


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
    provenance: ProvenanceLabel = "synthetic"


class PatientSummaryOut(BaseModel):
    """Synthetic patient summary (docs/API.md "Synthetic patient summary payload").

    Additive-only extension (2026-10-27): every pre-existing key
    (`patient`, `encounters`, `active_problems`, `problems`, `latest_labs`,
    `active_prescriptions`, `provenance`) is unchanged; the new sections are
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
    provenance: ProvenanceLabel = "synthetic"

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
    measured = [item for item in encounters if item.weight_kg is not None]
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
    )
