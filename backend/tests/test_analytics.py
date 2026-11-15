"""Analytics summary tests (docs/API.md "Analytics").

Guarantees asserted here:
  1. `analytics:read` enforcement at the API layer: Admin 200, doctor/nurse
     403, anonymous 401 (the MATRIX guard additionally declares the route).
  2. Validated UTC date ranges: defaults, custom ranges, previous-period
     derivation, maximum span, malformed/partial boundaries, and empty ranges.
  3. The response shape is EXACT and typed (ints are ints, bounded lists,
     zero-filled series; provenance is database-internal and not serialized).
  4. Every number matches a direct query over the database (real aggregates,
     never hardcoded guesses — fixtures vary between runs).
  5. The abnormal-lab rule is THE MCP rule with mutually exclusive
     normal/high/low/unclassified buckets and an explicit denominator.
  6. Appointment status/visit-type/weekday/hour/trend aggregates and
     booked-vs-available utilization come from real scheduling rows.
  7. Access/security and AI aggregates partition real audit events; unpersisted
     latency/voice metrics are explicitly unavailable.
  8. Each read writes exactly one `analytics.read` audit event (metadata only).
  9. Bounded query count (no N+1) and a latency sanity bound.
"""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import audit_rows  # noqa: F401
from test_permissions_api import MATRIX

SUMMARY_URL = "/api/v1/analytics/summary"

EXPECTED_TOP_KEYS = {
    "generated_at",
    "range",
    "counts",
    "activity",
    "previous_activity",
    "comparisons",
    "patients",
    "appointments",
    "labs",
    "prescriptions",
    "security",
    "ai_usage",
    "data_quality",
    "system_health",
    "staff",
}
ACTIVITY_KEYS = {
    "new_patients",
    "encounters",
    "lab_results",
    "prescriptions_written",
    "appointments",
    "ai_requests",
    "successful_signins",
    "failed_signins",
    "permission_denials",
}
COMPARISON_KEYS = {
    "new_patients",
    "encounters",
    "lab_results",
    "prescriptions_written",
    "appointments",
    "ai_requests",
}
SECURITY_KEYS = {
    "successful_signins",
    "failed_signins",
    "permission_denials",
    "patient_reads",
    "lab_reads",
    "prescription_reads",
    "ai_requests",
}


async def _direct_count(db, model, *criteria) -> int:
    stmt = select(func.count()).select_from(model)
    if criteria:
        stmt = stmt.where(*criteria)
    return int((await db.scalar(stmt)) or 0)


async def _summary(db_engine, **kwargs) -> dict:
    from app.services.analytics import build_summary

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        return await build_summary(db, **kwargs)


