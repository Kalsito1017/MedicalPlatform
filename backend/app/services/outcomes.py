"""Observed treatment-response read model (synthetic demo only).

This module does NOT store a second clinical outcome. It compares values that
already exist in the record:

* ``laboratory.lab_result`` for lab markers
* ``patients.encounter`` for blood pressure

For each prescription it finds the latest baseline before the start date and
values after treatment began. The result is labelled as an *observed change*,
never as proof that a medication caused the change.

The intended reading is synthetic demonstration data. No causal or clinical
efficacy claim is made.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab import LabResult
from app.models.medication import Medication, Prescription
from app.models.patient import Encounter

SUMMARY_RESPONSE_LIMIT = 6
LOOKBACK_DAYS = 180
FOLLOW_UP_DAYS = 30
MAX_POINTS = 8

Trend = Literal["improved", "worsened", "stable", "monitor", "insufficient_data"]
Direction = Literal["lower", "higher", "monitor"]


@dataclass(frozen=True)
class MetricSpec:
    code: str
    name: str
    unit: str | None
    direction: Direction
    kind: Literal["lab", "vitals"] = "lab"


# Medication code -> the markers that make a useful observed-response view.
# A single prescription may expose up to two markers; the API remains bounded.
METRICS_BY_MEDICATION: dict[str, tuple[MetricSpec, ...]] = {
    "MED-001": (  # Metformin
        MetricSpec("HBA1C", "HbA1c", "%", "lower"),
        MetricSpec("GLU", "Fasting glucose", "mg/dL", "lower"),
    ),
    "MED-009": (  # Insulin glargine
        MetricSpec("HBA1C", "HbA1c", "%", "lower"),
        MetricSpec("GLU", "Fasting glucose", "mg/dL", "lower"),
    ),
    "MED-003": (  # Atorvastatin
        MetricSpec("LDL", "LDL cholesterol", "mg/dL", "lower"),
        MetricSpec("CHOL", "Total cholesterol", "mg/dL", "lower"),
    ),
    "MED-014": (  # Iron sulfate
        MetricSpec("HGB", "Hemoglobin", "g/dL", "higher"),
    ),
    "MED-006": (  # Levothyroxine
        MetricSpec("TSH", "TSH", "mIU/L", "lower"),
    ),
    "MED-015": (  # Vitamin D3
        MetricSpec("VITD", "Vitamin D (25-OH)", "ng/mL", "higher"),
    ),
    "MED-002": (  # Lisinopril
        MetricSpec("SBP", "Systolic blood pressure", "mmHg", "lower", "vitals"),
        MetricSpec("DBP", "Diastolic blood pressure", "mmHg", "lower", "vitals"),
        MetricSpec("K", "Potassium", "mmol/L", "monitor"),
    ),
    "MED-008": (  # Amlodipine
        MetricSpec("SBP", "Systolic blood pressure", "mmHg", "lower", "vitals"),
        MetricSpec("DBP", "Diastolic blood pressure", "mmHg", "lower", "vitals"),
    ),
}


def _utc_start(value) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _metric_points(
    *,
    values: list[tuple[datetime, Decimal]],
    start: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Return baseline, follow-up, current and bounded series for one metric."""
    baseline_candidates = [(measured, value) for measured, value in values if measured < start]
    baseline = baseline_candidates[-1] if baseline_candidates else None
    after = [(measured, value) for measured, value in values if measured >= start]
    follow_up = next(
        (item for item in after if item[0] >= start + timedelta(days=FOLLOW_UP_DAYS)),
        None,
    )
    current = after[-1] if after else None
    selected = ([baseline] if baseline is not None else []) + after[: MAX_POINTS - 1]
    points = [
        {"measured_at": measured, "value": value}
        for measured, value in selected[-MAX_POINTS:]
    ]
    return (
        {"measured_at": baseline[0], "value": baseline[1]} if baseline else None,
        {"measured_at": follow_up[0], "value": follow_up[1]} if follow_up else None,
        {"measured_at": current[0], "value": current[1]} if current else None,
        points,
    )


