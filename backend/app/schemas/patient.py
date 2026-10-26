"""Patient, encounter, lab, and prescription response payloads (docs/API.md).

All clinical payloads carry "provenance": "synthetic". Numeric values serialize
as JSON strings (Decimal, never float). Name fields are free UTF-8 text and are
returned verbatim.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Timestamp

ProvenanceLabel = Literal["synthetic"]


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mrn: str
    display_name: str
    given_name: str
    family_name: str
    date_of_birth: date | None = None
    sex: str
    provenance: ProvenanceLabel = "synthetic"
    created_at: Timestamp
    updated_at: Timestamp


class EncounterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    occurred_at: Timestamp
    encounter_type: str
    summary: str
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
    provenance: ProvenanceLabel = "synthetic"
    created_at: Timestamp
    updated_at: Timestamp


class PatientSummaryOut(BaseModel):
    """Synthetic patient summary (docs/API.md: encounters, latest labs, active meds).

    TODO(Phase 6): add "active problems" once a problems data source exists.
    """

    patient: PatientOut
    encounters: list[EncounterOut] = Field(default_factory=list)
    latest_labs: list[LabResultOut] = Field(default_factory=list)
    active_prescriptions: list[PrescriptionOut] = Field(default_factory=list)
    provenance: ProvenanceLabel = "synthetic"