def _expected_change(current: int, previous: int) -> float | None:
    if previous <= 0:
        return None
    return float(
        ((Decimal(current) - Decimal(previous)) * Decimal(100) / Decimal(previous)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    )


@pytest.mark.postgres
async def test_analytics_route_is_declared_in_the_permission_matrix() -> None:
    declared = {(case.method, case.template) for case in MATRIX}
    assert ("GET", SUMMARY_URL) in declared


@pytest.mark.postgres
async def test_summary_requires_analytics_read(auth_client, make_user, make_client) -> None:
    anonymous = await make_client()
    response = await anonymous.get(SUMMARY_URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"

    for role in ("doctor", "nurse"):
        client = await auth_client(await make_user(roles=(role,)))
        response = await client.get(SUMMARY_URL)
        assert response.status_code == 403, role
        assert response.json()["error"]["code"] == "forbidden"

    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(SUMMARY_URL)
    assert response.status_code == 200, response.text


@pytest.mark.postgres
async def test_summary_validates_query_parameters(auth_client, make_user) -> None:
    client = await auth_client(await make_user(roles=("clinical_admin",)))

    for bad in ("?limit=1", "?days=7", "?extra=1", "?start=2026-01-01", "?end=2026-01-31"):
        response = await client.get(f"{SUMMARY_URL}{bad}")
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "validation_error"

    for bad in (
        "?start=not-a-date&end=2026-01-31",
        "?start=2026-02-01&end=2026-01-01",
        "?start=2025-01-01&end=2026-01-02",
    ):
        response = await client.get(f"{SUMMARY_URL}{bad}")
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "validation_error"

    response = await client.get(f"{SUMMARY_URL}?start=2026-01-01&end=2026-01-07")
    assert response.status_code == 200, response.text


@pytest.mark.postgres
async def test_default_and_custom_ranges_are_utc_and_have_a_previous_period(
    auth_client, make_user
) -> None:
    client = await auth_client(await make_user(roles=("clinical_admin",)))

    default = (await client.get(SUMMARY_URL)).json()["range"]
    today = datetime.now(timezone.utc).date()
    assert default["end"] == today.isoformat()
    assert default["start"] == (today - timedelta(days=29)).isoformat()
    assert default["days"] == 30
    assert default["previous_end"] == (date.fromisoformat(default["start"]) - timedelta(days=1)).isoformat()
    assert default["previous_start"] == (
        date.fromisoformat(default["start"]) - timedelta(days=30)
    ).isoformat()
    assert default["timezone"] == "UTC"
    assert default["appointment_timezone"]

    custom = (
        await client.get(f"{SUMMARY_URL}?start=2026-10-01&end=2026-10-07")
    ).json()["range"]
    assert custom["start"] == "2026-10-01"
    assert custom["end"] == "2026-10-07"
    assert custom["days"] == 7
    assert custom["previous_start"] == "2026-09-24"
    assert custom["previous_end"] == "2026-09-30"


@pytest.mark.postgres
async def test_summary_response_shape_is_exact_and_typed(auth_client, make_user) -> None:
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(f"{SUMMARY_URL}?start=2026-10-01&end=2026-10-14")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == EXPECTED_TOP_KEYS
    assert isinstance(body["generated_at"], str) and body["generated_at"].endswith("Z")
    datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))

    assert set(body["counts"]) == {
        "patients",
        "encounters",
        "labs",
        "prescriptions",
        "active_prescriptions",
        "diagnoses_active",
        "staff_active",
        "staff_inactive",
        "ai_conversations",
    }
    assert set(body["activity"]) == ACTIVITY_KEYS
    assert set(body["previous_activity"]) == ACTIVITY_KEYS
    assert set(body["comparisons"]) == COMPARISON_KEYS
    for metric, entry in body["comparisons"].items():
        assert set(entry) == {"current", "previous", "change_pct"}, metric
        assert entry["current"] == body["activity"][metric]
        assert entry["previous"] == body["previous_activity"][metric]
        if entry["previous"] == 0:
            assert entry["change_pct"] is None, metric
        else:
            assert isinstance(entry["change_pct"], (int, float)), metric

    assert set(body["patients"]) == {
        "new_patients",
        "returning_patients",
        "active_patients",
        "encounters",
        "avg_encounters_per_day",
        "by_encounter_type",
        "trend",
        "timezone",
    }
    assert set(body["appointments"]) == {
        "total",
        "by_status",
        "by_visit_type",
        "by_weekday",
        "by_hour",
        "trend",
        "utilization",
        "timezone",
    }
    assert set(body["appointments"]["by_status"]) == {
        "scheduled",
        "confirmed",
        "completed",
        "cancelled",
        "no_show",
    }
    assert set(body["appointments"]["utilization"]) == {
        "booked_minutes",
        "available_minutes",
        "utilization_pct",
        "doctors_with_schedule",
    }
    assert set(body["labs"]) == {
        "total",
        "status_mix",
        "abnormal_rate",
        "classification",
        "top_abnormal_tests",
        "trend",
        "timezone",
    }
    assert set(body["labs"]["status_mix"]) == {"final", "preliminary", "pending"}
    assert set(body["labs"]["abnormal_rate"]) == {"abnormal", "total", "pct"}
    assert set(body["labs"]["classification"]) == {
        "normal",
        "high",
        "low",
        "unclassified",
        "missing_reference_ranges",
        "reference_coverage_pct",
    }
    assert set(body["prescriptions"]) == {
        "total",
        "by_status",
        "lifetime_by_status",
        "trend",
        "timezone",
        "date_basis",
    }
    assert set(body["security"]) == {"totals", "trend", "timezone"}
    assert set(body["security"]["totals"]) == SECURITY_KEYS
    assert set(body["ai_usage"]) == {
        "total_requests",
        "successful",
        "failed",
        "denied",
        "success_rate",
        "top_tools",
        "latency",
        "voice",
        "timezone",
    }
    assert set(body["ai_usage"]["latency"]) == {"available", "average_ms", "reason"}
    assert set(body["ai_usage"]["voice"]) == {"available", "reason"}
    assert set(body["data_quality"]) == {"checks"}
    assert set(body["system_health"]) == {
        "backend",
        "database",
        "api_error_rate",
        "llm",
        "voice",
    }
    assert set(body["staff"]) == {"active_total", "inactive_total", "by_role"}

    days = body["range"]["days"]
    for block in ("patients", "labs", "prescriptions", "security"):
        assert len(body[block]["trend"]) == days, block
    # Appointment buckets are clinic-local days overlapping the UTC window;
    # a non-UTC clinic offset may expose one partial edge day.
    assert days <= len(body["appointments"]["trend"]) <= days + 1
    for entry in body["patients"]["trend"]:
        assert set(entry) == {"day", "encounters"}
    for entry in body["labs"]["trend"]:
        assert set(entry) == {"day", "total", "evaluable", "abnormal"}
    for entry in body["prescriptions"]["trend"]:
        assert set(entry) == {"day", "prescriptions"}
    for entry in body["appointments"]["trend"]:
        assert set(entry) == {"day", "total", "completed", "cancelled", "no_show"}
    for entry in body["security"]["trend"]:
        assert set(entry) == {"day", *SECURITY_KEYS}
    assert [entry["weekday"] for entry in body["appointments"]["by_weekday"]] == list(range(7))
    assert [entry["hour"] for entry in body["appointments"]["by_hour"]] == list(range(24))
    assert len(body["appointments"]["by_weekday"]) == 7
    assert len(body["appointments"]["by_hour"]) == 24
    assert len(body["appointments"]["by_visit_type"]) <= 20
    assert len(body["labs"]["top_abnormal_tests"]) <= 8
    assert len(body["ai_usage"]["top_tools"]) <= 6
    assert len(body["data_quality"]["checks"]) <= 8

    for block in ("counts", "activity", "previous_activity"):
        for key, value in body[block].items():
            assert isinstance(value, int) and not isinstance(value, bool), (block, key)
            assert value >= 0, (block, key)
    for block in ("appointments", "labs", "prescriptions", "security", "ai_usage"):
        for key in ("total", "total_requests"):
            if key in body[block]:
                assert isinstance(body[block][key], int), (block, key)
    rate = body["labs"]["abnormal_rate"]
    assert isinstance(rate["pct"], (int, float)) and not isinstance(rate["pct"], bool)
    assert 0.0 <= float(rate["pct"]) <= 100.0
    assert rate["abnormal"] <= rate["total"]

    classification = body["labs"]["classification"]
    assert (
        classification["normal"] + classification["high"] + classification["low"]
        + classification["unclassified"]
        == body["labs"]["total"]
    )
    assert classification["high"] + classification["low"] == rate["abnormal"]
    assert classification["normal"] + rate["abnormal"] == rate["total"]
    status_sum = sum(body["labs"]["status_mix"].values())
    assert status_sum == body["labs"]["total"]

    appointment_total = body["appointments"]["total"]
    assert sum(body["appointments"]["by_status"].values()) == appointment_total
    assert sum(entry["count"] for entry in body["appointments"]["by_weekday"]) == appointment_total
    assert sum(entry["count"] for entry in body["appointments"]["by_hour"]) == appointment_total
    assert sum(entry["total"] for entry in body["appointments"]["trend"]) == appointment_total

    assert sum(body["prescriptions"]["by_status"].values()) == body["prescriptions"]["total"]
    for key in SECURITY_KEYS:
        assert body["security"]["totals"][key] == sum(
            entry[key] for entry in body["security"]["trend"]
        )
    ai = body["ai_usage"]
    assert ai["total_requests"] == ai["successful"] + ai["failed"] + ai["denied"]
    if ai["successful"] + ai["failed"] == 0:
        assert ai["success_rate"] is None
    else:
        assert float(ai["success_rate"]) == pytest.approx(
            100 * ai["successful"] / (ai["successful"] + ai["failed"]), abs=0.05
        )
    assert ai["latency"]["available"] is False and ai["latency"]["average_ms"] is None
    assert ai["voice"]["available"] is False
    assert body["system_health"]["backend"] == "operational"
    assert body["system_health"]["database"] == "connected"
    assert body["system_health"]["api_error_rate"]["available"] is False

    assert len(body["staff"]["by_role"]) == 3
    assert {entry["role"] for entry in body["staff"]["by_role"]} == {
        "doctor",
        "nurse",
        "clinical_admin",
    }
    for check in body["data_quality"]["checks"]:
        assert set(check) == {
            "key",
            "label",
            "count",
            "denominator",
            "scope",
            "kind",
            "note",
        }
        assert check["count"] <= check["denominator"]


