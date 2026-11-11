"""Model/schema tests: constraints, numeric safety, soft delete, FK behavior.

Metadata tests run without a database. Behavior tests need PostgreSQL and are
marked `postgres`; they skip with a clear reason when it is unreachable.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.mixins import SoftDeleteMixin, not_deleted_clause
from app.models import (
    ClinicalNote,
    Diagnosis,
    Encounter,
    LabOrder,
    LabResult,
    Medication,
    Patient,
    PatientAssignment,
    Permission,
    Prescription,
    PrescriptionChange,
    PrescriptionHistory,
    PrescriptionItem,
    ReferenceRange,
    Specimen,
    StaffUser,
    TestCatalog as LabTestCatalog,
)
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
        Encounter.__table__.c.weight_kg,
        Encounter.__table__.c.height_cm,
        LabTestCatalog.__table__.c.ref_low,
        LabTestCatalog.__table__.c.ref_high,
        ReferenceRange.__table__.c.age_min_years,
        ReferenceRange.__table__.c.age_max_years,
        ReferenceRange.__table__.c.ref_low,
        ReferenceRange.__table__.c.ref_high,
        PrescriptionItem.__table__.c.dose_amount,
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
            if foreign_key.column.table.name == "staff_user":
                continue  # staff attribution FKs: see test_staff_attribution_foreign_keys_set_null
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
        session.add(_new_patient("MRN-DUP-0001"))
        await session.commit()
        session.add(_new_patient("MRN-DUP-0001"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.execute(sa.delete(Patient).where(Patient.mrn == "MRN-DUP-0001"))
        await session.commit()


@pytest.mark.postgres
async def test_invalid_mrn_prefix_rejected(db_engine) -> None:
    """Only the neutral MRN- scheme is accepted; other prefixes are rejected."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(_new_patient("REAL-0001"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(_new_patient("DEMO-0001"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.postgres
