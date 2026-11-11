"""Analytics summary payload (docs/API.md "Analytics", GET /analytics/summary).

Operational aggregates over the seeded dataset for one validated UTC date
range. Every count is an integer and no patient-identifying or clinical text
appears in this payload. Derived rates are rounded DISPLAY numbers (1 decimal
place) — deliberately JSON numbers, never clinical values (docs/API.md records
this choice). Timestamps serialize as ISO-8601 UTC `Z` like everywhere else.

Fields that depend on data the platform does not persist are represented as
explicit `available: false` objects (AI latency, voice transcription, API error
rate) instead of invented values.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Timestamp


class RangeOut(BaseModel):
    """The selected inclusive UTC dates plus the previous comparison window.

    `timezone` is the bucket timezone for clinical/audit daily series (UTC);
    `appointment_timezone` is the clinic-local timezone used for appointment
    weekday/hour/trend buckets because schedules are local wall-clock values.
    """

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    days: int = Field(ge=1, le=366)
    previous_start: date
    previous_end: date
    timezone: Literal["UTC"]
    appointment_timezone: str


class CountsOut(BaseModel):
    """Current lifetime row counts (soft-deleted rows excluded)."""

    model_config = ConfigDict(extra="forbid")

    patients: int = Field(ge=0)
    encounters: int = Field(ge=0)
    labs: int = Field(ge=0)
    prescriptions: int = Field(ge=0)
    active_prescriptions: int = Field(ge=0)
    diagnoses_active: int = Field(ge=0)
    staff_active: int = Field(ge=0)
    staff_inactive: int = Field(ge=0)
    ai_conversations: int = Field(ge=0)


class ActivityOut(BaseModel):
    """Headline activity in one selected window."""

    model_config = ConfigDict(extra="forbid")

    new_patients: int = Field(ge=0)
    encounters: int = Field(ge=0)
    lab_results: int = Field(ge=0)
    prescriptions_written: int = Field(ge=0)
    appointments: int = Field(ge=0)
    ai_requests: int = Field(ge=0)
    successful_signins: int = Field(ge=0)
    failed_signins: int = Field(ge=0)
    permission_denials: int = Field(ge=0)


class ComparisonOut(BaseModel):
    """Current vs equally long preceding period.

    `change_pct` is null when the previous period had zero events: a percentage
    against a zero denominator is undefined and must not be fabricated.
    """

    model_config = ConfigDict(extra="forbid")

    current: int = Field(ge=0)
    previous: int = Field(ge=0)
    change_pct: float | None = None


class ComparisonsOut(BaseModel):
    """Comparisons for the headline activity metrics."""

    model_config = ConfigDict(extra="forbid")

    new_patients: ComparisonOut
    encounters: ComparisonOut
    lab_results: ComparisonOut
    prescriptions_written: ComparisonOut
    appointments: ComparisonOut
    ai_requests: ComparisonOut


class EncounterTypeOut(BaseModel):
    """One encounter type's count in the window (bounded list, count desc)."""

    model_config = ConfigDict(extra="forbid")

    encounter_type: str
    count: int = Field(ge=0)


class EncounterTrendDayOut(BaseModel):
    """One UTC day of encounter volume (zero-filled)."""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    encounters: int = Field(ge=0)


class PatientAnalyticsOut(BaseModel):
    """Operational patient activity and encounter volume."""

    model_config = ConfigDict(extra="forbid")

    new_patients: int = Field(ge=0)
    returning_patients: int = Field(ge=0)
    active_patients: int = Field(ge=0)
    encounters: int = Field(ge=0)
    avg_encounters_per_day: float = Field(ge=0)
    by_encounter_type: list[EncounterTypeOut] = Field(max_length=20)
    trend: list[EncounterTrendDayOut]
    timezone: Literal["UTC"]


class AppointmentStatusOut(BaseModel):
    """Appointment counts over the closed scheduling status vocabulary."""

    model_config = ConfigDict(extra="forbid")

    scheduled: int = Field(ge=0)
    confirmed: int = Field(ge=0)
    completed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    no_show: int = Field(ge=0)


class AppointmentVisitTypeOut(BaseModel):
    """One visit type's count (bounded list, count desc)."""

    model_config = ConfigDict(extra="forbid")

    visit_type: str
    count: int = Field(ge=0)