@pytest.mark.postgres
async def test_summary_numbers_match_direct_queries(db_engine, auth_client, make_user) -> None:
    """Real aggregates: headline/patient/prescription/security/AI blocks."""
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(f"{SUMMARY_URL}?start=2026-10-01&end=2026-10-14")
    assert response.status_code == 200
    body = response.json()

    start_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 10, 15, tzinfo=timezone.utc)
    previous_start = datetime(2026, 9, 17, tzinfo=timezone.utc)
    previous_end = start_at

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
        UserRole,
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        expected_counts = {
            "patients": await _direct_count(db, Patient, Patient.deleted_at.is_(None)),
            "encounters": await _direct_count(db, Encounter, Encounter.deleted_at.is_(None)),
            "labs": await _direct_count(db, LabResult, LabResult.deleted_at.is_(None)),
            "prescriptions": await _direct_count(
                db, Prescription, Prescription.deleted_at.is_(None)
            ),
            "active_prescriptions": await _direct_count(
                db,
                Prescription,
                Prescription.deleted_at.is_(None),
                Prescription.status == "active",
            ),
            "diagnoses_active": await _direct_count(
                db,
                Diagnosis,
                Diagnosis.deleted_at.is_(None),
                Diagnosis.status == "active",
            ),
            "staff_active": await _direct_count(
                db, StaffUser, StaffUser.deleted_at.is_(None), StaffUser.is_active.is_(True)
            ),
            "staff_inactive": await _direct_count(
                db, StaffUser, StaffUser.deleted_at.is_(None), StaffUser.is_active.is_(False)
            ),
            "ai_conversations": await _direct_count(db, AiConversation),
        }
        assert body["counts"] == expected_counts

        expected_activity = {
            "new_patients": await _direct_count(
                db,
                Patient,
                Patient.deleted_at.is_(None),
                Patient.created_at >= start_at,
                Patient.created_at < end_at,
            ),
            "encounters": await _direct_count(
                db,
                Encounter,
                Encounter.deleted_at.is_(None),
                Encounter.occurred_at >= start_at,
                Encounter.occurred_at < end_at,
            ),
            "lab_results": await _direct_count(
                db,
                LabResult,
                LabResult.deleted_at.is_(None),
                LabResult.collected_at >= start_at,
                LabResult.collected_at < end_at,
            ),
            "prescriptions_written": await _direct_count(
                db,
                Prescription,
                Prescription.deleted_at.is_(None),
                Prescription.start_date >= date(2026, 10, 1),
                Prescription.start_date <= date(2026, 10, 14),
            ),
            "appointments": await _direct_count(
                db,
                Appointment,
                Appointment.start_at >= start_at,
                Appointment.start_at < end_at,
            ),
            "ai_requests": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "ai.ask",
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            ),
            "successful_signins": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "auth.login.success",
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            ),
            "failed_signins": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "auth.login.failure",
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            ),
            "permission_denials": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "authz.denied",
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            ),
        }
        assert body["activity"] == expected_activity

        expected_previous = {
            "new_patients": await _direct_count(
                db,
                Patient,
                Patient.deleted_at.is_(None),
                Patient.created_at >= previous_start,
                Patient.created_at < previous_end,
            ),
            "encounters": await _direct_count(
                db,
                Encounter,
                Encounter.deleted_at.is_(None),
                Encounter.occurred_at >= previous_start,
                Encounter.occurred_at < previous_end,
            ),
            "lab_results": await _direct_count(
                db,
                LabResult,
                LabResult.deleted_at.is_(None),
                LabResult.collected_at >= previous_start,
                LabResult.collected_at < previous_end,
            ),
            "prescriptions_written": await _direct_count(
                db,
                Prescription,
                Prescription.deleted_at.is_(None),
                Prescription.start_date >= date(2026, 9, 17),
                Prescription.start_date <= date(2026, 9, 30),
            ),
            "appointments": await _direct_count(
                db,
                Appointment,
                Appointment.start_at >= previous_start,
                Appointment.start_at < previous_end,
            ),
            "ai_requests": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "ai.ask",
                AuditEvent.created_at >= previous_start,
                AuditEvent.created_at < previous_end,
            ),
            "successful_signins": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "auth.login.success",
                AuditEvent.created_at >= previous_start,
                AuditEvent.created_at < previous_end,
            ),
            "failed_signins": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "auth.login.failure",
                AuditEvent.created_at >= previous_start,
                AuditEvent.created_at < previous_end,
            ),
            "permission_denials": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "authz.denied",
                AuditEvent.created_at >= previous_start,
                AuditEvent.created_at < previous_end,
            ),
        }
        assert body["previous_activity"] == expected_previous
        for metric, entry in body["comparisons"].items():
            assert entry["change_pct"] == pytest.approx(
                _expected_change(entry["current"], entry["previous"]), abs=0.05
            ) if entry["previous"] > 0 else entry["change_pct"] is None

        # Patient/encounter operational block.
        distinct_encounters = (
            select(Encounter.patient_id)
            .join(Patient, Patient.id == Encounter.patient_id)
            .where(
                Encounter.deleted_at.is_(None),
                Patient.deleted_at.is_(None),
                Encounter.occurred_at >= start_at,
                Encounter.occurred_at < end_at,
            )
            .distinct()
            .subquery()
        )
        active_patients = int(
            await db.scalar(select(func.count()).select_from(distinct_encounters)) or 0
        )
        returning_patients = int(
            await db.scalar(
                select(func.count())
                .select_from(distinct_encounters)
                .join(Patient, Patient.id == distinct_encounters.c.patient_id)
                .where(Patient.created_at < start_at)
            )
            or 0
        )
        assert body["patients"]["active_patients"] == active_patients
        assert body["patients"]["returning_patients"] == returning_patients
        assert body["patients"]["new_patients"] == expected_activity["new_patients"]
        assert body["patients"]["encounters"] == expected_activity["encounters"]

        encounter_type_rows = (
            await db.execute(
                select(Encounter.encounter_type, func.count())
                .where(
                    Encounter.deleted_at.is_(None),
                    Encounter.occurred_at >= start_at,
                    Encounter.occurred_at < end_at,
                )
                .group_by(Encounter.encounter_type)
                .order_by(func.count().desc(), Encounter.encounter_type.asc())
                .limit(20)
            )
        ).all()
        assert body["patients"]["by_encounter_type"] == [
            {"encounter_type": str(key), "count": int(count)}
            for key, count in encounter_type_rows
        ]

        # Prescription period + lifetime status mixes.
        period_rx_rows = (
            await db.execute(
                select(Prescription.status, func.count())
                .where(
                    Prescription.deleted_at.is_(None),
                    Prescription.start_date >= date(2026, 10, 1),
                    Prescription.start_date <= date(2026, 10, 14),
                )
                .group_by(Prescription.status)
            )
        ).all()
        period_rx = {str(status): int(count) for status, count in period_rx_rows}
        assert body["prescriptions"]["by_status"] == {
            key: period_rx.get(key, 0)
            for key in ("active", "completed", "stopped", "cancelled")
        }
        assert body["prescriptions"]["total"] == sum(period_rx.values())

        lifetime_rx_rows = (
            await db.execute(
                select(Prescription.status, func.count())
                .where(Prescription.deleted_at.is_(None))
                .group_by(Prescription.status)
            )
        ).all()
        lifetime_rx = {str(status): int(count) for status, count in lifetime_rx_rows}
        assert body["prescriptions"]["lifetime_by_status"] == {
            key: lifetime_rx.get(key, 0)
            for key in ("active", "completed", "stopped", "cancelled")
        }

        # Access/security totals.
        action_map = {
            "successful_signins": ("auth.login.success",),
            "failed_signins": ("auth.login.failure",),
            "permission_denials": ("authz.denied",),
            "patient_reads": ("patient.read", "patient.summary.read"),
            "lab_reads": ("labs.read",),
            "prescription_reads": ("prescriptions.read",),
            "ai_requests": ("ai.ask",),
        }
        expected_security = {
            key: await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action.in_(actions),
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            )
            for key, actions in action_map.items()
        }
        assert body["security"]["totals"] == expected_security

        # AI outcome partition.
        expected_ai = {
            "successful": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "ai.ask",
                AuditEvent.outcome == "success",
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            ),
            "failed": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "ai.ask",
                AuditEvent.outcome == "error",
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            ),
            "denied": await _direct_count(
                db,
                AuditEvent,
                AuditEvent.action == "ai.ask",
                AuditEvent.outcome == "denied",
                AuditEvent.created_at >= start_at,
                AuditEvent.created_at < end_at,
            ),
        }
        assert {key: body["ai_usage"][key] for key in expected_ai} == expected_ai
        assert body["ai_usage"]["total_requests"] == sum(expected_ai.values())

        # Staff compact block.
        role_rows = (
            await db.execute(
                select(Role.key, func.count(func.distinct(UserRole.user_id)))
                .select_from(UserRole)
                .join(Role, Role.id == UserRole.role_id)
                .join(StaffUser, StaffUser.id == UserRole.user_id)
                .where(StaffUser.deleted_at.is_(None), StaffUser.is_active.is_(True))
                .group_by(Role.key)
            )
        ).all()
        role_counts = {str(key): int(count) for key, count in role_rows}
        assert body["staff"]["by_role"] == [
            {"role": role, "count": role_counts.get(role, 0)}
            for role in ("doctor", "nurse", "clinical_admin")
        ]
        assert body["staff"]["active_total"] == expected_counts["staff_active"]
        assert body["staff"]["inactive_total"] == expected_counts["staff_inactive"]


