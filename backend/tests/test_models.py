"""Model/schema tests: constraints, numeric safety, soft delete, FK behavior.

Metadata tests run without a database. Behavior tests need PostgreSQL and are
marked `postgres`; they skip with a clear reason when it is unreachable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.mixins import SoftDeleteMixin, not_deleted_clause
from app.models import Encounter, LabResult, Medication, Patient, Prescription, StaffUser
from app.models.admin import AuditEvent, Role, UserRole


# --- metadata-level (no database required) ---------------------------------


def test_unique_constraints_present() -> None:
    assert StaffUser.__table__.c.email.unique is True
    assert Patient.__table__.c.mrn.unique is True
    assert Medication.__table__.c.code.unique is True
    assert Role.__table__.c.key.unique is True


def test_email_lowercase_check_constraint() -> None:
    names = {constraint.name for constraint in StaffUser.__table__.constraints}
    assert "ck_staff_user_email_lower" in names


def test_patient_synthetic_check_constraints() -> None:
    names = {constraint.name for constraint in Patient.__table__.constraints}
    assert "ck_patient_mrn_synthetic" in names
    assert "ck_patient_provenance_synthetic" in names


def test_numeric_columns_are_never_float() -> None:
    numeric_columns = [
        LabResult.__table__.c.value_num,
        LabResult.__table__.c.ref_low,
        LabResult.__table__.c.ref_high,
        Prescription.__table__.c.dose_amount,
    ]
    for column in numeric_columns:
        assert isinstance(column.type, sa.Numeric), column
        assert not isinstance(column.type, sa.Float), column


def test_soft_delete_mixin_applied_to_clinical_models() -> None:
    for model in (StaffUser, Patient, Encounter, LabResult, Prescription):
        assert "deleted_at" in model.__table__.c, model
        assert issubclass(model, SoftDeleteMixin), model


def test_soft_delete_filter_clause() -> None:
    clause = Patient.not_deleted()
    assert str(clause) == "patients.patient.deleted_at IS NULL"
    assert str(not_deleted_clause(Encounter)) == "patients.encounter.deleted_at IS NULL"


def test_audit_event_columns_and_indexes() -> None:
    table = AuditEvent.__table__
    assert {"actor_user_id", "action", "resource_type", "resource_id", "outcome", "request_id", "ip"} <= set(table.c.keys())
    index_names = {index.name for index in table.indexes}
    assert {"ix_audit_event_created_at", "ix_audit_event_actor_user_id", "ix_audit_event_action"} <= index_names
    # No clinical payload column exists in the audit table.
    assert "payload" not in table.c and "details" not in table.c


def test_composite_primary_key_user_role() -> None:
    pk_columns = {column.name for column in UserRole.__table__.primary_key.columns}
    assert pk_columns == {"user_id", "role_id"}


def test_clinical_foreign_keys_are_restrict() -> None:
    for table in (
        Encounter.__table__,
        LabResult.__table__,
        Prescription.__table__,
        UserRole.__table__,
        AuditEvent.__table__,
    ):
        for foreign_key in table.foreign_keys:
            assert foreign_key.ondelete == "RESTRICT", f"{table.name}.{foreign_key.parent.name}"


def test_timestamps_are_timezone_aware() -> None:
    for column in (StaffUser.__table__.c.created_at, Patient.__table__.c.updated_at):
        assert isinstance(column.type, sa.DateTime)
        assert column.type.timezone is True
        assert column.server_default is not None


# --- database behavior (requires PostgreSQL) -------------------------------


def _new_staff(email: str) -> StaffUser:
    return StaffUser(id=uuid.uuid4(), email=email, display_name="Constraint Test")


def _new_patient(mrn: str) -> Patient:
    return Patient(
        id=uuid.uuid4(),
        mrn=mrn,
        display_name="Constraint Test (Demo)",
        given_name="Constraint",
        family_name="Test",
        sex="unknown",
        provenance="synthetic",
    )


@pytest.mark.postgres
async def test_duplicate_email_rejected(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(_new_staff("dup-email@example.com"))
        await session.commit()
        session.add(_new_staff("dup-email@example.com"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.postgres
async def test_email_normalized_to_lowercase(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(_new_staff("Mixed-Case@Example.com"))
        await session.commit()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        stored = await session.scalar(sa.select(StaffUser).where(StaffUser.email == "mixed-case@example.com"))
        assert stored is not None
        await session.execute(sa.delete(StaffUser).where(StaffUser.id == stored.id))
        await session.commit()


@pytest.mark.postgres
async def test_duplicate_mrn_rejected(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(_new_patient("DEMO-DUP-0001"))
        await session.commit()
        session.add(_new_patient("DEMO-DUP-0001"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.execute(sa.delete(Patient).where(Patient.mrn == "DEMO-DUP-0001"))
        await session.commit()


@pytest.mark.postgres
async def test_non_synthetic_mrn_rejected(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(_new_patient("REAL-0001"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.postgres
async def test_soft_delete_filter_excludes_deleted_rows(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(_new_patient("DEMO-SOFT-0001"))
        session.add(_new_patient("DEMO-SOFT-0002"))
        await session.commit()

        deleted = await session.scalar(sa.select(Patient).where(Patient.mrn == "DEMO-SOFT-0001"))
        deleted.deleted_at = datetime.now(timezone.utc)
        await session.commit()

        visible = (await session.scalars(sa.select(Patient).where(Patient.not_deleted()))).all()
        visible_mrns = {patient.mrn for patient in visible}
        assert "DEMO-SOFT-0001" not in visible_mrns
        assert "DEMO-SOFT-0002" in visible_mrns

        await session.execute(sa.delete(Patient).where(Patient.mrn.in_(["DEMO-SOFT-0001", "DEMO-SOFT-0002"])))
        await session.commit()


@pytest.mark.postgres
async def test_patient_delete_blocked_when_clinical_rows_exist(db_engine) -> None:
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("DEMO-FK-0001")
        session.add(patient)
        await session.flush()
        patient_id = patient.id
        session.add(
            LabResult(
                id=uuid.uuid4(),
                patient_id=patient_id,
                test_code="GLU",
                test_name="Glucose, fasting",
                value_num=91,
                unit="mg/dL",
                status="final",
                collected_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

        with pytest.raises(IntegrityError):
            await session.execute(sa.delete(Patient).where(Patient.id == patient_id))
            await session.commit()
        await session.rollback()

        await session.execute(sa.delete(LabResult).where(LabResult.patient_id == patient_id))
        await session.execute(sa.delete(Patient).where(Patient.id == patient_id))
        await session.commit()