class AppointmentWeekdayOut(BaseModel):
    """One clinic-local weekday (0 = Monday)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    label: str
    count: int = Field(ge=0)


class AppointmentHourOut(BaseModel):
    """One clinic-local hour (0-23)."""

    model_config = ConfigDict(extra="forbid")

    hour: int = Field(ge=0, le=23)
    count: int = Field(ge=0)


class AppointmentTrendDayOut(BaseModel):
    """One clinic-local day of appointment volume (zero-filled)."""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    no_show: int = Field(ge=0)


class AppointmentUtilizationOut(BaseModel):
    """Booked / recorded-available minutes; null when availability is unknown."""

    model_config = ConfigDict(extra="forbid")

    booked_minutes: int = Field(ge=0)
    available_minutes: int = Field(ge=0)
    utilization_pct: float | None = Field(default=None, ge=0)
    doctors_with_schedule: int = Field(ge=0)


class AppointmentAnalyticsOut(BaseModel):
    """Real scheduling aggregates; no per-doctor performance ranking."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    by_status: AppointmentStatusOut
    by_visit_type: list[AppointmentVisitTypeOut] = Field(max_length=20)
    by_weekday: list[AppointmentWeekdayOut] = Field(min_length=7, max_length=7)
    by_hour: list[AppointmentHourOut] = Field(min_length=24, max_length=24)
    trend: list[AppointmentTrendDayOut]
    utilization: AppointmentUtilizationOut
    timezone: str


class LabStatusMixOut(BaseModel):
    """Document-state mix of lab results (the closed `status` vocabulary)."""

    model_config = ConfigDict(extra="forbid")

    final: int = Field(ge=0)
    preliminary: int = Field(ge=0)
    pending: int = Field(ge=0)


class AbnormalLabRateOut(BaseModel):
    """Abnormal share of EVALUABLE results; `total` is the denominator."""

    model_config = ConfigDict(extra="forbid")

    abnormal: int = Field(ge=0)
    total: int = Field(ge=0)
    pct: float = Field(ge=0, le=100)


class LabClassificationOut(BaseModel):
    """Mutually exclusive classification buckets for the selected window.

    `normal + high + low + unclassified == total`; `high + low == abnormal`.
    Missing/invalid reference ranges are unclassified, never normal.
    """

    model_config = ConfigDict(extra="forbid")

    normal: int = Field(ge=0)
    high: int = Field(ge=0)
    low: int = Field(ge=0)
    unclassified: int = Field(ge=0)
    missing_reference_ranges: int = Field(ge=0)
    reference_coverage_pct: float = Field(ge=0, le=100)


class TopAbnormalTestOut(BaseModel):
    """One test code's abnormal-result count (bounded list, count desc)."""

    model_config = ConfigDict(extra="forbid")

    test_code: str
    test_name: str
    abnormal_count: int = Field(ge=1)


class LabTrendDayOut(BaseModel):
    """One UTC day of lab volume and classification (zero-filled)."""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    total: int = Field(ge=0)
    evaluable: int = Field(ge=0)
    abnormal: int = Field(ge=0)


class LabAnalyticsOut(BaseModel):
    """Laboratory workload, classification and trends for the window."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    status_mix: LabStatusMixOut
    abnormal_rate: AbnormalLabRateOut
    classification: LabClassificationOut
    top_abnormal_tests: list[TopAbnormalTestOut] = Field(max_length=8)
    trend: list[LabTrendDayOut]
    timezone: Literal["UTC"]


class PrescriptionsByStatusOut(BaseModel):
    """Prescription counts over the closed lifecycle vocabulary."""

    model_config = ConfigDict(extra="forbid")

    active: int = Field(ge=0)
    completed: int = Field(ge=0)
    stopped: int = Field(ge=0)
    cancelled: int = Field(ge=0)


class PrescriptionTrendDayOut(BaseModel):
    """One UTC start-date day of prescriptions written (zero-filled)."""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    prescriptions: int = Field(ge=0)


class PrescriptionAnalyticsOut(BaseModel):
    """Prescription workload for the window plus the lifetime status mix."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    by_status: PrescriptionsByStatusOut
    lifetime_by_status: PrescriptionsByStatusOut
    trend: list[PrescriptionTrendDayOut]
    timezone: Literal["UTC"]
    date_basis: Literal["start_date"]


class SecurityTotalsOut(BaseModel):
    """Aggregate access/security events in the selected window."""

    model_config = ConfigDict(extra="forbid")

    successful_signins: int = Field(ge=0)
    failed_signins: int = Field(ge=0)
    permission_denials: int = Field(ge=0)
    patient_reads: int = Field(ge=0)
    lab_reads: int = Field(ge=0)
    prescription_reads: int = Field(ge=0)
    ai_requests: int = Field(ge=0)