@pytest.mark.postgres
async def test_labs_and_appointments_match_direct_queries(db_engine, auth_client, make_user) -> None:
    """Lab classification and appointment status/trend blocks are real."""
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(f"{SUMMARY_URL}?start=2026-10-01&end=2026-10-14")
    assert response.status_code == 200
    body = response.json()
    start_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 10, 15, tzinfo=timezone.utc)

    from app.models import Appointment, LabResult

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        lab_rows = (
            await db.execute(
                select(
                    LabResult.test_code,
                    LabResult.test_name,
                    LabResult.value_num,
                    LabResult.ref_low,
                    LabResult.ref_high,
                    LabResult.status,
                ).where(
                    LabResult.deleted_at.is_(None),
                    LabResult.collected_at >= start_at,
                    LabResult.collected_at < end_at,
                )
            )
        ).all()

        def is_high(value, high):
            return value is not None and high is not None and value > high

        def is_low(value, low):
            return value is not None and low is not None and value < low

        evaluable = [
            row for row in lab_rows if row.value_num is not None and (row.ref_low is not None or row.ref_high is not None)
        ]
        abnormal = [
            row for row in lab_rows if is_high(row.value_num, row.ref_high) or is_low(row.value_num, row.ref_low)
        ]
        high = [row for row in lab_rows if is_high(row.value_num, row.ref_high)]
        low = [
            row
            for row in lab_rows
            if is_low(row.value_num, row.ref_low) and not is_high(row.value_num, row.ref_high)
        ]
        missing_refs = [row for row in lab_rows if row.ref_low is None and row.ref_high is None]

        assert body["labs"]["total"] == len(lab_rows)
        assert body["labs"]["abnormal_rate"]["abnormal"] == len(abnormal)
        assert body["labs"]["abnormal_rate"]["total"] == len(evaluable)
        assert body["labs"]["classification"] == {
            "normal": len(evaluable) - len(abnormal),
            "high": len(high),
            "low": len(low),
            "unclassified": len(lab_rows) - len(evaluable),
            "missing_reference_ranges": len(missing_refs),
            "reference_coverage_pct": pytest.approx(
                round(100 * len(evaluable) / len(lab_rows), 1) if lab_rows else 0.0,
                abs=0.05,
            ),
        }

        status_rows = (
            await db.execute(
                select(LabResult.status, func.count())
                .where(
                    LabResult.deleted_at.is_(None),
                    LabResult.collected_at >= start_at,
                    LabResult.collected_at < end_at,
                )
                .group_by(LabResult.status)
            )
        ).all()
        status_counts = {str(status): int(count) for status, count in status_rows}
        assert body["labs"]["status_mix"] == {
            key: status_counts.get(key, 0) for key in ("final", "preliminary", "pending")
        }

        appointment_rows = (
            await db.execute(
                select(Appointment.status, Appointment.appointment_type, func.count())
                .where(
                    Appointment.start_at >= start_at,
                    Appointment.start_at < end_at,
                )
                .group_by(Appointment.status, Appointment.appointment_type)
            )
        ).all()
        status_totals: dict[str, int] = {}
        type_totals: dict[str, int] = {}
        for status, appointment_type, count in appointment_rows:
            status_totals[str(status)] = status_totals.get(str(status), 0) + int(count)
            key = str(appointment_type) if appointment_type is not None else "Unspecified"
            type_totals[key] = type_totals.get(key, 0) + int(count)
        assert body["appointments"]["total"] == sum(status_totals.values())
        assert body["appointments"]["by_status"] == {
            key: status_totals.get(key, 0)
            for key in ("scheduled", "confirmed", "completed", "cancelled", "no_show")
        }
        # The bounded list may omit low-volume types; every returned entry is
        # still a real grouped count.
        for entry in body["appointments"]["by_visit_type"]:
            assert entry["count"] == type_totals.get(entry["visit_type"], 0)

        # Utilization is internally consistent with its own denominator.
        utilization = body["appointments"]["utilization"]
        assert utilization["booked_minutes"] >= 0
        assert utilization["available_minutes"] >= 0
        if utilization["available_minutes"] == 0:
            assert utilization["utilization_pct"] is None
        else:
            assert float(utilization["utilization_pct"]) == pytest.approx(
                100 * utilization["booked_minutes"] / utilization["available_minutes"],
                abs=0.05,
            )