async def test_soft_delete_filter_excludes_deleted_rows(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(_new_patient("MRN-SOFT-0001"))
        session.add(_new_patient("MRN-SOFT-0002"))
        await session.commit()

        deleted = await session.scalar(sa.select(Patient).where(Patient.mrn == "MRN-SOFT-0001"))
        deleted.deleted_at = datetime.now(timezone.utc)
        await session.commit()

        visible = (await session.scalars(sa.select(Patient).where(Patient.not_deleted()))).all()
        visible_mrns = {patient.mrn for patient in visible}
        assert "MRN-SOFT-0001" not in visible_mrns
        assert "MRN-SOFT-0002" in visible_mrns

        await session.execute(sa.delete(Patient).where(Patient.mrn.in_(["MRN-SOFT-0001", "MRN-SOFT-0002"])))
        await session.commit()


@pytest.mark.postgres
async def test_patient_delete_blocked_when_clinical_rows_exist(db_engine) -> None:
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-FK-0001")
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


# --- clinical data layer: metadata (no database required) -------------------


NEW_TABLE_MODELS = (
    Diagnosis,
    ClinicalNote,
    PatientAssignment,
    LabTestCatalog,
    ReferenceRange,
    LabOrder,
    Specimen,
    PrescriptionItem,
    PrescriptionHistory,
    PrescriptionChange,
    Permission,
)


def test_blood_type_check_constraint_and_column() -> None:
    table = Patient.__table__
    assert "blood_type" in table.c
    assert table.c.blood_type.nullable is True
    names = {constraint.name for constraint in table.constraints}
    assert "ck_patient_blood_type" in names
    blood_check = next(constraint for constraint in table.constraints if constraint.name == "ck_patient_blood_type")
    sqltext = str(blood_check.sqltext)
    # Closed ABO/Rh vocabulary (+ explicit 'unknown'); NULL = not recorded.
    for value in ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown"):
        assert f"'{value}'" in sqltext
    assert "IS NULL" in sqltext


def test_encounter_vitals_and_practitioner_columns() -> None:
    table = Encounter.__table__
    for column_name in ("practitioner_id", "weight_kg", "height_cm"):
        assert column_name in table.c, column_name
        assert table.c[column_name].nullable is True, column_name
    assert isinstance(table.c.weight_kg.type, sa.Numeric)
    assert isinstance(table.c.height_cm.type, sa.Numeric)
    names = {constraint.name for constraint in table.constraints}
    assert {"ck_encounter_weight_range", "ck_encounter_height_range"} <= names


def test_new_tables_have_single_uuid_primary_keys() -> None:
    for model in NEW_TABLE_MODELS:
        pk_columns = list(model.__table__.primary_key.columns)
        assert len(pk_columns) == 1, model.__table__.name
        assert isinstance(pk_columns[0].type, sa.Uuid), model.__table__.name


def test_new_tables_have_utc_created_updated_timestamps() -> None:
    for model in NEW_TABLE_MODELS:
        table = model.__table__
        for column_name in ("created_at", "updated_at"):
            column = table.c[column_name]
            assert isinstance(column.type, sa.DateTime), f"{table.name}.{column_name}"
            assert column.type.timezone is True, f"{table.name}.{column_name}"
            assert column.server_default is not None, f"{table.name}.{column_name}"


def test_new_check_constraints_present() -> None:
    expected = {
        Diagnosis: {"ck_diagnosis_status"},
        ClinicalNote: {"ck_clinical_note_type"},
        PatientAssignment: {"ck_patient_assignment_care_role"},
        LabTestCatalog: {"ck_test_catalog_ref_range"},
        ReferenceRange: {"ck_reference_range_sex", "ck_reference_range_ages", "ck_reference_range_ref_order"},
        LabOrder: {"ck_lab_order_status"},
        Specimen: {"ck_specimen_type", "ck_specimen_status", "ck_specimen_times"},
        PrescriptionItem: {"ck_prescription_item_sequence_no"},
        PrescriptionHistory: {"ck_prescription_history_change_type"},
    }
    for model, expected_names in expected.items():
        present = {constraint.name for constraint in model.__table__.constraints}
        assert expected_names <= present, model.__table__.name


def test_new_unique_constraints() -> None:
    assert LabTestCatalog.__table__.c.code.unique is True
    assert Permission.__table__.c.key.unique is True
    assignment_names = {constraint.name for constraint in PatientAssignment.__table__.constraints}
    assert "uq_patient_assignment_patient_staff_role" in assignment_names
    item_names = {constraint.name for constraint in PrescriptionItem.__table__.constraints}
    assert "uq_prescription_item_prescription_sequence" in item_names


def test_new_clinical_foreign_keys_are_restrict() -> None:
    for model in (
        Diagnosis,
        ClinicalNote,
        LabOrder,
        Specimen,
        PrescriptionItem,
        PrescriptionHistory,
        PrescriptionChange,
        PatientAssignment,
        ReferenceRange,
    ):
        for foreign_key in model.__table__.foreign_keys:
            if foreign_key.column.table.name == "staff_user":
                continue  # attribution/membership FKs asserted below
            assert foreign_key.ondelete == "RESTRICT", f"{model.__table__.name}.{foreign_key.parent.name}"


def test_staff_attribution_foreign_keys_set_null() -> None:
    """Attribution columns keep the clinical record when a staff account is removed."""
    attribution = {
        (Encounter.__table__.name, "practitioner_id"),
        (ClinicalNote.__table__.name, "author_id"),
        (LabOrder.__table__.name, "ordering_practitioner_id"),
        (PrescriptionHistory.__table__.name, "changed_by"),
        (PrescriptionChange.__table__.name, "changed_by"),
    }
    found = set()
    for table in (
        Encounter.__table__,
        ClinicalNote.__table__,
        LabOrder.__table__,
        PrescriptionHistory.__table__,
        PrescriptionChange.__table__,
    ):
        for foreign_key in table.foreign_keys:
            key = (table.name, foreign_key.parent.name)
            if key in attribution:
                assert foreign_key.ondelete == "SET NULL", key
                found.add(key)
    assert found == attribution
    # Required care-team membership is RESTRICT (like admin.user_role).
    assignment_fks = {fk.parent.name: fk.ondelete for fk in PatientAssignment.__table__.foreign_keys}
    assert assignment_fks["staff_user_id"] == "RESTRICT"


def test_soft_delete_conventions_for_new_tables() -> None:
    """Clinical record rows are soft-deleted; link rows, event logs, catalogs are not."""
    for model in (Diagnosis, ClinicalNote, LabOrder, Specimen, PrescriptionItem):
        assert "deleted_at" in model.__table__.c, model
        assert issubclass(model, SoftDeleteMixin), model
    for model in (PatientAssignment, LabTestCatalog, ReferenceRange, PrescriptionHistory, PrescriptionChange, Permission):
        assert "deleted_at" not in model.__table__.c, model
        assert not issubclass(model, SoftDeleteMixin), model


def test_new_text_columns_are_text_not_string() -> None:
    """Free-text clinical narrative is UTF-8 TEXT (never length-limited)."""
    for column in (
        Diagnosis.__table__.c.code,
        Diagnosis.__table__.c.name,
        ClinicalNote.__table__.c.symptoms,
        ClinicalNote.__table__.c.assessment,
        ClinicalNote.__table__.c.proposed_plan,
        ClinicalNote.__table__.c.body,
    ):
        assert isinstance(column.type, sa.Text), column


# --- clinical data layer: database behavior (requires PostgreSQL) -----------


def _new_encounter(patient_id: uuid.UUID, occurred_at: datetime | None = None) -> Encounter:
    return Encounter(
        id=uuid.uuid4(),
        patient_id=patient_id,
        occurred_at=occurred_at or datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        encounter_type="outpatient visit",
        summary="Fixture encounter for constraint tests.",
    )


@pytest.mark.postgres
async def test_blood_type_check_accepts_abo_and_rejects_free_text(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-BT-0001")
        patient.blood_type = "A+"
        session.add(patient)
        await session.commit()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        stored = await session.scalar(sa.select(Patient).where(Patient.mrn == "MRN-BT-0001"))
        assert stored is not None and stored.blood_type == "A+"
        await session.execute(sa.delete(Patient).where(Patient.mrn == "MRN-BT-0001"))
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-BT-0002")
        patient.blood_type = "O"  # not a valid ABO/Rh combination
        session.add(patient)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.postgres
async def test_diagnosis_status_check_and_soft_delete_filter(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-DX-0001")
        session.add(patient)
        await session.commit()
        patient_id = patient.id
        session.add(
            Diagnosis(
                id=uuid.uuid4(),
                patient_id=patient_id,
                code="E11.9",
                name="Type 2 diabetes mellitus without complications",
                status="chronic",  # not a valid problem-list status
                recorded_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                Diagnosis(
                    id=uuid.uuid4(),
                    patient_id=patient_id,
                    code="E11.9",
                    name="Type 2 diabetes mellitus without complications",
                    status="active",
                    recorded_at=datetime.now(timezone.utc),
                ),
                Diagnosis(
                    id=uuid.uuid4(),
                    patient_id=patient_id,
                    code="J06.9",
                    name="Acute upper respiratory infection, unspecified",
                    status="resolved",
                    recorded_at=datetime.now(timezone.utc),
                    deleted_at=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        visible = (await session.scalars(sa.select(Diagnosis).where(Diagnosis.not_deleted()))).all()
        visible_codes = {row.code for row in visible if row.patient_id == patient_id}
        assert "E11.9" in visible_codes
        assert "J06.9" not in visible_codes
        await session.execute(sa.delete(Diagnosis).where(Diagnosis.patient_id == patient_id))
        await session.execute(sa.delete(Patient).where(Patient.id == patient_id))
        await session.commit()


@pytest.mark.postgres
async def test_patient_assignment_uniqueness_and_role_check(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-AS-0001")
        staff = _new_staff("assignment-test@example.com")
        session.add_all([patient, staff])
        await session.commit()
        patient_id, staff_id = patient.id, staff.id
        session.add(
            PatientAssignment(
                id=uuid.uuid4(), patient_id=patient_id, staff_user_id=staff_id,
                care_role="attending", assigned_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        session.add(
            PatientAssignment(
                id=uuid.uuid4(), patient_id=patient_id, staff_user_id=staff_id,
                care_role="attending", assigned_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(
            PatientAssignment(
                id=uuid.uuid4(), patient_id=patient_id, staff_user_id=staff_id,
                care_role="consulting", assigned_at=datetime.now(timezone.utc),  # not a care role
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.execute(sa.delete(PatientAssignment).where(PatientAssignment.patient_id == patient_id))
        await session.execute(sa.delete(StaffUser).where(StaffUser.id == staff_id))
        await session.execute(sa.delete(Patient).where(Patient.id == patient_id))
        await session.commit()


@pytest.mark.postgres
async def test_prescription_item_sequence_constraints(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-RX-0001")
        session.add(patient)
        await session.commit()
        patient_id = patient.id
        prescription = Prescription(
            id=uuid.uuid4(),
            patient_id=patient_id,
            medication_name="Metformin",
            dose_amount=Decimal("500"),
            dose_unit="mg",
            route="oral",
            frequency="twice daily",
            status="active",
            start_date=date(2026, 1, 1),
        )
        session.add(prescription)
        await session.commit()
        prescription_id = prescription.id
        session.add(
            PrescriptionItem(
                id=uuid.uuid4(), prescription_id=prescription_id, medication_name="Metformin",
                dose_amount=Decimal("500"), dose_unit="mg", sequence_no=1,
            )
        )
        await session.commit()
        session.add(
            PrescriptionItem(
                id=uuid.uuid4(), prescription_id=prescription_id, medication_name="Metformin",
                dose_amount=Decimal("250"), dose_unit="mg", sequence_no=1,  # duplicate sequence
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(
            PrescriptionItem(
                id=uuid.uuid4(), prescription_id=prescription_id, medication_name="Metformin",
                dose_amount=Decimal("500"), dose_unit="mg", sequence_no=0,  # sequence starts at 1
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.execute(sa.delete(PrescriptionItem).where(PrescriptionItem.prescription_id == prescription_id))
        await session.execute(sa.delete(Prescription).where(Prescription.id == prescription_id))
        await session.execute(sa.delete(Patient).where(Patient.id == patient_id))
        await session.commit()


@pytest.mark.postgres
async def test_reference_range_sex_check_and_test_code_fk(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        catalog_row = LabTestCatalog(
            id=uuid.uuid4(), code="MRN-TST-X", name="Fictional test", unit="aU",
            ref_low=Decimal("1"), ref_high=Decimal("2"), category="chemistry",
        )
        session.add(catalog_row)
        await session.commit()
        catalog_id = catalog_row.id
        session.add(
            ReferenceRange(
                id=uuid.uuid4(), test_code="MRN-TST-X", sex="other",  # not a sex value
                ref_low=Decimal("1"), ref_high=Decimal("2"),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(
            ReferenceRange(
                id=uuid.uuid4(), test_code="MRN-NOPE", sex="any",  # unknown test code
                ref_low=Decimal("1"), ref_high=Decimal("2"),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.execute(sa.delete(LabTestCatalog).where(LabTestCatalog.id == catalog_id))
        await session.commit()


@pytest.mark.postgres
async def test_specimen_type_check_and_time_coherence(db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-SP-0001")
        session.add(patient)
        await session.commit()
        patient_id = patient.id
        order = LabOrder(
            id=uuid.uuid4(), patient_id=patient_id,
            ordered_at=datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc), status="completed",
        )
        session.add(order)
        await session.commit()
        order_id = order.id
        session.add(
            Specimen(
                id=uuid.uuid4(), lab_order_id=order_id, specimen_type="plasma",  # not a specimen type
                status="resulted",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(
            Specimen(
                id=uuid.uuid4(), lab_order_id=order_id, specimen_type="blood", status="resulted",
                collected_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
                received_at=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),  # received before collected
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(
            Specimen(
                id=uuid.uuid4(), lab_order_id=order_id, specimen_type="urine", status="received",
                collected_at=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
                received_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
            )
        )
        await session.commit()
        await session.execute(sa.delete(Specimen).where(Specimen.lab_order_id == order_id))
        await session.execute(sa.delete(LabOrder).where(LabOrder.id == order_id))
        await session.execute(sa.delete(Patient).where(Patient.id == patient_id))
        await session.commit()


@pytest.mark.postgres
async def test_staff_attribution_set_null_on_staff_delete(db_engine) -> None:
    """Deleting a staff account nulls attribution but keeps the clinical record."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = _new_patient("MRN-ATTR-0001")
        staff = _new_staff("attribution-test@example.com")
        session.add_all([patient, staff])
        await session.flush()
        encounter = _new_encounter(patient.id)
        encounter.practitioner_id = staff.id
        session.add(encounter)
        await session.commit()
        encounter_id = encounter.id
        staff_id = staff.id

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await session.execute(sa.delete(StaffUser).where(StaffUser.id == staff_id))
        await session.commit()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        stored = await session.scalar(sa.select(Encounter).where(Encounter.id == encounter_id))
        assert stored is not None and stored.practitioner_id is None
        await session.execute(sa.delete(Encounter).where(Encounter.id == encounter_id))
        await session.execute(sa.delete(Patient).where(Patient.mrn == "MRN-ATTR-0001"))
        await session.commit()