class SecurityTrendDayOut(BaseModel):
    """One UTC day of access/security activity (zero-filled)."""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    successful_signins: int = Field(ge=0)
    failed_signins: int = Field(ge=0)
    permission_denials: int = Field(ge=0)
    patient_reads: int = Field(ge=0)
    lab_reads: int = Field(ge=0)
    prescription_reads: int = Field(ge=0)
    ai_requests: int = Field(ge=0)


class SecurityAnalyticsOut(BaseModel):
    """Access & security aggregate activity for the window."""

    model_config = ConfigDict(extra="forbid")

    totals: SecurityTotalsOut
    trend: list[SecurityTrendDayOut]
    timezone: Literal["UTC"]


class TopToolOut(BaseModel):
    """One MCP tool's real call/failure counts (bounded list)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    calls: int = Field(ge=1)
    failures: int = Field(ge=0)


class LatencyAvailabilityOut(BaseModel):
    """AI latency is not persisted; the reason is part of the contract."""

    model_config = ConfigDict(extra="forbid")

    available: Literal[False]
    average_ms: None = None
    reason: str


class VoiceAvailabilityOut(BaseModel):
    """Voice metrics are not persisted by design; no aggregate exists."""

    model_config = ConfigDict(extra="forbid")

    available: Literal[False]
    reason: str


class AiUsageOut(BaseModel):
    """AI ask reliability plus real MCP tool calls for the window."""

    model_config = ConfigDict(extra="forbid")

    total_requests: int = Field(ge=0)
    successful: int = Field(ge=0)
    failed: int = Field(ge=0)
    denied: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0, le=100)
    top_tools: list[TopToolOut] = Field(max_length=6)
    latency: LatencyAvailabilityOut
    voice: VoiceAvailabilityOut
    timezone: Literal["UTC"]


class DataQualityCheckOut(BaseModel):
    """One real recorded data-quality gap with its denominator and scope."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    count: int = Field(ge=0)
    denominator: int = Field(ge=0)
    scope: Literal["period", "current"]
    kind: Literal["classification", "optional_metadata"]
    note: str


class DataQualityOut(BaseModel):
    """Bounded data-quality checks (never clinical-quality judgements)."""

    model_config = ConfigDict(extra="forbid")

    checks: list[DataQualityCheckOut] = Field(max_length=8)


class ApiErrorRateOut(BaseModel):
    """API error rate is not persisted; expose that fact explicitly."""

    model_config = ConfigDict(extra="forbid")

    available: Literal[False]
    rate_pct: None = None
    reason: str


class ServiceHealthOut(BaseModel):
    """Configured integration + honest availability state."""

    model_config = ConfigDict(extra="forbid")

    configured: bool
    availability: Literal["not_monitored"]
    note: str


class SystemHealthOut(BaseModel):
    """Operational facts this request can verify; no synthetic probes."""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["operational"]
    database: Literal["connected"]
    api_error_rate: ApiErrorRateOut
    llm: ServiceHealthOut
    voice: ServiceHealthOut


class StaffByRoleOut(BaseModel):
    """Distinct ACTIVE staff accounts holding one role (all policy roles)."""

    model_config = ConfigDict(extra="forbid")

    role: str
    count: int = Field(ge=0)


class StaffAnalyticsOut(BaseModel):
    """Compact staff account aggregates; no individual performance data."""

    model_config = ConfigDict(extra="forbid")

    active_total: int = Field(ge=0)
    inactive_total: int = Field(ge=0)
    by_role: list[StaffByRoleOut]


class AnalyticsSummaryOut(BaseModel):
    """GET /api/v1/analytics/summary — one bounded operational overview.

    Accepts optional `start` and `end` inclusive UTC dates (both or neither);
    the default is the last 30 days including today, the maximum span is 366
    days, and unknown query parameters remain `400 validation_error`.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: Timestamp
    range: RangeOut
    counts: CountsOut
    activity: ActivityOut
    previous_activity: ActivityOut
    comparisons: ComparisonsOut
    patients: PatientAnalyticsOut
    appointments: AppointmentAnalyticsOut
    labs: LabAnalyticsOut
    prescriptions: PrescriptionAnalyticsOut
    security: SecurityAnalyticsOut
    ai_usage: AiUsageOut
    data_quality: DataQualityOut
    system_health: SystemHealthOut
    staff: StaffAnalyticsOut