@pytest.mark.postgres
async def test_empty_range_is_zero_filled_and_comparisons_are_null(auth_client, make_user) -> None:
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    response = await client.get(f"{SUMMARY_URL}?start=2000-01-01&end=2000-01-07")
    assert response.status_code == 200
    body = response.json()

    assert body["range"]["days"] == 7
    for key, value in body["activity"].items():
        assert value == 0, key
    for key, value in body["previous_activity"].items():
        assert value == 0, key
    for metric, entry in body["comparisons"].items():
        assert entry["current"] == 0 and entry["previous"] == 0, metric
        assert entry["change_pct"] is None, metric

    assert body["patients"]["new_patients"] == 0
    assert body["patients"]["returning_patients"] == 0
    assert body["patients"]["active_patients"] == 0
    assert body["patients"]["encounters"] == 0
    assert all(entry["encounters"] == 0 for entry in body["patients"]["trend"])
    assert body["appointments"]["total"] == 0
    assert all(entry["total"] == 0 for entry in body["appointments"]["trend"])
    assert sum(entry["count"] for entry in body["appointments"]["by_weekday"]) == 0
    assert sum(entry["count"] for entry in body["appointments"]["by_hour"]) == 0
    assert body["labs"]["total"] == 0
    assert body["labs"]["abnormal_rate"] == {"abnormal": 0, "total": 0, "pct": 0.0}
    assert body["labs"]["classification"]["unclassified"] == 0
    assert body["prescriptions"]["total"] == 0
    assert body["security"]["totals"] == {key: 0 for key in SECURITY_KEYS}
    assert body["ai_usage"]["total_requests"] == 0
    assert body["ai_usage"]["success_rate"] is None

    for check in body["data_quality"]["checks"]:
        if check["scope"] == "period":
            assert check["count"] == 0, check["key"]
            assert check["denominator"] == 0, check["key"]


@pytest.mark.postgres
async def test_comparisons_use_the_immediately_preceding_equal_window(
    db_engine, clinical_data
) -> None:
    """One current + one previous encounter move exactly the documented windows."""
    from app.models import Encounter

    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    period = {"start": date(2026, 10, 1), "end": date(2026, 10, 7)}
    before = await _summary(db_engine, now=now, **period)

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        db.add_all(
            [
                Encounter(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    occurred_at=datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
                    encounter_type=f"comparison-current-{clinical_data.token}",
                    summary="Synthetic comparison fixture (current).",
                ),
                Encounter(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    occurred_at=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
                    encounter_type=f"comparison-previous-{clinical_data.token}",
                    summary="Synthetic comparison fixture (previous).",
                ),
            ]
        )
        await db.commit()

    after = await _summary(db_engine, now=now, **period)
    assert after["activity"]["encounters"] == before["activity"]["encounters"] + 1
    assert after["previous_activity"]["encounters"] == before["previous_activity"]["encounters"] + 1
    comparison = after["comparisons"]["encounters"]
    assert comparison["current"] == after["activity"]["encounters"]
    assert comparison["previous"] == after["previous_activity"]["encounters"]
    assert comparison["change_pct"] == pytest.approx(
        _expected_change(comparison["current"], comparison["previous"]), abs=0.05
    )


