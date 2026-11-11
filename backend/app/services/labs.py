"""Lab-result data access (synthetic data only).

Parameterized, bounded, soft-delete aware. Numeric values stay `Decimal`
through serialization (JSON strings — never floats).
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ValidationFailed
from app.models.lab import LabResult, TestCatalog
from app.services import patients as patients_service

TREND_DAYS_DEFAULT = 365
TREND_DAYS_MIN = 1
TREND_DAYS_MAX = 365
TREND_TESTS_MAX = 5
TREND_POINTS_MAX = 50
TREND_TEST_CODE_MAX_LENGTH = 32
TREND_TESTS_QUERY_MAX_LENGTH = (
    TREND_TESTS_MAX * (TREND_TEST_CODE_MAX_LENGTH + 1) - 1
)

TrendFlag = Literal["high", "low", "normal", "unclassified"]


async def list_lab_results(
    db: AsyncSession, patient_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[LabResult], int]:
    """Paginated lab results for a patient, most recently collected first."""
    base = select(LabResult).where(
        LabResult.patient_id == patient_id, LabResult.not_deleted()
    )
    total = (await db.scalar(select(func.count()).select_from(base.subquery()))) or 0
    rows = list(
        (
            await db.scalars(
                base.order_by(LabResult.collected_at.desc(), LabResult.id).limit(limit).offset(offset)
            )
        ).all()
    )
    return rows, int(total)


def parse_trend_test_codes(raw: str | None) -> list[str] | None:
    """Parse the optional `tests=K,GLU` query value into bounded unique codes."""
    if raw is None:
        return None
    parts = [part.strip() for part in raw.split(",")]
    if not parts or any(not part for part in parts):
        raise _trend_validation_error()
    if any(len(part) > TREND_TEST_CODE_MAX_LENGTH for part in parts):
        raise _trend_validation_error()
    unique = list(dict.fromkeys(parts))
    if len(unique) > TREND_TESTS_MAX:
        raise _trend_validation_error()
    return unique


def _trend_validation_error() -> ValidationFailed:
    return ValidationFailed(
        "tests must contain between one and five known lab test codes.",
        details={
            "fields": [
                {
                    "loc": "query.tests",
                    "msg": "Unknown or malformed lab test code list.",
                    "type": "trend_test_codes",
                }
            ]
        },
    )


def _flag_for(value: Any, ref_low: Any, ref_high: Any) -> TrendFlag:
    """Classify one numeric result strictly from its own recorded bounds."""
    if value is None:
        return "unclassified"
    if ref_low is not None and value < ref_low:
        return "low"
    if ref_high is not None and value > ref_high:
        return "high"
    if ref_low is not None or ref_high is not None:
        return "normal"
    return "unclassified"


async def get_lab_trends(
    db: AsyncSession,
    patient_id: uuid.UUID,
    *,
    days: int,
    test_codes: list[str] | None,
    now=None,
) -> dict[str, Any]:
    """Bounded numeric lab trends for one patient.

    The result is intentionally chart-ready and minimal: one series per test,
    the most recent `TREND_POINTS_MAX` numeric points per series in ascending
    collection order, and no direct identifiers beyond the caller's authorized
    `patient_id`.
    """
    if not (TREND_DAYS_MIN <= days <= TREND_DAYS_MAX):
        raise ValidationFailed("days is outside the supported trend window.")
    window_start = (now or patients_service.utc_now()) - timedelta(days=days)

    base = (
        select(LabResult)
        .where(
            LabResult.patient_id == patient_id,
            LabResult.not_deleted(),
            LabResult.value_num.is_not(None),
            LabResult.collected_at >= window_start,
        )
        .subquery()
    )

    if test_codes is None:
        candidate_rows = (
            await db.execute(
                select(base.c.test_code, func.max(base.c.collected_at).label("latest"))
                .group_by(base.c.test_code)
                .order_by(func.max(base.c.collected_at).desc(), base.c.test_code.asc())
                .limit(TREND_TESTS_MAX + 1)
            )
        ).all()
        selected_codes = [str(row.test_code) for row in candidate_rows[:TREND_TESTS_MAX]]
        omitted_series = len(candidate_rows) > TREND_TESTS_MAX
    else:
        selected_codes = test_codes
        omitted_series = False
        known_codes = {
            str(code)
            for code in (
                await db.scalars(
                    select(base.c.test_code).where(base.c.test_code.in_(selected_codes))
                )
            ).all()
        }
        if set(selected_codes) - known_codes:
            raise _trend_validation_error()

    if not selected_codes:
        return {
            "patient_id": patient_id,
            "window_days": days,
            "series": [],
            "truncated": False,
        }

    counts = {
        str(code): int(total)
        for code, total in (
            await db.execute(
                select(base.c.test_code, func.count().label("total"))
                .where(base.c.test_code.in_(selected_codes))
                .group_by(base.c.test_code)
            )
        ).all()
    }

    ranked = select(
        base.c.test_code,
        base.c.test_name,
        base.c.unit,
        base.c.value_num,
        base.c.ref_low,
        base.c.ref_high,
        base.c.collected_at,
        func.row_number()
        .over(
            partition_by=base.c.test_code,
            order_by=(base.c.collected_at.desc(), base.c.id.desc()),
        )
        .label("point_rank"),
    ).subquery()
    rows = (
        await db.execute(
            select(ranked)
            .where(ranked.c.test_code.in_(selected_codes), ranked.c.point_rank <= TREND_POINTS_MAX)
            .order_by(ranked.c.test_code.asc(), ranked.c.collected_at.asc())
        )
    ).all()

    by_code: dict[str, list[Any]] = {code: [] for code in selected_codes}
    for row in rows:
        by_code[str(row.test_code)].append(row)

    catalog = {
        str(item.code): item
        for item in (
            await db.scalars(select(TestCatalog).where(TestCatalog.code.in_(selected_codes)))
        ).all()
    }

    series: list[dict[str, Any]] = []
    truncated = omitted_series
    for code in selected_codes:
        points = by_code.get(code, [])
        total = counts.get(code, 0)
        if total > len(points):
            truncated = True
        latest = points[-1] if points else None
        catalog_row = catalog.get(code)
        ref_low = None
        ref_high = None
        for point in reversed(points):
            if point.ref_low is not None or point.ref_high is not None:
                ref_low = point.ref_low
                ref_high = point.ref_high
                break
        if ref_low is None and catalog_row is not None:
            ref_low = catalog_row.ref_low
        if ref_high is None and catalog_row is not None:
            ref_high = catalog_row.ref_high

        flags = [
            _flag_for(point.value_num, point.ref_low, point.ref_high) for point in points
        ]
        series.append(
            {
                "test_code": code,
                "test_name": (
                    str(latest.test_name)
                    if latest is not None
                    else (str(catalog_row.name) if catalog_row is not None else code)
                ),
                "unit": (
                    str(latest.unit)
                    if latest is not None and latest.unit is not None
                    else (str(catalog_row.unit) if catalog_row is not None and catalog_row.unit is not None else None)
                ),
                "ref_low": ref_low,
                "ref_high": ref_high,
                "points": [
                    {
                        "collected_at": point.collected_at,
                        "value_num": point.value_num,
                        "flag": flag,
                    }
                    for point, flag in zip(points, flags, strict=True)
                ],
                "abnormal_count": sum(flag in {"high", "low"} for flag in flags),
            }
        )

    return {
        "patient_id": patient_id,
        "window_days": days,
        "series": series,
        "truncated": truncated,
    }