def _trend(
    direction: Direction,
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> tuple[Trend, Decimal | None]:
    if baseline is None or current is None:
        return "insufficient_data", None
    delta = current["value"] - baseline["value"]
    if direction == "monitor":
        return "monitor", delta
    threshold = max(Decimal("0.1"), abs(baseline["value"]) * Decimal("0.02"))
    if abs(delta) <= threshold:
        return "stable", delta
    improved = delta < 0 if direction == "lower" else delta > 0
    return ("improved" if improved else "worsened"), delta


def _lab_values_by_code(labs: list[LabResult]) -> dict[str, list[tuple[datetime, Decimal]]]:
    grouped: dict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
    for row in labs:
        if row.value_num is not None:
            grouped[row.test_code].append((row.collected_at, row.value_num))
    for rows in grouped.values():
        rows.sort(key=lambda item: item[0])
    return grouped


def _vitals_values(encounters: list[Encounter]) -> dict[str, list[tuple[datetime, Decimal]]]:
    grouped: dict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
    for row in encounters:
        measured = row.occurred_at
        if row.systolic_bp is not None:
            grouped["SBP"].append((measured, Decimal(row.systolic_bp)))
        if row.diastolic_bp is not None:
            grouped["DBP"].append((measured, Decimal(row.diastolic_bp)))
    for rows in grouped.values():
        rows.sort(key=lambda item: item[0])
    return grouped


async def load_treatment_responses(
    db: AsyncSession,
    patient_id: uuid.UUID,
    *,
    limit: int = SUMMARY_RESPONSE_LIMIT,
) -> list[dict[str, Any]]:
    """Build bounded observed-response rows for one patient.

    The query is deliberately small and bounded: at most ``limit`` completed
    response rows are returned, and each point series is capped. No clinical
    payload is written to logs or audit.
    """
    prescriptions = list(
        (
            await db.scalars(
                select(Prescription)
                .where(Prescription.patient_id == patient_id, Prescription.not_deleted())
                .order_by(
                    case((Prescription.status == "active", 0), else_=1),
                    Prescription.start_date.desc(),
                    Prescription.id,
                )
                .limit(40)
            )
        ).all()
    )
    if not prescriptions:
        return []

    medication_rows = await db.execute(select(Medication.id, Medication.code))
    code_by_id = {row.id: row.code for row in medication_rows}

    labs = list(
        (
            await db.scalars(
                select(LabResult)
                .where(
                    LabResult.patient_id == patient_id,
                    LabResult.not_deleted(),
                    LabResult.value_num.is_not(None),
                )
                .order_by(LabResult.collected_at.asc(), LabResult.id)
            )
        ).all()
    )
    encounters = list(
        (
            await db.scalars(
                select(Encounter)
                .where(Encounter.patient_id == patient_id, Encounter.not_deleted())
                .order_by(Encounter.occurred_at.asc(), Encounter.id)
            )
        ).all()
    )
    by_lab = _lab_values_by_code(labs)
    by_vitals = _vitals_values(encounters)
    results: list[dict[str, Any]] = []

    for prescription in prescriptions:
        code = code_by_id.get(prescription.medication_id)
        metrics = METRICS_BY_MEDICATION.get(code or "", ())
        if not metrics:
            continue
        start = _utc_start(prescription.start_date)
        for metric in metrics[:2]:
            values = by_vitals.get(metric.code, []) if metric.kind == "vitals" else by_lab.get(metric.code, [])
            # A response needs at least a baseline and a value after treatment
            # began; otherwise expose the course as awaiting follow-up.
            baseline, follow_up, current, points = _metric_points(values=values, start=start)
            trend, delta = _trend(metric.direction, baseline, current)
            results.append(
                {
                    "prescription_id": prescription.id,
                    "medication": prescription.medication_name,
                    "status": prescription.status,
                    "started_on": prescription.start_date,
                    "metric_code": metric.code,
                    "metric_name": metric.name,
                    "unit": metric.unit,
                    "baseline": baseline,
                    "follow_up": follow_up,
                    "current": current,
                    "delta": delta,
                    "trend": trend,
                    "points": points,
                }
            )
            if len(results) >= limit:
                return results
    return results