@pytest.mark.postgres
async def test_utc_day_boundaries_are_inclusive_start_exclusive_end(
    db_engine, clinical_data
) -> None:
    """A record exactly at 00:00Z belongs to its day; the next midnight does not."""
    from app.models import Encounter

    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    period = {"start": date(2026, 10, 1), "end": date(2026, 10, 1)}
    before = await _summary(db_engine, now=now, **period)

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        db.add_all(
            [
                Encounter(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    occurred_at=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
                    encounter_type=f"boundary-start-{clinical_data.token}",
                    summary="Synthetic boundary fixture (included at start).",
                ),
                Encounter(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    occurred_at=datetime(2026, 10, 1, 23, 59, 59, tzinfo=timezone.utc),
                    encounter_type=f"boundary-end-{clinical_data.token}",
                    summary="Synthetic boundary fixture (included at end of day).",
                ),
                Encounter(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    occurred_at=datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc),
                    encounter_type=f"boundary-next-{clinical_data.token}",
                    summary="Synthetic boundary fixture (excluded).",
                ),
            ]
        )
        await db.commit()

    after = await _summary(db_engine, now=now, **period)
    assert after["activity"]["encounters"] == before["activity"]["encounters"] + 2
    day = next(entry for entry in after["patients"]["trend"] if entry["day"] == "2026-10-01")
    before_day = next(
        entry for entry in before["patients"]["trend"] if entry["day"] == "2026-10-01"
    )
    assert day["encounters"] == before_day["encounters"] + 2


