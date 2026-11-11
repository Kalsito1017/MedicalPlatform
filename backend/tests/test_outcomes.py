"""Observed treatment-response read model tests."""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LabResult, Medication, Prescription
from app.services.outcomes import load_treatment_responses

pytestmark = pytest.mark.postgres


@pytest.mark.postgres
async def test_treatment_response_uses_baseline_and_follow_up(
    db_engine, clinical_data
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        medication = await db.scalar(select(Medication).where(Medication.code == "MED-001"))
        if medication is None:
            medication = Medication(
                id=uuid.uuid4(),
                code="MED-001",
                display_name="Metformin",
                form="tablet",
            )
            db.add(medication)
            await db.flush()

        start = datetime.now(timezone.utc).date() - timedelta(days=180)
        prescription = Prescription(
            id=uuid.uuid4(),
            patient_id=clinical_data.patient_id,
            medication_id=medication.id,
            medication_name="Metformin",
            dose_amount=Decimal("500"),
            dose_unit="mg",
            route="oral",
            frequency="twice daily",
            status="active",
            start_date=start,
        )
        db.add(prescription)

        baseline_at = datetime.combine(start - timedelta(days=30), time(8, tzinfo=timezone.utc))
        follow_at = datetime.combine(start + timedelta(days=90), time(8, tzinfo=timezone.utc))
        current_at = datetime.combine(start + timedelta(days=150), time(8, tzinfo=timezone.utc))
        for lab_id, measured_at, value in (
            (uuid.uuid4(), baseline_at, "9.4"),
            (uuid.uuid4(), follow_at, "7.2"),
            (uuid.uuid4(), current_at, "7.0"),
        ):
            db.add(
                LabResult(
                    id=lab_id,
                    patient_id=clinical_data.patient_id,
                    test_code="HBA1C",
                    test_name="HbA1c",
                    value_num=Decimal(value),
                    unit="%",
                    ref_low=Decimal("4.0"),
                    ref_high=Decimal("5.6"),
                    status="final",
                    collected_at=measured_at,
                )
            )
        await db.commit()

        responses = await load_treatment_responses(db, clinical_data.patient_id)
        response = next(
            item
            for item in responses
            if item["prescription_id"] == prescription.id and item["metric_code"] == "HBA1C"
        )
        assert response["baseline"]["value"] == Decimal("9.4")
        assert response["follow_up"]["value"] == Decimal("7.2")
        assert response["current"]["value"] == Decimal("7.0")
        assert response["trend"] == "improved"
        assert response["delta"] == Decimal("-2.4")
        assert len(response["points"]) >= 2
        assert all(point["measured_at"] <= datetime.now(timezone.utc) for point in response["points"])


async def test_treatment_response_requires_patient_data(db_engine, clinical_data) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        assert await load_treatment_responses(db, uuid.uuid4()) == []
