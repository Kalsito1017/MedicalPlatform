"""Analytics summary payload (docs/API.md "Analytics", GET /analytics/summary).

Operational aggregates over the synthetic demo dataset. Every count is an
integer and no clinical measurement appears in this payload at all. The one
derived rate, `abnormal_lab_rate.pct`, is a rounded DISPLAY number (1 decimal
place) — deliberately a JSON number, never a clinical value (docs/API.md
records this choice). Timestamps serialize as ISO-8601 UTC `Z` like
everywhere else.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Timestamp


class CountsOut(BaseModel):
    """Current row counts of the bounded demo tables (soft-deleted rows excluded)."""

    model_config = ConfigDict(extra="forbid")

    patients: int = Field(ge=0)
    encounters: int = Field(ge=0)
    labs: int = Field(ge=0)
    prescriptions: int = Field(ge=0)
    active_prescriptions: int = Field(ge=0)
    diagnoses_active: int = Field(ge=0)
    staff_active: int = Field(ge=0)
    ai_conversations: int = Field(ge=0)


class Last30DaysOut(BaseModel):
    """Activity in `[now - 30 days, now]` UTC, each metric on its own event time."""

    model_config = ConfigDict(extra="forbid")

    encounters: int = Field(ge=0)
    lab_results: int = Field(ge=0)
    prescriptions_written: int = Field(ge=0)
    ai_asks: int = Field(ge=0)
    logins: int = Field(ge=0)
    permission_denials: int = Field(ge=0)


class LabStatusMixOut(BaseModel):
    """Document-state mix of lab results (the closed `status` vocabulary)."""

    model_config = ConfigDict(extra="forbid")

    final: int = Field(ge=0)
    preliminary: int = Field(ge=0)
    pending: int = Field(ge=0)


class AbnormalLabRateOut(BaseModel):
    """Abnormal share of the EVALUABLE lab results (see docs/API.md "Analytics").

    `total` counts numeric results carrying at least one reference bound — the
    only results the rule can classify. `pct` is `abnormal / total * 100`
    rounded to 1 decimal (0.0 when total is 0): a rounded display number, not a
    clinical value.
    """

    model_config = ConfigDict(extra="forbid")

    abnormal: int = Field(ge=0)
    total: int = Field(ge=0)
    pct: float = Field(ge=0, le=100)


class TopAbnormalTestOut(BaseModel):
    """One test code's abnormal-result count (bounded list, count desc)."""

    model_config = ConfigDict(extra="forbid")

    test_code: str
    test_name: str
    abnormal_count: int = Field(ge=1)


class PrescriptionsByStatusOut(BaseModel):
    """Prescription counts over the closed `ck_prescription_status` vocabulary."""

    model_config = ConfigDict(extra="forbid")

    active: int = Field(ge=0)
    completed: int = Field(ge=0)
    stopped: int = Field(ge=0)
    cancelled: int = Field(ge=0)


class StaffByRoleOut(BaseModel):
    """Distinct ACTIVE staff accounts holding one role (all policy roles listed)."""

    model_config = ConfigDict(extra="forbid")

    role: str
    count: int = Field(ge=0)


class AuditActivityDayOut(BaseModel):
    """One UTC day of audit activity (last 14 days, oldest first, zero-filled)."""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    logins: int = Field(ge=0)
    denials: int = Field(ge=0)
    record_reads: int = Field(ge=0)


class TopToolOut(BaseModel):
    """One MCP tool's real call count (bounded list, count desc)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    calls: int = Field(ge=1)


class AiUsageOut(BaseModel):
    """Successful AI asks (audit stream) plus the MCP tool calls behind them."""

    model_config = ConfigDict(extra="forbid")

    total_asks: int = Field(ge=0)
    general_asks: int = Field(ge=0)
    patient_asks: int = Field(ge=0)
    top_tools: list[TopToolOut] = Field(max_length=6)


class AnalyticsSummaryOut(BaseModel):
    """GET /api/v1/analytics/summary — one bounded operational overview.

    No query parameters are accepted (the endpoint is one call; unknown query
    parameters are `400 validation_error` per the frozen contract).
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: Timestamp
    counts: CountsOut
    last_30_days: Last30DaysOut
    lab_status_mix: LabStatusMixOut
    abnormal_lab_rate: AbnormalLabRateOut
    top_abnormal_tests: list[TopAbnormalTestOut] = Field(max_length=8)
    prescriptions_by_status: PrescriptionsByStatusOut
    staff_by_role: list[StaffByRoleOut]
    audit_activity: list[AuditActivityDayOut]
    ai_usage: AiUsageOut
    data_provenance: Literal["synthetic"]