@pytest.mark.postgres
async def test_appointment_utilization_uses_real_working_hours_and_time_off(
    db_engine, clinical_data, make_user, monkeypatch
) -> None:
    """Booked minutes / recorded available minutes, proven with a one-day fixture."""
    from app.config import get_settings
    from app.models import Appointment, DoctorSchedule, TimeOff, WorkingHours

    monkeypatch.setattr("app.services.analytics.TOP_TYPE_LIMIT", 500)
    doctor = await make_user(roles=("doctor",))
    zone = ZoneInfo(get_settings().clinic_timezone)
    day = date(2030, 1, 7)  # Monday
    period = {"start": day, "end": day}
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)

    baseline = await _summary(db_engine, now=now, **period)

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        db.add(DoctorSchedule(doctor_id=doctor.id))
        db.add(
            WorkingHours(
                id=uuid.uuid4(),
                doctor_id=doctor.id,
                weekday=0,
                start_time=dt_time(9, 0),
                end_time=dt_time(12, 0),
            )
        )
        await db.commit()
    with_schedule = await _summary(db_engine, now=now, **period)
    assert (
        with_schedule["appointments"]["utilization"]["available_minutes"]
        - baseline["appointments"]["utilization"]["available_minutes"]
        == 180
    )
    assert (
        with_schedule["appointments"]["utilization"]["doctors_with_schedule"]
        == baseline["appointments"]["utilization"]["doctors_with_schedule"] + 1
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        db.add(
            TimeOff(
                id=uuid.uuid4(),
                doctor_id=doctor.id,
                start_at=datetime.combine(day, dt_time(10, 0), tzinfo=zone).astimezone(
                    timezone.utc
                ),
                end_at=datetime.combine(day, dt_time(11, 0), tzinfo=zone).astimezone(
                    timezone.utc
                ),
                reason="Analytics utilization probe",
            )
        )
        await db.commit()
    with_time_off = await _summary(db_engine, now=now, **period)
    assert (
        with_schedule["appointments"]["utilization"]["available_minutes"]
        - with_time_off["appointments"]["utilization"]["available_minutes"]
        == 60
    )

    def at(hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, dt_time(hour, minute), tzinfo=zone).astimezone(
            timezone.utc
        )

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        db.add_all(
            [
                Appointment(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    doctor_id=doctor.id,
                    start_at=at(9),
                    end_at=at(10),
                    appointment_type="Analytics Probe",
                    status="scheduled",
                ),
                Appointment(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    doctor_id=doctor.id,
                    start_at=at(10, 30),
                    end_at=at(11, 30),
                    appointment_type="Analytics Probe",
                    status="completed",
                ),
                Appointment(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    doctor_id=doctor.id,
                    start_at=at(11, 30),
                    end_at=at(12, 30),
                    appointment_type="Analytics Probe",
                    status="cancelled",
                ),
            ]
        )
        await db.commit()
    with_appointments = await _summary(db_engine, now=now, **period)

    appointments = with_appointments["appointments"]
    before_appointments = with_time_off["appointments"]
    assert appointments["total"] == before_appointments["total"] + 3
    for status, delta in (("scheduled", 1), ("completed", 1), ("cancelled", 1)):
        assert (
            appointments["by_status"][status]
            - before_appointments["by_status"][status]
            == delta
        ), status
    assert (
        appointments["utilization"]["booked_minutes"]
        - before_appointments["utilization"]["booked_minutes"]
        == 120
    )
    probe = next(
        entry for entry in appointments["by_visit_type"] if entry["visit_type"] == "Analytics Probe"
    )
    assert probe["count"] >= 3
    assert sum(entry["count"] for entry in appointments["by_weekday"]) == appointments["total"]
    assert sum(entry["count"] for entry in appointments["by_hour"]) == appointments["total"]
    assert sum(entry["total"] for entry in appointments["trend"]) == appointments["total"]
    assert appointments["trend"][0]["total"] == appointments["total"]
    if appointments["utilization"]["available_minutes"] == 0:
        assert appointments["utilization"]["utilization_pct"] is None
    else:
        assert float(appointments["utilization"]["utilization_pct"]) == pytest.approx(
            100
            * appointments["utilization"]["booked_minutes"]
            / appointments["utilization"]["available_minutes"],
            abs=0.05,
        )


@pytest.mark.postgres
async def test_security_and_ai_aggregates_match_crafted_audit_events(
    db_engine, make_user, monkeypatch
) -> None:
    from app.models import AuditEvent

    monkeypatch.setattr("app.services.analytics.TOP_TOOLS_LIMIT", 500)
    account = await make_user(roles=("doctor",))
    token = uuid.uuid4().hex[:8]
    # Use a REAL inventory tool action so the immutable audit stream stays
    # valid for the audit-inventory guard (no invented mcp.<fake> actions).
    probe_tool = "get_patient_summary"
    start = datetime(2031, 1, 1, tzinfo=timezone.utc)
    period = {"start": date(2031, 1, 1), "end": date(2031, 1, 7)}
    now = datetime(2031, 1, 9, 12, tzinfo=timezone.utc)

    # The test database persists between runs and other suites inspect the
    # newest audit row for a real tool action. Remove only this test's probe
    # rows (identified by resource_type) before and after, so the immutable
    # audit inventory is never polluted by fixture traffic.
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        await db.execute(delete(AuditEvent).where(AuditEvent.resource_type == "analytics_probe"))
        await db.commit()

    def audit(action: str, outcome: str, when: datetime) -> AuditEvent:
        return AuditEvent(
            id=uuid.uuid4(),
            actor_user_id=account.id,
            action=action,
            resource_type="analytics_probe",
            resource_id=None,
            outcome=outcome,
            request_id=f"analytics-probe-{token}",
            ip="127.0.0.1",
            created_at=when,
        )

    before = await _summary(db_engine, now=now, **period)
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        db.add_all(
            [
                audit("ai.ask", "success", start + timedelta(hours=1)),
                audit("ai.ask", "error", start + timedelta(hours=2)),
                audit(f"mcp.{probe_tool}", "success", start + timedelta(hours=3)),
                audit(f"mcp.{probe_tool}", "error", start + timedelta(hours=4)),
                audit("auth.login.success", "success", start + timedelta(hours=5)),
                audit("auth.login.failure", "denied", start + timedelta(hours=6)),
                audit("authz.denied", "denied", start + timedelta(hours=7)),
                audit("patient.read", "success", start + timedelta(hours=8)),
                audit("labs.read", "success", start + timedelta(hours=9)),
                audit("prescriptions.read", "success", start + timedelta(hours=10)),
            ]
        )
        await db.commit()

    body = await _summary(db_engine, now=now, **period)
    assert body["ai_usage"]["total_requests"] - before["ai_usage"]["total_requests"] == 2
    assert body["ai_usage"]["successful"] - before["ai_usage"]["successful"] == 1
    assert body["ai_usage"]["failed"] - before["ai_usage"]["failed"] == 1
    assert body["ai_usage"]["denied"] - before["ai_usage"]["denied"] == 0
    if body["ai_usage"]["successful"] + body["ai_usage"]["failed"] == 0:
        assert body["ai_usage"]["success_rate"] is None
    else:
        assert float(body["ai_usage"]["success_rate"]) == pytest.approx(
            100
            * body["ai_usage"]["successful"]
            / (body["ai_usage"]["successful"] + body["ai_usage"]["failed"]),
            abs=0.05,
        )
    probe_after = next(
        entry for entry in body["ai_usage"]["top_tools"] if entry["tool"] == probe_tool
    )
    probe_before = next(
        (entry for entry in before["ai_usage"]["top_tools"] if entry["tool"] == probe_tool),
        {"calls": 0, "failures": 0},
    )
    assert probe_after["calls"] - probe_before["calls"] == 2
    assert probe_after["failures"] - probe_before["failures"] == 1

    for key, delta in (
        ("successful_signins", 1),
        ("failed_signins", 1),
        ("permission_denials", 1),
        ("patient_reads", 1),
        ("lab_reads", 1),
        ("prescription_reads", 1),
        ("ai_requests", 2),
    ):
        assert body["security"]["totals"][key] - before["security"]["totals"][key] == delta, key
    day = body["security"]["trend"][0]
    assert day["successful_signins"] >= 1
    assert day["failed_signins"] >= 1
    assert day["permission_denials"] >= 1
    assert day["patient_reads"] >= 1
    assert day["lab_reads"] >= 1
    assert day["prescription_reads"] >= 1
    assert day["ai_requests"] >= 2

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        await db.execute(delete(AuditEvent).where(AuditEvent.resource_type == "analytics_probe"))
        await db.commit()


@pytest.mark.postgres
async def test_abnormal_rule_matches_the_mcp_lookup_tool(
    db_engine, clinical_data, make_user, monkeypatch
) -> None:
    """The abnormal definition is THE MCP rule (docs/API.md "MCP tool contract").

    Crafted fixture (unique code per test): abnormal high (document status
    `preliminary` — status is NOT an abnormality flag), abnormal low, normal,
    a text-only result, and a numeric result without reference bounds. The
    analytics buckets must classify exactly like the real
    `find_patients_by_lab(abnormal_only=True)` MCP tool.
    """
    from app.models import LabResult

    token = clinical_data.token
    code = f"AT{token}"
    name = f"AbnormalTest {token}"
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    period = {"start": date(2026, 10, 1), "end": date(2026, 10, 5)}
    ref_low, ref_high = Decimal("3.500"), Decimal("5.100")

    def lab(patient_id, *, value=None, text=None, status, collected, with_refs=True):
        return LabResult(
            id=uuid.uuid4(),
            patient_id=patient_id,
            test_code=code,
            test_name=name,
            value_num=Decimal(value) if value is not None else None,
            value_text=text,
            unit="mmol/L",
            ref_low=ref_low if (with_refs and text is None) else None,
            ref_high=ref_high if (with_refs and text is None) else None,
            status=status,
            collected_at=datetime.fromisoformat(collected).replace(tzinfo=timezone.utc),
        )

    before = await _summary(db_engine, now=now, **period)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                lab(
                    clinical_data.patient_id,
                    value="4.500",
                    status="final",
                    collected="2026-10-01T08:00:00",
                ),
                lab(
                    clinical_data.patient_id,
                    value="3.000",
                    status="final",
                    collected="2026-10-03T08:00:00",
                ),
                lab(
                    clinical_data.patient_id,
                    value="5.800",
                    status="preliminary",
                    collected="2026-10-05T08:00:00",
                ),
                lab(
                    clinical_data.empty_patient_id,
                    text="within range",
                    status="final",
                    collected="2026-10-01T08:00:00",
                ),
                lab(
                    clinical_data.empty_patient_id,
                    value="7.700",
                    status="pending",
                    collected="2026-10-02T08:00:00",
                    with_refs=False,
                ),
            ]
        )
        await session.commit()
    after = await _summary(db_engine, now=now, **period)

    assert (
        after["labs"]["abnormal_rate"]["abnormal"]
        - before["labs"]["abnormal_rate"]["abnormal"]
        == 2
    )
    assert after["labs"]["abnormal_rate"]["total"] - before["labs"]["abnormal_rate"]["total"] == 3
    assert after["labs"]["total"] - before["labs"]["total"] == 5
    for status, delta in (("final", 3), ("preliminary", 1), ("pending", 1)):
        assert (
            after["labs"]["status_mix"][status] - before["labs"]["status_mix"][status]
            == delta
        ), status
    for key, delta in (
        ("normal", 1),
        ("high", 1),
        ("low", 1),
        ("unclassified", 2),
        ("missing_reference_ranges", 2),
    ):
        assert (
            after["labs"]["classification"][key]
            - before["labs"]["classification"][key]
            == delta
        ), key

    monkeypatch.setattr("app.services.analytics.TOP_ABNORMAL_LIMIT", 500)
    widened = await _summary(db_engine, now=now, **period)
    entry = next(
        item for item in widened["labs"]["top_abnormal_tests"] if item["test_code"] == code
    )
    assert entry == {"test_code": code, "test_name": name, "abnormal_count": 2}

    # --- cross-check against the real MCP lookup tool -----------------------
    from app.authorization.policy import Principal, permissions_for_roles
    from app.mcp.context import ToolContext
    from app.mcp.server import mcp_server

    account = await make_user(roles=("doctor",))
    principal = Principal(
        user_id=str(account.id),
        roles=account.roles,
        is_active=True,
        email=account.email,
        permissions=permissions_for_roles(account.roles),
    )
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        ctx = ToolContext(
            principal=principal, db=session, request_id="test-analytics-mcp", ip="127.0.0.1"
        )
        abnormal = await mcp_server.call_tool(
            "find_patients_by_lab",
            {"test_code": code, "abnormal_only": True, "limit": 50},
            ctx,
        )
        assert abnormal.ok is True
        rows = abnormal.model_payload["data"]["patients"]
        listed = {row["patient_id"]: row["matched"] for row in rows}
        assert str(clinical_data.patient_id) in listed
        assert "high" in listed[str(clinical_data.patient_id)]
        assert str(clinical_data.empty_patient_id) not in listed

        all_rows = await mcp_server.call_tool(
            "find_patients_by_lab",
            {"test_code": code, "abnormal_only": False, "limit": 50},
            ctx,
        )
        everything = {
            row["patient_id"]: row["matched"]
            for row in all_rows.model_payload["data"]["patients"]
        }
        assert str(clinical_data.empty_patient_id) in everything
        assert "high" not in everything[str(clinical_data.empty_patient_id)]
        assert "low" not in everything[str(clinical_data.empty_patient_id)]


