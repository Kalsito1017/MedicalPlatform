"""Seed script tests: idempotency and synthetic-data guarantees.

Marked `postgres`; skips with a clear reason when PostgreSQL is unreachable.
Counts are derived from the seed script's own spec constants (imported via
importlib) so the assertions stay true as the demo cohort grows.
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditEvent,
    Encounter,
    LabResult,
    Medication,
    Patient,
    Prescription,
    Role,
    StaffUser,
    UserRole,
)
from conftest import REPO_ROOT, run_seed


def _load_seed_spec():
    """Import scripts/seed_demo_data.py (module registered before exec)."""
    path = REPO_ROOT / "scripts" / "seed_demo_data.py"
    spec = importlib.util.spec_from_file_location("seed_demo_data_spec", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SEED_SPEC = _load_seed_spec()
PATIENT_COUNT = len(SEED_SPEC.PATIENT_SPECS)
MEDICATION_COUNT = len(SEED_SPEC.MEDICATION_CATALOG)

DEMO_PASSWORDS = {
    "DEMO_ADMIN_PASSWORD": "test-only-admin-pw",
    "DEMO_DOCTOR_PASSWORD": "test-only-doctor-pw",
    "DEMO_NURSE_PASSWORD": "test-only-nurse-pw",
    "DEMO_TECH_ADMIN_PASSWORD": "test-only-techadmin-pw",
}

ENTITIES = {
    "roles": Role,
    "staff_users": StaffUser,
    "role_grants": UserRole,
    "medications": Medication,
    "patients": Patient,
    "encounters": Encounter,
    "lab_results": LabResult,
    "prescriptions": Prescription,
}


async def _counts(session: AsyncSession) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, model in ENTITIES.items():
        counts[name] = (await session.execute(sa.select(sa.func.count()).select_from(model))).scalar_one()
    return counts


async def _clear_all(db_engine) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        for model in (AuditEvent, UserRole, Prescription, LabResult, Encounter, Patient, Medication, StaffUser, Role):
            await session.execute(sa.delete(model))
        await session.commit()


def _ensure_seeded() -> None:
    result = run_seed(extra_env=DEMO_PASSWORDS)
    assert result.returncode == 0, f"seed failed:\n{result.stdout}\n{result.stderr}"


async def _count_after_runs(db_engine, runs: int) -> tuple[dict[str, int], str]:
    last_stdout = ""
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        counts = await _counts(session)
    for _ in range(runs):
        result = run_seed(extra_env=DEMO_PASSWORDS)
        assert result.returncode == 0, f"seed failed:\n{result.stdout}\n{result.stderr}"
        last_stdout = result.stdout
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            counts = await _counts(session)
    return counts, last_stdout


@pytest.mark.postgres
async def test_seed_is_idempotent(db_engine) -> None:
    await _clear_all(db_engine)

    first, _ = await _count_after_runs(db_engine, runs=1)
    second, stdout = await _count_after_runs(db_engine, runs=1)

    assert first == second, "second seed run must create nothing"
    assert second["roles"] == 4
    assert second["staff_users"] == 4
    assert second["role_grants"] == 4
    assert second["patients"] == PATIENT_COUNT
    assert second["medications"] == MEDICATION_COUNT
    # Every patient has 2-6 encounters, 6-12 lab results, and 0-5 prescriptions.
    assert 2 * PATIENT_COUNT <= second["encounters"] <= 6 * PATIENT_COUNT
    assert 6 * PATIENT_COUNT <= second["lab_results"] <= 12 * PATIENT_COUNT
    assert 0 < second["prescriptions"] <= 5 * PATIENT_COUNT
    assert "0 created" in stdout


@pytest.mark.postgres
async def test_seed_data_is_synthetic_and_unique(db_engine) -> None:
    _ensure_seeded()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        total = (await session.execute(sa.select(sa.func.count()).select_from(Patient))).scalar_one()
        distinct_mrns = (await session.execute(sa.select(sa.func.count(Patient.mrn.distinct())))).scalar_one()
        assert total == distinct_mrns

        non_demo = (
            await session.execute(sa.select(sa.func.count()).select_from(Patient).where(~Patient.mrn.like("DEMO-%")))
        ).scalar_one()
        assert non_demo == 0

        non_synthetic = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Patient).where(Patient.provenance != "synthetic")
            )
        ).scalar_one()
        assert non_synthetic == 0

        emails = (await session.execute(sa.select(StaffUser.email))).scalars().all()
        assert all(email == email.lower() for email in emails)

        hashes = (await session.execute(sa.select(StaffUser.password_hash))).scalars().all()
        assert hashes and all(value and value.startswith("$argon2") for value in hashes)


@pytest.mark.postgres
async def test_seed_deliberate_abnormal_patterns(db_engine) -> None:
    """The demo cohort carries coherent abnormal patterns for AI lookups."""
    await _clear_all(db_engine)
    _ensure_seeded()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:

        async def distinct_patients(code: str, condition) -> int:
            stmt = sa.select(sa.func.count(sa.func.distinct(LabResult.patient_id))).where(
                LabResult.test_code == code, condition
            )
            return (await session.execute(stmt)).scalar_one()

        # High potassium, anemia, diabetes, high LDL, elevated liver enzymes.
        assert await distinct_patients("K", LabResult.value_num > Decimal("5.2")) >= 5
        assert await distinct_patients("HGB", LabResult.value_num < Decimal("12.0")) >= 3
        assert await distinct_patients("HBA1C", LabResult.value_num > Decimal("6.5")) >= 5
        assert await distinct_patients("GLU", LabResult.value_num > Decimal("126.0")) >= 5
        assert await distinct_patients("LDL", LabResult.value_num > Decimal("150.0")) >= 5
        assert await distinct_patients("ALT", LabResult.value_num > Decimal("60.0")) >= 3

        metformin_patients = (
            await session.execute(
                sa.select(sa.func.count(sa.func.distinct(Prescription.patient_id))).where(
                    Prescription.medication_name == "Metformin",
                    Prescription.status == "active",
                )
            )
        ).scalar_one()
        assert metformin_patients >= 5

        # Status mix: mostly final with a few non-final results.
        status_rows = (
            await session.execute(sa.select(LabResult.status, sa.func.count()).group_by(LabResult.status))
        ).all()
        by_status = dict(status_rows)
        assert by_status.get("final", 0) > 0
        assert sum(count for name, count in by_status.items() if name != "final") > 0


@pytest.mark.postgres
async def test_seed_utf8_names_roundtrip(db_engine) -> None:
    _ensure_seeded()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        oconnor = await session.scalar(sa.select(Patient).where(Patient.mrn == "DEMO-0007"))
        assert oconnor is not None and oconnor.family_name == "O'Connor"

        jose = await session.scalar(sa.select(Patient).where(Patient.mrn == "DEMO-0008"))
        assert jose is not None and jose.given_name == "José"

        rtl = await session.scalar(sa.select(Patient).where(Patient.mrn == "DEMO-0009"))
        assert rtl is not None and rtl.display_name == "ليلى منصور (Demo)"

        emoji = await session.scalar(sa.select(Patient).where(Patient.mrn == "DEMO-0010"))
        assert emoji is not None and "⭐" in emoji.family_name

        hyphen = await session.scalar(sa.select(Patient).where(Patient.mrn == "DEMO-0011"))
        assert hyphen is not None and hyphen.family_name == "O'Neill-Rossi"

        long_name = await session.scalar(sa.select(Patient).where(Patient.mrn == "DEMO-0013"))
        assert long_name is not None
        assert long_name.display_name == "Maximiliano Fernando Alejandro de la Cruz y Santibáñez (Demo)"


@pytest.mark.postgres
async def test_seed_generates_passwords_once(db_engine) -> None:
    await _clear_all(db_engine)

    result = run_seed(extra_env={key: None for key in DEMO_PASSWORDS})
    assert result.returncode == 0, f"seed failed:\n{result.stdout}\n{result.stderr}"
    assert "Set these in your .env" in result.stdout
    for name in DEMO_PASSWORDS:
        assert f"{name}=" in result.stdout
        line = next(line for line in result.stdout.splitlines() if line.strip().startswith(f"{name}="))
        generated = line.strip().split("=", 1)[1]
        assert generated and result.stdout.count(generated) == 1
