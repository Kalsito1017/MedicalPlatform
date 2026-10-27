"""Operational analytics aggregates over the synthetic demo dataset.

One bounded read model for `GET /api/v1/analytics/summary` (analytics:read,
Admin only). Every number is a REAL aggregate over the existing tables — no
fabricated values, no new tables, no writes. The whole payload is produced by
a handful of grouped/scalar-subquery SELECTs (no N+1; the statement count is
asserted in tests) over bounded demo tables.

Metric definitions (also recorded in docs/API.md "Analytics"):

* Abnormal lab result — THE SAME rule the MCP lookup tools implement and
  document (docs/API.md "MCP tool contract"; app.mcp.tools.find_patients_by_lab
  and app.services.patients): `value_num` STRICTLY outside the recorded
  `[ref_low, ref_high]` (a missing bound is never exceeded). The result's
  `status` is the document state (e.g. `final`) and is NEVER an abnormality
  flag; text-only results (no `value_num`) can never be abnormal. One SQL
  expression (`_ABNORMAL`) feeds every aggregate below so the rate and the
  per-test counts cannot drift apart.
* Evaluable lab result — numeric `value_num` with at least one reference
  bound; only these can be classified, so `abnormal_lab_rate.total` counts
  them (text-only and bound-less results are outside the rate).
* `abnormal_lab_rate.pct` = `abnormal / total * 100` rounded to 1 decimal
  (half up, `0.0` when the total is 0): a rounded DISPLAY number, never a
  clinical measurement (documented choice, docs/API.md).
* `last_30_days` window = `[now - 30 days, now]` UTC; each metric uses the
  record's own event timestamp: encounters by `occurred_at`, labs by
  `collected_at`, prescriptions by `start_date` (the recorded start of the
  course), AI asks / logins / permission denials by the audit `created_at`.
* `audit_activity` — the last 14 UTC days INCLUDING today, oldest first,
  zero-filled. `logins` counts `auth.login.success`; `denials` counts
  `authz.denied`; `record_reads` counts the record-read actions
  (`patient.read`, `patient.summary.read`, `labs.read`, `prescriptions.read`).
* `ai_usage` counts SUCCESSFUL `ai.ask` audit events (outcome `success`):
  `patient_asks` are those audited with a patient subject
  (`resource_type='patient'`) and `general_asks` everything else
  (`ai_lookup`), so the two always partition `total_asks`. `top_tools` counts
  real MCP tool CALLS from the `mcp.<tool>` audit stream (exactly one event
  per call; `mcp.unknown_tool` is a rejection marker, not a tool).
* `counts.ai_conversations` is the row count of `admin.ai_conversation` (the
  persisted per-user history) — deliberately a different source from
  `ai_usage`: clearing personal history shrinks the history table but never
  erases what the system was actually asked.

All timestamps UTC. Every list is bounded: `top_abnormal_tests` <= 8 (count
desc, code asc), `top_tools` <= 6, `staff_by_role` exactly the policy roles,
`audit_activity` exactly 14 days. `data_provenance` is `"synthetic"`.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.policy import ALL_ROLES
from app.mcp.limits import AUDIT_ACTION_PREFIX, UNKNOWN_TOOL_AUDIT_ACTION
from app.models import (
    AiConversation,
    AuditEvent,
    Diagnosis,
    Encounter,
    LabResult,
    Patient,
    Prescription,
    Role,
    StaffUser,
    UserRole,
)

WINDOW_DAYS = 30
AUDIT_ACTIVITY_DAYS = 14
TOP_ABNORMAL_LIMIT = 8
TOP_TOOLS_LIMIT = 6

# Audit actions behind the activity metrics (docs/SECURITY.md audit inventory).
AI_ASK_ACTION = "ai.ask"
LOGIN_ACTION = "auth.login.success"
DENIAL_ACTION = "authz.denied"
RECORD_READ_ACTIONS = (
    "patient.read",
    "patient.summary.read",
    "labs.read",
    "prescriptions.read",
)

# Closed vocabularies (mirroring the model CHECK constraints — the models
# define them inline, so they are restated here once for zero-fill ordering).
PRESCRIPTION_STATUSES = ("active", "completed", "stopped", "cancelled")
LAB_STATUSES = ("final", "preliminary", "pending")

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


def _count(model, *criteria):
    """Scalar subquery `count(*)` over `model` with optional filters."""
    stmt = select(func.count()).select_from(model)
    if criteria:
        stmt = stmt.where(*criteria)
    return stmt.scalar_subquery()


def _pct(abnormal: int, total: int) -> float:
    """Rounded display percentage (1 decimal, half up); 0.0 when total is 0."""
    if total <= 0:
        return 0.0
    value = (Decimal(abnormal) * Decimal(100) / Decimal(total)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return float(value)


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
        "ai_conversations": int(row.ai_conversations),
    }


async def _last_30_days_block(db: AsyncSession, cutoff: datetime) -> dict[str, int]:
    row = (
        await db.execute(
            select(
                _count(
                    Encounter, Encounter.not_deleted(), Encounter.occurred_at >= cutoff
                ).label("encounters"),
                _count(
                    LabResult, LabResult.not_deleted(), LabResult.collected_at >= cutoff
                ).label("lab_results"),
                _count(
                    Prescription,
                    Prescription.not_deleted(),
                    Prescription.start_date >= cutoff.date(),
                ).label("prescriptions_written"),
                _count(
                    AuditEvent,
                    AuditEvent.action == AI_ASK_ACTION,
                    AuditEvent.outcome == "success",
                    AuditEvent.created_at >= cutoff,
                ).label("ai_asks"),
                _count(
                    AuditEvent,
                    AuditEvent.action == LOGIN_ACTION,
                    AuditEvent.created_at >= cutoff,
                ).label("logins"),
                _count(
                    AuditEvent,
                    AuditEvent.action == DENIAL_ACTION,
                    AuditEvent.created_at >= cutoff,
                ).label("permission_denials"),
            )
        )
    ).one()
    return {
        "encounters": int(row.encounters),
        "lab_results": int(row.lab_results),
        "prescriptions_written": int(row.prescriptions_written),
        "ai_asks": int(row.ai_asks),
        "logins": int(row.logins),
        "permission_denials": int(row.permission_denials),
    }


async def _lab_blocks(db: AsyncSession) -> tuple[dict[str, int], dict[str, object], list[dict[str, object]]]:
    mix_rows = (
        await db.execute(
            select(LabResult.status, func.count())
            .where(LabResult.not_deleted())
            .group_by(LabResult.status)
        )
    ).all()
    mix_counts = {str(status): int(count) for status, count in mix_rows}
    lab_status_mix = {status: mix_counts.get(status, 0) for status in LAB_STATUSES}

    rate = (
        await db.execute(
            select(
                _count(LabResult, LabResult.not_deleted(), _ABNORMAL).label("abnormal"),
                _count(LabResult, LabResult.not_deleted(), _EVALUABLE).label("total"),
            )
        )
    ).one()
    abnormal, total = int(rate.abnormal), int(rate.total)
    abnormal_lab_rate = {"abnormal": abnormal, "total": total, "pct": _pct(abnormal, total)}

    top_rows = (
        await db.execute(
            select(
                LabResult.test_code,
                LabResult.test_name,
                func.count().label("abnormal_count"),
            )
            .where(LabResult.not_deleted(), _ABNORMAL)
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
    return lab_status_mix, abnormal_lab_rate, top_abnormal_tests


async def _prescriptions_by_status_block(db: AsyncSession) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Prescription.status, func.count())
            .where(Prescription.not_deleted())
            .group_by(Prescription.status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    return {status: counts.get(status, 0) for status in PRESCRIPTION_STATUSES}


async def _staff_by_role_block(db: AsyncSession) -> list[dict[str, object]]:
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
    counts = {str(key): int(count) for key, count in rows}
    return [{"role": role, "count": counts.get(role, 0)} for role in ALL_ROLES]


async def _audit_activity_block(db: AsyncSession, today) -> list[dict[str, object]]:
    """Last 14 UTC days (oldest first), zero-filled — one grouped query."""
    window_start = datetime.combine(
        today - timedelta(days=AUDIT_ACTIVITY_DAYS - 1), time.min, tzinfo=timezone.utc
    )
    day_expr = func.date_trunc("day", func.timezone("UTC", AuditEvent.created_at))
    rows = (
        await db.execute(
            select(
                day_expr.label("day"),
                func.count().filter(AuditEvent.action == LOGIN_ACTION).label("logins"),
                func.count()
                .filter(AuditEvent.action == DENIAL_ACTION)
                .label("denials"),
                func.count()
                .filter(AuditEvent.action.in_(RECORD_READ_ACTIONS))
                .label("record_reads"),
            )
            .where(AuditEvent.created_at >= window_start)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()
    by_day: dict[str, dict[str, int]] = {}
    for day, logins, denials, record_reads in rows:
        key = day.date().isoformat() if isinstance(day, datetime) else str(day)[:10]
        by_day[key] = {
            "logins": int(logins),
            "denials": int(denials),
            "record_reads": int(record_reads),
        }
    activity: list[dict[str, object]] = []
    for offset in range(AUDIT_ACTIVITY_DAYS):
        day = (today - timedelta(days=AUDIT_ACTIVITY_DAYS - 1 - offset)).isoformat()
        counts = by_day.get(day, {"logins": 0, "denials": 0, "record_reads": 0})
        activity.append({"day": day, **counts})
    return activity


async def _ai_usage_block(db: AsyncSession) -> dict[str, object]:
    asks = (
        await db.execute(
            select(
                _count(
                    AuditEvent,
                    AuditEvent.action == AI_ASK_ACTION,
                    AuditEvent.outcome == "success",
                ).label("total"),
                _count(
                    AuditEvent,
                    AuditEvent.action == AI_ASK_ACTION,
                    AuditEvent.outcome == "success",
                    AuditEvent.resource_type == "patient",
                ).label("patient"),
            )
        )
    ).one()
    total_asks = int(asks.total)
    patient_asks = int(asks.patient)

    tool_rows = (
        await db.execute(
            select(AuditEvent.action, func.count().label("calls"))
            .where(
                AuditEvent.action.startswith(AUDIT_ACTION_PREFIX),
                AuditEvent.action != UNKNOWN_TOOL_AUDIT_ACTION,
            )
            .group_by(AuditEvent.action)
            .order_by(func.count().desc(), AuditEvent.action.asc())
            .limit(TOP_TOOLS_LIMIT)
        )
    ).all()
    top_tools = [
        {"tool": str(action)[len(AUDIT_ACTION_PREFIX) :], "calls": int(calls)}
        for action, calls in tool_rows
    ]
    return {
        "total_asks": total_asks,
        "general_asks": total_asks - patient_asks,
        "patient_asks": patient_asks,
        "top_tools": top_tools,
    }


async def build_summary(db: AsyncSession, *, now: datetime | None = None) -> dict[str, object]:
    """The full analytics payload as a plain dict (validated by the API schema).

    `now` is injectable for deterministic tests; it defaults to the current UTC
    time and anchors every window (30-day activity, 14-day audit chart).
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    cutoff = moment - timedelta(days=WINDOW_DAYS)
    today = moment.date()

    counts = await _counts_block(db)
    last_30_days = await _last_30_days_block(db, cutoff)
    lab_status_mix, abnormal_lab_rate, top_abnormal_tests = await _lab_blocks(db)
    prescriptions_by_status = await _prescriptions_by_status_block(db)
    staff_by_role = await _staff_by_role_block(db)
    audit_activity = await _audit_activity_block(db, today)
    ai_usage = await _ai_usage_block(db)

    return {
        "generated_at": moment,
        "counts": counts,
        "last_30_days": last_30_days,
        "lab_status_mix": lab_status_mix,
        "abnormal_lab_rate": abnormal_lab_rate,
        "top_abnormal_tests": top_abnormal_tests,
        "prescriptions_by_status": prescriptions_by_status,
        "staff_by_role": staff_by_role,
        "audit_activity": audit_activity,
        "ai_usage": ai_usage,
        "data_provenance": "synthetic",
    }