@pytest.mark.postgres
async def test_analytics_read_is_audited(auth_client, make_user) -> None:
    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    response = await client.get(f"{SUMMARY_URL}?start=2026-10-01&end=2026-10-07")
    assert response.status_code == 200

    rows = await audit_rows(action="analytics.read", actor_user_id=account.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "success"
    assert row.resource_type == "analytics"
    assert row.resource_id is None
    assert row.request_id == response.headers["X-Request-Id"]
    # Metadata only: the audit row never carries payloads or secrets.
    blob = " ".join(
        str(value)
        for value in (row.action, row.resource_type, row.resource_id, row.request_id)
        if value is not None
    )
    assert "Metformin" not in blob and "DEMO-" not in blob and "@" not in blob


@pytest.mark.postgres
async def test_summary_is_zero_filled_and_bounded(auth_client, make_user) -> None:
    client = await auth_client(await make_user(roles=("clinical_admin",)))
    body = (await client.get(f"{SUMMARY_URL}?start=2026-10-01&end=2026-10-14")).json()

    assert body["range"]["days"] == 14
    days = [entry["day"] for entry in body["security"]["trend"]]
    assert days == [
        (date(2026, 10, 1) + timedelta(days=index)).isoformat() for index in range(14)
    ]
    for block in ("patients", "labs", "prescriptions"):
        assert [entry["day"] for entry in body[block]["trend"]] == days

    appointment_days = [entry["day"] for entry in body["appointments"]["trend"]]
    assert 14 <= len(appointment_days) <= 15
    assert appointment_days == sorted(appointment_days)
    assert sum(entry["total"] for entry in body["appointments"]["trend"]) == body["appointments"]["total"]

    assert set(body["labs"]["status_mix"]) == {"final", "preliminary", "pending"}
    assert set(body["prescriptions"]["by_status"]) == {
        "active",
        "completed",
        "stopped",
        "cancelled",
    }
    assert [entry["role"] for entry in body["staff"]["by_role"]] == [
        "doctor",
        "nurse",
        "clinical_admin",
    ]
    assert [entry["weekday"] for entry in body["appointments"]["by_weekday"]] == list(range(7))
    assert [entry["hour"] for entry in body["appointments"]["by_hour"]] == list(range(24))


@pytest.mark.postgres
async def test_summary_uses_bounded_queries_and_completes_on_the_seeded_dataset(
    db_engine, auth_client, make_user
) -> None:
    """Performance sanity: one bounded pass of grouped aggregates (no N+1).

    The statement count is asserted directly (a bounded number of aggregate
    SELECTs); the endpoint must answer one request against the seeded demo
    dataset (45 patients, hundreds of labs/notes) well within an interactive
    budget.
    """
    from app.services.analytics import build_summary

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        event.listen(db_engine.sync_engine, "before_cursor_execute", _record)
        try:
            started = time.perf_counter()
            payload = await build_summary(db, start=date(2026, 10, 1), end=date(2026, 10, 14))
            elapsed = time.perf_counter() - started
        finally:
            event.remove(db_engine.sync_engine, "before_cursor_execute", _record)

    assert len(statements) <= 30, (
        f"analytics must not fan out per row: {len(statements)} statements"
    )
    assert elapsed < 5.0, f"build_summary took {elapsed:.2f}s"
    assert payload["counts"]["patients"] >= 1

    account = await make_user(roles=("clinical_admin",))
    client = await auth_client(account)
    started = time.perf_counter()
    response = await client.get(f"{SUMMARY_URL}?start=2026-10-01&end=2026-10-14")
    request_elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert request_elapsed < 5.0, f"GET {SUMMARY_URL} took {request_elapsed:.2f}s"
