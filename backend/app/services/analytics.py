"""Operational analytics aggregates over the seeded dataset.

One bounded read model for `GET /api/v1/analytics/summary` (analytics:read,
Admin only). Every number is a REAL aggregate over the existing tables — no
fabricated values, no new tables, no writes. The whole payload is produced by
a bounded set of grouped/scalar-subquery SELECTs (no N+1; the statement count
is asserted in tests) over the selected date range.

Metric definitions (also recorded in docs/API.md "Analytics"):

* Date range — inclusive UTC calendar dates `start`..`end`; the SQL window is
  `[start 00:00Z, end+1 00:00Z)`. Defaults to the last 30 UTC days including
  today. Maximum span is 366 days. The previous period is the equally long
  window immediately before `start`.
* Daily aggregation timezone — clinical/audit series (`encounters`,
  `lab_results`, `prescriptions`, `audit_activity`) bucket by **UTC** day.
  Appointment series bucket by the configured **clinic timezone** because
  working hours and appointment hours are local wall-clock values; this is
  stated in the payload (`range.appointment_timezone`).
* Abnormal lab result — THE SAME rule the MCP lookup tools implement and
  document (docs/API.md "MCP tool contract"; app.mcp.tools.find_patients_by_lab
  and app.services.patients): `value_num` STRICTLY outside the recorded
  `[ref_low, ref_high]` (a missing bound is never exceeded). The result's
  `status` is the document state (e.g. `final`) and is NEVER an abnormality
  flag; text-only results (no `value_num`) can never be abnormal. One SQL
  expression (`_ABNORMAL`) feeds every aggregate below so the rate and the
  per-test counts cannot drift apart.
* Evaluable lab result — numeric `value_num` with at least one reference
  bound; only these can be classified. `labs.classification.normal` is
  `evaluable - abnormal`; `high` and `low` partition `abnormal` (high wins
  when an invalid range would satisfy both); `unclassified` is
  `total - evaluable` (text-only or no usable bound). Missing/invalid
  reference ranges are never silently treated as normal.
* `abnormal_rate.pct` = `abnormal / evaluable * 100` rounded to 1 decimal
  (half up, `0.0` when the denominator is 0): a rounded DISPLAY number, never
  a clinical measurement (documented choice, docs/API.md).
* Patient metrics — `new_patients` counts patients created in the window;
  `returning_patients` counts distinct patients with an encounter in the
  window whose patient row was created before it; `active_patients` counts
  every distinct patient with an encounter in the window.
* Appointment metrics — counts use `start_at` in the window. `cancelled`
  appointments are excluded from booked minutes; scheduled, confirmed,
  completed and no-show minutes are considered booked. `available_minutes`
  comes only from recorded `scheduling.working_hours` for each local weekday,
  clipped to the window, minus overlapping `scheduling.time_off`; it is never
  guessed from a default schedule. `utilization_pct` is `booked / available`
  rounded to 1 decimal, or null when no availability is recorded.
* Access/security — successful and failed sign-ins, `authz.denied`, and the
  record-read actions (`patient.read`, `patient.summary.read`, `labs.read`,
  `prescriptions.read`) from the audit stream. `ai_requests` counts every
  `ai.ask` event (success + error + denied).
* AI usage — success rate is `successful / (successful + failed)` (denied asks
  are excluded), null when that denominator is 0. MCP `top_tools` counts real
  `mcp.<tool>` calls in the window; `failures` counts outcomes other than
  success. Per-request AI latency and voice metrics are NOT persisted by
  design, so the payload reports them as unavailable instead of inventing a
  number (docs/API.md, docs/SECURITY.md).
* Data quality — only fields whose absence is a real recorded gap are listed;
  optional metadata gaps are labelled as optional, never as errors.
* Staff — active accounts per policy role plus current inactive accounts;
  no per-person performance data is exposed.

All timestamps UTC. Every list is bounded: `top_abnormal_tests` <= 8,
`top_tools` <= 6, encounter/visit-type lists <= 20, weekday list exactly 7,
hour list exactly 24, day series exactly the selected range, data-quality
checks <= 8. Data provenance is database-internal and never serializes into
this payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.policy import ALL_ROLES
from app.mcp.limits import AUDIT_ACTION_PREFIX, UNKNOWN_TOOL_AUDIT_ACTION
from app.models import (
    AiConversation,
    Appointment,
    AuditEvent,
    Diagnosis,
    Encounter,
    LabResult,
    Patient,
    Prescription,
    Role,
    StaffUser,
    TimeOff,
    UserRole,
    WorkingHours,
)

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366
TOP_ABNORMAL_LIMIT = 8
TOP_TOOLS_LIMIT = 6
TOP_TYPE_LIMIT = 20

# Audit actions behind the activity metrics (docs/SECURITY.md audit inventory).
AI_ASK_ACTION = "ai.ask"
LOGIN_ACTION = "auth.login.success"
LOGIN_FAILURE_ACTION = "auth.login.failure"
DENIAL_ACTION = "authz.denied"
PATIENT_READ_ACTIONS = ("patient.read", "patient.summary.read")
LAB_READ_ACTION = "labs.read"
PRESCRIPTION_READ_ACTION = "prescriptions.read"
RECORD_READ_ACTIONS = (*PATIENT_READ_ACTIONS, LAB_READ_ACTION, PRESCRIPTION_READ_ACTION)

# Closed vocabularies (mirroring the model CHECK constraints).
PRESCRIPTION_STATUSES = ("active", "completed", "stopped", "cancelled")
LAB_STATUSES = ("final", "preliminary", "pending")
APPOINTMENT_STATUSES = ("scheduled", "confirmed", "completed", "cancelled", "no_show")
WEEKDAY_LABELS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# --- the abnormal rule (ONE definition for every aggregate) ------------------
#
# Mirrors app.mcp.tools.find_patients_by_lab / app.services.patients exactly:
# numeric value STRICTLY outside the recorded bounds; `status` never used;
# text-only results never qualify.
_ABNORMAL = or_(
    and_(
        LabResult.ref_high.is_not(None),
        LabResult.value_num.is_not(None),
        LabResult.value_num > LabResult.ref_high,
    ),
    and_(
        LabResult.ref_low.is_not(None),
        LabResult.value_num.is_not(None),
        LabResult.value_num < LabResult.ref_low,
    ),
)

# Classifiable results: numeric value with at least one reference bound.
_EVALUABLE = and_(
    LabResult.value_num.is_not(None),
    or_(LabResult.ref_low.is_not(None), LabResult.ref_high.is_not(None)),
)

_HIGH = and_(
    LabResult.ref_high.is_not(None),
    LabResult.value_num.is_not(None),
    LabResult.value_num > LabResult.ref_high,
)

# Low excludes high so high + low always partitions the abnormal union even if
# a malformed range has ref_low > ref_high.
_LOW = and_(
    LabResult.ref_low.is_not(None),
    LabResult.value_num.is_not(None),
    LabResult.value_num < LabResult.ref_low,
    not_(_HIGH),
)

_MISSING_REFS = and_(LabResult.ref_low.is_(None), LabResult.ref_high.is_(None))


@dataclass(frozen=True)
class Period:
    """Inclusive UTC calendar dates plus the SQL half-open window."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=timezone.utc)

    @property
    def end_at(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=timezone.utc)

    @property
    def previous(self) -> "Period":
        previous_end = self.start - timedelta(days=1)
        return Period(self.start - timedelta(days=self.days), previous_end)


def resolve_period(
    *, now: datetime, start: date | None = None, end: date | None = None
) -> Period:
    """Validate the requested range and resolve the default/previous windows.

    Exactly both or neither query boundary must be supplied. Invalid order or
    an unbounded span raises `ValidationFailed` (400 validation_error).
    """
    from app.errors import ValidationFailed

    if (start is None) != (end is None):
        raise ValidationFailed(
            "Both start and end are required for a custom range.",
            details={
                "fields": [
                    {
                        "loc": "query.start" if start is None else "query.end",
                        "msg": "Both boundaries are required.",
                        "type": "range_boundary",
                    }
                ]
            },
        )

    if start is None or end is None:
        end = now.astimezone(timezone.utc).date()
        start = end - timedelta(days=DEFAULT_RANGE_DAYS - 1)

    if end < start:
        raise ValidationFailed(
            "The date range end must be on or after start.",
            details={
                "fields": [
                    {
                        "loc": "query.end",
                        "msg": "Must be on or after start.",
                        "type": "range_order",
                    }
                ]
            },
        )
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValidationFailed(
            f"The analytics range may not exceed {MAX_RANGE_DAYS} days.",
            details={
                "fields": [
                    {
                        "loc": "query.end",
                        "msg": f"Maximum span is {MAX_RANGE_DAYS} days.",
                        "type": "range_too_large",
                    }
                ]
            },
        )
    return Period(start=start, end=end)


def _count(model, *criteria):
    """Scalar subquery `count(*)` over `model` with optional filters."""
    stmt = select(func.count()).select_from(model)
    if criteria:
        stmt = stmt.where(*criteria)
    return stmt.scalar_subquery()


def _pct(part: int, total: int) -> float:
    """Rounded display percentage (1 decimal, half up); 0.0 when total is 0."""
    if total <= 0:
        return 0.0
    value = (Decimal(part) * Decimal(100) / Decimal(total)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return float(value)


def _ratio(part: int, total: int) -> float | None:
    """Rounded display percentage, or null when the denominator is unknown."""
    return None if total <= 0 else _pct(part, total)


def _change_pct(current: int, previous: int) -> float | None:
    """Period-over-period change; null when the previous period had no events."""
    if previous <= 0:
        return None
    value = (
        (Decimal(current) - Decimal(previous))
        * Decimal(100)
        / Decimal(previous)
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return float(value)


def _day_keys(period: Period) -> list[str]:
    return [
        (period.start + timedelta(days=offset)).isoformat()
        for offset in range(period.days)
    ]


def _appointment_day_keys(period: Period, timezone_name: str) -> list[str]:
    """Clinic-local calendar days overlapping the UTC window.

    A positive/negative UTC offset can expose one partial local day at either
    edge, so this list may be one entry longer than `range.days`; every
    appointment still falls into exactly one bucket and the series always sums
    to `appointments.total`.
    """
    zone = ZoneInfo(timezone_name)
    first = period.start_at.astimezone(zone).date()
    last = period.end_at.astimezone(zone).date()
    return [
        (first + timedelta(days=offset)).isoformat()
        for offset in range((last - first).days + 1)
    ]


def _day_key(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _utc_day(column):
    return func.date_trunc("day", func.timezone("UTC", column))


async def _counts_block(db: AsyncSession) -> dict[str, int]:
    row = (
        await db.execute(
            select(
                _count(Patient, Patient.not_deleted()).label("patients"),
                _count(Encounter, Encounter.not_deleted()).label("encounters"),
                _count(LabResult, LabResult.not_deleted()).label("labs"),
                _count(Prescription, Prescription.not_deleted()).label("prescriptions"),
                _count(
                    Prescription,
                    Prescription.not_deleted(),
                    Prescription.status == "active",
                ).label("active_prescriptions"),
                _count(
                    Diagnosis,
                    Diagnosis.not_deleted(),
                    Diagnosis.status == "active",
                ).label("diagnoses_active"),
                _count(
                    StaffUser,
                    StaffUser.not_deleted(),
                    StaffUser.is_active.is_(True),
                ).label("staff_active"),
                _count(
                    StaffUser,
                    StaffUser.not_deleted(),
                    StaffUser.is_active.is_(False),
                ).label("staff_inactive"),
                _count(AiConversation).label("ai_conversations"),
            )
        )
    ).one()
    return {
        "patients": int(row.patients),
        "encounters": int(row.encounters),
        "labs": int(row.labs),
        "prescriptions": int(row.prescriptions),
        "active_prescriptions": int(row.active_prescriptions),
        "diagnoses_active": int(row.diagnoses_active),
        "staff_active": int(row.staff_active),
        "staff_inactive": int(row.staff_inactive),
        "ai_conversations": int(row.ai_conversations),
    }


async def _activity_block(db: AsyncSession, period: Period) -> dict[str, int]:
    """All headline activity counts for one window (one SELECT)."""
    start_at, end_at = period.start_at, period.end_at
    row = (
        await db.execute(
            select(
                _count(
                    Patient,
                    Patient.not_deleted(),
                    Patient.created_at >= start_at,
                    Patient.created_at < end_at,
                ).label("new_patients"),
                _count(
                    Encounter,
                    Encounter.not_deleted(),
                    Encounter.occurred_at >= start_at,
                    Encounter.occurred_at < end_at,
                ).label("encounters"),
                _count(
                    LabResult,
                    LabResult.not_deleted(),
                    LabResult.collected_at >= start_at,
                    LabResult.collected_at < end_at,
                ).label("lab_results"),
                _count(
                    Prescription,
                    Prescription.not_deleted(),
                    Prescription.start_date >= period.start,
                    Prescription.start_date <= period.end,
                ).label("prescriptions_written"),
                _count(
                    Appointment,
                    Appointment.start_at >= start_at,
                    Appointment.start_at < end_at,
                ).label("appointments"),
                _count(
                    AuditEvent,
                    AuditEvent.action == AI_ASK_ACTION,
                    AuditEvent.created_at >= start_at,
                    AuditEvent.created_at < end_at,
                ).label("ai_requests"),
                _count(
                    AuditEvent,
                    AuditEvent.action == LOGIN_ACTION,
                    AuditEvent.created_at >= start_at,
                    AuditEvent.created_at < end_at,
                ).label("successful_signins"),
                _count(
                    AuditEvent,
                    AuditEvent.action == LOGIN_FAILURE_ACTION,
                    AuditEvent.created_at >= start_at,
                    AuditEvent.created_at < end_at,
                ).label("failed_signins"),
                _count(
                    AuditEvent,
                    AuditEvent.action == DENIAL_ACTION,
                    AuditEvent.created_at >= start_at,
                    AuditEvent.created_at < end_at,
                ).label("permission_denials"),
            )
        )
    ).one()
    return {
        "new_patients": int(row.new_patients),
        "encounters": int(row.encounters),
        "lab_results": int(row.lab_results),
        "prescriptions_written": int(row.prescriptions_written),
        "appointments": int(row.appointments),
        "ai_requests": int(row.ai_requests),
        "successful_signins": int(row.successful_signins),
        "failed_signins": int(row.failed_signins),
        "permission_denials": int(row.permission_denials),
    }


COMPARISON_METRICS = (
    "new_patients",
    "encounters",
    "lab_results",
    "prescriptions_written",
    "appointments",
    "ai_requests",
)


def _comparisons_block(current: dict[str, int], previous: dict[str, int]) -> dict[str, object]:
    return {
        metric: {
            "current": current[metric],
            "previous": previous[metric],
            "change_pct": _change_pct(current[metric], previous[metric]),
        }
        for metric in COMPARISON_METRICS
    }


async def _patients_block(
    db: AsyncSession, period: Period, activity: dict[str, int], timezone_name: str
) -> dict[str, object]:
    start_at, end_at = period.start_at, period.end_at
    row = (
        await db.execute(
            select(
                func.count(func.distinct(Encounter.patient_id))
                .filter(Patient.created_at < start_at)
                .label("returning_patients"),
                func.count(func.distinct(Encounter.patient_id)).label("active_patients"),
            )
            .select_from(Encounter)
            .join(Patient, Patient.id == Encounter.patient_id)
            .where(
                Encounter.not_deleted(),
                Patient.not_deleted(),
                Encounter.occurred_at >= start_at,
                Encounter.occurred_at < end_at,
            )
        )
    ).one()

    type_rows = (
        await db.execute(
            select(Encounter.encounter_type, func.count().label("count"))
            .where(
                Encounter.not_deleted(),
                Encounter.occurred_at >= start_at,
                Encounter.occurred_at < end_at,
            )
            .group_by(Encounter.encounter_type)
            .order_by(func.count().desc(), Encounter.encounter_type.asc())
            .limit(TOP_TYPE_LIMIT)
        )
    ).all()

    day_expr = _utc_day(Encounter.occurred_at)
    trend_rows = (
        await db.execute(
            select(day_expr.label("day"), func.count().label("count"))
            .where(
                Encounter.not_deleted(),
                Encounter.occurred_at >= start_at,
                Encounter.occurred_at < end_at,
            )
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()
    by_day = {_day_key(day): int(count) for day, count in trend_rows}
    trend = [{"day": day, "encounters": by_day.get(day, 0)} for day in _day_keys(period)]

    encounters = activity["encounters"]
    return {
        "new_patients": activity["new_patients"],
        "returning_patients": int(row.returning_patients),
        "active_patients": int(row.active_patients),
        "encounters": encounters,
        "avg_encounters_per_day": float(
            (Decimal(encounters) / Decimal(period.days)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        ),
        "by_encounter_type": [
            {"encounter_type": str(encounter_type), "count": int(count)}
            for encounter_type, count in type_rows
        ],
        "trend": trend,
        "timezone": timezone_name,
    }


async def _labs_block(db: AsyncSession, period: Period) -> dict[str, object]:
    start_at, end_at = period.start_at, period.end_at
    window = (
        LabResult.not_deleted(),
        LabResult.collected_at >= start_at,
        LabResult.collected_at < end_at,
    )
    row = (
        await db.execute(
            select(
                _count(LabResult, *window).label("total"),
                _count(LabResult, *window, _EVALUABLE).label("evaluable"),
                _count(LabResult, *window, _ABNORMAL).label("abnormal"),
                _count(LabResult, *window, _HIGH).label("high"),
                _count(LabResult, *window, _LOW).label("low"),
                _count(LabResult, *window, _MISSING_REFS).label("missing_reference_ranges"),
                _count(LabResult, *window, LabResult.status == "final").label("final"),
                _count(LabResult, *window, LabResult.status == "preliminary").label("preliminary"),
                _count(LabResult, *window, LabResult.status == "pending").label("pending"),
            )
        )
    ).one()
    total = int(row.total)
    evaluable = int(row.evaluable)
    abnormal = int(row.abnormal)
    high = int(row.high)
    low = int(row.low)
    normal = max(0, evaluable - abnormal)

    top_rows = (
        await db.execute(
            select(
                LabResult.test_code,
                LabResult.test_name,
                func.count().label("abnormal_count"),
            )
            .where(*window, _ABNORMAL)
            .group_by(LabResult.test_code, LabResult.test_name)
            .order_by(
                func.count().desc(),
                LabResult.test_code.asc(),
                LabResult.test_name.asc(),
            )
            .limit(TOP_ABNORMAL_LIMIT)
        )
    ).all()
    top_abnormal_tests = [
        {
            "test_code": str(code),
            "test_name": str(name),
            "abnormal_count": int(count),
        }
        for code, name, count in top_rows
    ]

    day_expr = _utc_day(LabResult.collected_at)
    trend_rows = (
        await db.execute(
            select(
                day_expr.label("day"),
                func.count().label("total"),
                func.count().filter(_EVALUABLE).label("evaluable"),
                func.count().filter(_ABNORMAL).label("abnormal"),
            )
            .select_from(LabResult)
            .where(*window)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()
    by_day = {
        _day_key(day): {
            "total": int(day_total),
            "evaluable": int(day_evaluable),
            "abnormal": int(day_abnormal),
        }
        for day, day_total, day_evaluable, day_abnormal in trend_rows
    }
    trend = [
        {
            "day": day,
            **by_day.get(day, {"total": 0, "evaluable": 0, "abnormal": 0}),
        }
        for day in _day_keys(period)
    ]

    return {
        "total": total,
        "status_mix": {
            "final": int(row.final),
            "preliminary": int(row.preliminary),
            "pending": int(row.pending),
        },
        "abnormal_rate": {"abnormal": abnormal, "total": evaluable, "pct": _pct(abnormal, evaluable)},
        "classification": {
            "normal": normal,
            "high": high,
            "low": low,
            "unclassified": max(0, total - evaluable),
            "missing_reference_ranges": int(row.missing_reference_ranges),
            "reference_coverage_pct": _pct(evaluable, total),
        },
        "top_abnormal_tests": top_abnormal_tests,
        "trend": trend,
        "timezone": "UTC",
    }


async def _prescriptions_block(db: AsyncSession, period: Period) -> dict[str, object]:
    period_filters = (
        Prescription.not_deleted(),
        Prescription.start_date >= period.start,
        Prescription.start_date <= period.end,
    )
    row = (
        await db.execute(
            select(
                *[
                    _count(Prescription, *period_filters, Prescription.status == status).label(
                        f"period_{status}"
                    )
                    for status in PRESCRIPTION_STATUSES
                ],
                *[
                    _count(
                        Prescription,
                        Prescription.not_deleted(),
                        Prescription.status == status,
                    ).label(f"lifetime_{status}")
                    for status in PRESCRIPTION_STATUSES
                ],
            )
        )
    ).one()
    period_by_status = {
        status: int(getattr(row, f"period_{status}")) for status in PRESCRIPTION_STATUSES
    }
    lifetime_by_status = {
        status: int(getattr(row, f"lifetime_{status}")) for status in PRESCRIPTION_STATUSES
    }

    day_expr = Prescription.start_date
    trend_rows = (
        await db.execute(
            select(day_expr.label("day"), func.count().label("count"))
            .where(*period_filters)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()
    by_day = {_day_key(day): int(count) for day, count in trend_rows}
    trend = [
        {"day": day, "prescriptions": by_day.get(day, 0)} for day in _day_keys(period)
    ]

    return {
        "total": sum(period_by_status.values()),
        "by_status": period_by_status,
        "lifetime_by_status": lifetime_by_status,
        "trend": trend,
        "timezone": "UTC",
        # Kept explicit so the UI never labels a date-only course start as a
        # precise timestamp.
        "date_basis": "start_date",
    }


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _clip(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> tuple[datetime, datetime] | None:
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    return (clipped_start, clipped_end) if clipped_end > clipped_start else None


def _overlap_seconds(
    first: tuple[datetime, datetime], second: tuple[datetime, datetime]
) -> float:
    start = max(first[0], second[0])
    end = min(first[1], second[1])
    return max(0.0, (end - start).total_seconds())


async def _working_minutes(
    db: AsyncSession, period: Period, timezone_name: str
) -> tuple[int, set[object]]:
    """Recorded clinic-wide availability and the doctors who own it.

    Availability is generated ONLY from real `working_hours` rows of active,
    non-deleted staff for the local weekdays in the window, clipped to the UTC
    window, then reduced by merged overlapping `time_off`. Doctors without
    working-hour rows contribute neither availability nor booked minutes.
    This is bounded scheduling metadata, not clinical data.
    """
    zone = ZoneInfo(timezone_name)
    hour_rows = (
        await db.execute(
            select(
                WorkingHours.doctor_id,
                WorkingHours.weekday,
                WorkingHours.start_time,
                WorkingHours.end_time,
            )
            .join(StaffUser, StaffUser.id == WorkingHours.doctor_id)
            .where(StaffUser.not_deleted(), StaffUser.is_active.is_(True))
        )
    ).all()
    doctor_ids = {row.doctor_id for row in hour_rows}
    if not doctor_ids:
        return 0, set()

    by_doctor_weekday: dict[tuple[object, int], list[tuple[time, time]]] = {}
    for doctor_id, weekday, start_time, end_time in hour_rows:
        by_doctor_weekday.setdefault((doctor_id, int(weekday)), []).append(
            (start_time, end_time)
        )

    local_start = period.start_at.astimezone(zone).date()
    local_end = period.end_at.astimezone(zone).date()
    work_by_doctor: dict[object, list[tuple[datetime, datetime]]] = {}
    local_day = local_start
    while local_day <= local_end:
        weekday = local_day.weekday()
        for doctor_id in doctor_ids:
            for start_time, end_time in by_doctor_weekday.get((doctor_id, weekday), []):
                start_local = datetime.combine(local_day, start_time, tzinfo=zone)
                end_local = datetime.combine(local_day, end_time, tzinfo=zone)
                clipped = _clip(
                    start_local.astimezone(timezone.utc),
                    end_local.astimezone(timezone.utc),
                    period.start_at,
                    period.end_at,
                )
                if clipped is not None:
                    work_by_doctor.setdefault(doctor_id, []).append(clipped)
        local_day += timedelta(days=1)

    time_off_rows = (
        await db.execute(
            select(TimeOff.doctor_id, TimeOff.start_at, TimeOff.end_at).where(
                TimeOff.doctor_id.in_(doctor_ids),
                TimeOff.start_at < period.end_at,
                TimeOff.end_at > period.start_at,
            )
        )
    ).all()
    off_by_doctor: dict[object, list[tuple[datetime, datetime]]] = {}
    for off_doctor_id, off_start, off_end in time_off_rows:
        clipped_off = _clip(off_start, off_end, period.start_at, period.end_at)
        if clipped_off is not None:
            off_by_doctor.setdefault(off_doctor_id, []).append(clipped_off)

    available_seconds = 0.0
    for doctor_id, intervals in work_by_doctor.items():
        merged = _merge_intervals(intervals)
        available_seconds += sum(
            (end - start).total_seconds() for start, end in merged
        )
        merged_off = _merge_intervals(off_by_doctor.get(doctor_id, []))
        available_seconds -= sum(
            _overlap_seconds(interval, off_interval)
            for interval in merged
            for off_interval in merged_off
        )

    # Only doctors with at least one working interval in this window own
    # availability (and therefore booked-minute utilization) for the window.
    return max(0, round(available_seconds / 60)), set(work_by_doctor)


async def _appointments_block(
    db: AsyncSession, period: Period, timezone_name: str
) -> dict[str, object]:
    start_at, end_at = period.start_at, period.end_at
    window = (Appointment.start_at >= start_at, Appointment.start_at < end_at)
    status_row = (
        await db.execute(
            select(
                func.count().label("total"),
                *[
                    func.count()
                    .filter(Appointment.status == status)
                    .label(status)
                    for status in APPOINTMENT_STATUSES
                ],
            )
            .select_from(Appointment)
            .where(*window)
        )
    ).one()
    by_status = {status: int(getattr(status_row, status)) for status in APPOINTMENT_STATUSES}

    visit_type_expr = func.coalesce(Appointment.appointment_type, "Unspecified")
    visit_rows = (
        await db.execute(
            select(visit_type_expr.label("visit_type"), func.count().label("count"))
            .where(*window)
            .group_by(visit_type_expr)
            .order_by(func.count().desc(), visit_type_expr.asc())
            .limit(TOP_TYPE_LIMIT)
        )
    ).all()

    local_start = func.timezone(timezone_name, Appointment.start_at)
    weekday_expr = func.mod(func.extract("dow", local_start) + 6, 7)
    weekday_rows = (
        await db.execute(
            select(weekday_expr.label("weekday"), func.count().label("count"))
            .where(*window)
            .group_by(weekday_expr)
            .order_by(weekday_expr)
        )
    ).all()
    weekday_counts = {int(weekday): int(count) for weekday, count in weekday_rows}
    by_weekday = [
        {
            "weekday": weekday,
            "label": WEEKDAY_LABELS[weekday],
            "count": weekday_counts.get(weekday, 0),
        }
        for weekday in range(7)
    ]

    hour_expr = func.extract("hour", local_start)
    hour_rows = (
        await db.execute(
            select(hour_expr.label("hour"), func.count().label("count"))
            .where(*window)
            .group_by(hour_expr)
            .order_by(hour_expr)
        )
    ).all()
    hour_counts = {int(hour): int(count) for hour, count in hour_rows}
    by_hour = [{"hour": hour, "count": hour_counts.get(hour, 0)} for hour in range(24)]

    day_expr = func.date_trunc("day", local_start)
    trend_rows = (
        await db.execute(
            select(
                day_expr.label("day"),
                func.count().label("total"),
                func.count().filter(Appointment.status == "completed").label("completed"),
                func.count().filter(Appointment.status == "cancelled").label("cancelled"),
                func.count().filter(Appointment.status == "no_show").label("no_show"),
            )
            .select_from(Appointment)
            .where(*window)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()
    by_day = {
        _day_key(day): {
            "total": int(day_total),
            "completed": int(completed),
            "cancelled": int(cancelled),
            "no_show": int(no_show),
        }
        for day, day_total, completed, cancelled, no_show in trend_rows
    }
    trend = [
        {
            "day": day,
            **by_day.get(day, {"total": 0, "completed": 0, "cancelled": 0, "no_show": 0}),
        }
        for day in _appointment_day_keys(period, timezone_name)
    ]

    available_minutes, scheduled_doctor_ids = await _working_minutes(
        db, period, timezone_name
    )
    duration_seconds = func.extract(
        "epoch",
        func.least(Appointment.end_at, end_at)
        - func.greatest(Appointment.start_at, start_at),
    )
    booked_filters = [
        Appointment.start_at < end_at,
        Appointment.end_at > start_at,
        Appointment.status != "cancelled",
    ]
    if not scheduled_doctor_ids:
        booked_minutes = 0
    else:
        booked = (
            await db.execute(
                select(func.coalesce(func.sum(duration_seconds), 0)).where(
                    *booked_filters,
                    Appointment.doctor_id.in_(scheduled_doctor_ids),
                )
            )
        ).scalar_one()
        booked_minutes = max(0, round(float(booked) / 60))
    doctors_with_schedule = len(scheduled_doctor_ids)

    return {
        "total": int(status_row.total),
        "by_status": by_status,
        "by_visit_type": [
            {"visit_type": str(visit_type), "count": int(count)}
            for visit_type, count in visit_rows
        ],
        "by_weekday": by_weekday,
        "by_hour": by_hour,
        "trend": trend,
        "utilization": {
            "booked_minutes": booked_minutes,
            "available_minutes": available_minutes,
            "utilization_pct": _ratio(booked_minutes, available_minutes),
            "doctors_with_schedule": doctors_with_schedule,
        },
        "timezone": timezone_name,
    }


async def _security_block(db: AsyncSession, period: Period) -> dict[str, object]:
    start_at, end_at = period.start_at, period.end_at
    day_expr = _utc_day(AuditEvent.created_at)
    rows = (
        await db.execute(
            select(
                day_expr.label("day"),
                func.count().filter(AuditEvent.action == LOGIN_ACTION).label("successful_signins"),
                func.count()
                .filter(AuditEvent.action == LOGIN_FAILURE_ACTION)
                .label("failed_signins"),
                func.count().filter(AuditEvent.action == DENIAL_ACTION).label("permission_denials"),
                func.count()
                .filter(AuditEvent.action.in_(PATIENT_READ_ACTIONS))
                .label("patient_reads"),
                func.count()
                .filter(AuditEvent.action == LAB_READ_ACTION)
                .label("lab_reads"),
                func.count()
                .filter(AuditEvent.action == PRESCRIPTION_READ_ACTION)
                .label("prescription_reads"),
                func.count().filter(AuditEvent.action == AI_ASK_ACTION).label("ai_requests"),
            )
            .select_from(AuditEvent)
            .where(AuditEvent.created_at >= start_at, AuditEvent.created_at < end_at)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()
    series_keys = (
        "successful_signins",
        "failed_signins",
        "permission_denials",
        "patient_reads",
        "lab_reads",
        "prescription_reads",
        "ai_requests",
    )
    by_day: dict[str, dict[str, int]] = {}
    for day, *values in rows:
        by_day[_day_key(day)] = dict(zip(series_keys, (int(value) for value in values)))

    trend = []
    totals = {key: 0 for key in series_keys}
    for day in _day_keys(period):
        entry = {"day": day, **{key: 0 for key in series_keys}}
        entry.update(by_day.get(day, {}))
        trend.append(entry)
        for key in series_keys:
            totals[key] += int(entry[key])

    return {"totals": totals, "trend": trend, "timezone": "UTC"}


async def _ai_usage_block(db: AsyncSession, period: Period) -> dict[str, object]:
    start_at, end_at = period.start_at, period.end_at
    window = (
        AuditEvent.action == AI_ASK_ACTION,
        AuditEvent.created_at >= start_at,
        AuditEvent.created_at < end_at,
    )
    row = (
        await db.execute(
            select(
                _count(AuditEvent, *window, AuditEvent.outcome == "success").label("successful"),
                _count(AuditEvent, *window, AuditEvent.outcome == "error").label("failed"),
                _count(AuditEvent, *window, AuditEvent.outcome == "denied").label("denied"),
            )
        )
    ).one()
    successful = int(row.successful)
    failed = int(row.failed)
    denied = int(row.denied)

    tool_rows = (
        await db.execute(
            select(
                AuditEvent.action,
                func.count().label("calls"),
                func.count().filter(AuditEvent.outcome != "success").label("failures"),
            )
            .where(
                AuditEvent.action.startswith(AUDIT_ACTION_PREFIX),
                AuditEvent.action != UNKNOWN_TOOL_AUDIT_ACTION,
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            )
            .group_by(AuditEvent.action)
            .order_by(func.count().desc(), AuditEvent.action.asc())
            .limit(TOP_TOOLS_LIMIT)
        )
    ).all()
    top_tools = [
        {
            "tool": str(action)[len(AUDIT_ACTION_PREFIX) :],
            "calls": int(calls),
            "failures": int(failures),
        }
        for action, calls, failures in tool_rows
    ]

    return {
        "total_requests": successful + failed + denied,
        "successful": successful,
        "failed": failed,
        "denied": denied,
        "success_rate": _ratio(successful, successful + failed),
        "top_tools": top_tools,
        "latency": {
            "available": False,
            "average_ms": None,
            "reason": (
                "Per-request AI latency is not persisted; audit events store "
                "metadata only."
            ),
        },
        "voice": {
            "available": False,
            "reason": (
                "Voice transcription requests are intentionally ephemeral and "
                "never audited, so no volume, language, duration or latency "
                "aggregate exists."
            ),
        },
        "timezone": "UTC",
    }


async def _staff_block(db: AsyncSession, counts: dict[str, int]) -> dict[str, object]:
    rows = (
        await db.execute(
            select(Role.key, func.count(func.distinct(UserRole.user_id)))
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .join(StaffUser, StaffUser.id == UserRole.user_id)
            .where(StaffUser.not_deleted(), StaffUser.is_active.is_(True))
            .group_by(Role.key)
        )
    ).all()
    role_counts = {str(key): int(count) for key, count in rows}
    return {
        "active_total": counts["staff_active"],
        "inactive_total": counts["staff_inactive"],
        "by_role": [
            {"role": role, "count": role_counts.get(role, 0)} for role in ALL_ROLES
        ],
    }


async def _data_quality_block(db: AsyncSession, period: Period) -> dict[str, object]:
    start_at, end_at = period.start_at, period.end_at
    lab_window = (
        LabResult.not_deleted(),
        LabResult.collected_at >= start_at,
        LabResult.collected_at < end_at,
    )
    encounter_window = (
        Encounter.not_deleted(),
        Encounter.occurred_at >= start_at,
        Encounter.occurred_at < end_at,
    )
    appointment_window = (
        Appointment.start_at >= start_at,
        Appointment.start_at < end_at,
    )
    row = (
        await db.execute(
            select(
                _count(LabResult, *lab_window).label("lab_total"),
                _count(LabResult, *lab_window, _MISSING_REFS).label("lab_missing_refs"),
                _count(LabResult, *lab_window, not_(_EVALUABLE)).label("lab_unclassified"),
                _count(Patient, Patient.not_deleted()).label("patient_total"),
                _count(Patient, Patient.not_deleted(), Patient.date_of_birth.is_(None)).label(
                    "patient_missing_dob"
                ),
                _count(Patient, Patient.not_deleted(), Patient.blood_type.is_(None)).label(
                    "patient_missing_blood_type"
                ),
                _count(Encounter, *encounter_window).label("encounter_total"),
                _count(
                    Encounter, *encounter_window, Encounter.practitioner_id.is_(None)
                ).label("encounter_missing_practitioner"),
                _count(Appointment, *appointment_window).label("appointment_total"),
                _count(
                    Appointment,
                    *appointment_window,
                    or_(
                        Appointment.appointment_type.is_(None),
                        Appointment.appointment_type == "",
                    ),
                ).label("appointment_missing_type"),
            )
        )
    ).one()

    checks = [
        {
            "key": "lab_missing_reference_ranges",
            "label": "Lab results missing reference ranges",
            "count": int(row.lab_missing_refs),
            "denominator": int(row.lab_total),
            "scope": "period",
            "kind": "classification",
            "note": "A missing range cannot be classified and is never counted as normal.",
        },
        {
            "key": "lab_unclassified",
            "label": "Unclassified lab results",
            "count": int(row.lab_unclassified),
            "denominator": int(row.lab_total),
            "scope": "period",
            "kind": "classification",
            "note": "Text-only results or numeric results without a usable bound.",
        },
        {
            "key": "patient_missing_date_of_birth",
            "label": "Patients without a recorded date of birth",
            "count": int(row.patient_missing_dob),
            "denominator": int(row.patient_total),
            "scope": "current",
            "kind": "optional_metadata",
            "note": "Optional demographic field; not a data error.",
        },
        {
            "key": "patient_missing_blood_type",
            "label": "Patients without a recorded blood type",
            "count": int(row.patient_missing_blood_type),
            "denominator": int(row.patient_total),
            "scope": "current",
            "kind": "optional_metadata",
            "note": "Optional clinical field; blank means not recorded.",
        },
        {
            "key": "encounter_missing_practitioner",
            "label": "Encounters without a practitioner",
            "count": int(row.encounter_missing_practitioner),
            "denominator": int(row.encounter_total),
            "scope": "period",
            "kind": "optional_metadata",
            "note": "Practitioner attribution is nullable in the encounter model.",
        },
        {
            "key": "appointment_missing_visit_type",
            "label": "Appointments without a visit type",
            "count": int(row.appointment_missing_type),
            "denominator": int(row.appointment_total),
            "scope": "period",
            "kind": "optional_metadata",
            "note": "Visit type is optional scheduling metadata.",
        },
    ]
    return {"checks": checks}


async def _system_health_block() -> dict[str, object]:
    """Only facts this request can verify; no synthetic probes or numbers."""
    from app.config import get_settings
    from app.llm.provider import NullProvider, build_provider

    settings = get_settings()
    llm_configured = not isinstance(build_provider(settings), NullProvider)
    return {
        "backend": "operational",
        "database": "connected",
        "api_error_rate": {
            "available": False,
            "rate_pct": None,
            "reason": (
                "API error rates are not persisted in the audit stream; use "
                "the request logs/metrics instead."
            ),
        },
        "llm": {
            "configured": llm_configured,
            "availability": "not_monitored",
            "note": "No persisted provider-health probe; AI ask outcomes are reported above.",
        },
        "voice": {
            "configured": settings.whisper_base_url is not None,
            "availability": "not_monitored",
            "note": "Transcription requests are intentionally not audited or persisted.",
        },
    }


async def build_summary(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, object]:
    """The full analytics payload as a plain dict (validated by the API schema).

    `now` is injectable for deterministic tests; it defaults to the current UTC
    time and anchors the default range. `start`/`end` are inclusive calendar
    dates; the previous period is derived from the same length.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)

    period = resolve_period(now=moment, start=start, end=end)
    previous = period.previous

    from app.config import get_settings

    timezone_name = get_settings().clinic_timezone

    counts = await _counts_block(db)
    activity = await _activity_block(db, period)
    previous_activity = await _activity_block(db, previous)
    patients = await _patients_block(db, period, activity, "UTC")
    labs = await _labs_block(db, period)
    prescriptions = await _prescriptions_block(db, period)
    appointments = await _appointments_block(db, period, timezone_name)
    security = await _security_block(db, period)
    ai_usage = await _ai_usage_block(db, period)
    staff = await _staff_block(db, counts)
    data_quality = await _data_quality_block(db, period)
    system_health = await _system_health_block()

    return {
        "generated_at": moment,
        "range": {
            "start": period.start,
            "end": period.end,
            "days": period.days,
            "previous_start": previous.start,
            "previous_end": previous.end,
            "timezone": "UTC",
            "appointment_timezone": timezone_name,
        },
        "counts": counts,
        "activity": activity,
        "previous_activity": previous_activity,
        "comparisons": _comparisons_block(activity, previous_activity),
        "patients": patients,
        "appointments": appointments,
        "labs": labs,
        "prescriptions": prescriptions,
        "security": security,
        "ai_usage": ai_usage,
        "data_quality": data_quality,
        "system_health": system_health,
        "staff": staff,
    }
