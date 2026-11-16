"""ICD-10 catalogue, diagnosis-code assignment, and print-read tests."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diagnosis
from app.services import icd10
from conftest import audit_rows, csrf_headers


def test_catalogue_metadata_and_search() -> None:
    catalogue = icd10.load_catalogue()
    assert catalogue.metadata["version"].startswith("NCPHA-2026")
    assert catalogue.metadata["source_organization"].startswith("Национален център")
    assert "NCPHA" in catalogue.metadata["license_note"]

    assert catalogue.lookup("I10").name_bg == "ЕСЕНЦИАЛНА [ПЪРВИЧНА] ХИПЕРТОНИЯ"
    assert [entry.code for entry in catalogue.search("I10")][0] == "I10"
    assert any(entry.code == "I10" for entry in catalogue.search("хипертония"))
    assert any(entry.code == "I10" for entry in catalogue.search("hypertension"))
    assert any(entry.code == "E11.9" for entry in catalogue.search("E11"))
    assert any(entry.code == "J30.4" for entry in catalogue.search("алергичен"))
    assert catalogue.search("zzzz") == []
    assert catalogue.search("i") == []  # below the minimum query length


def test_catalogue_normalization_and_statuses() -> None:
    catalogue = icd10.load_catalogue()
    assert icd10.normalize_code(" i10 ") == "I10"
    assert icd10.normalize_code("i10.0") == "I10.0"
    assert icd10.normalize_code("") is None

    assert catalogue.describe(None) == (None, None, "missing")
    description_bg, description_en, status = catalogue.describe("I10")
    assert description_bg.startswith("ЕСЕНЦИАЛНА")
    assert description_en == "Essential (primary) hypertension"
    assert status == "confirmed"
    assert catalogue.describe("R53.83") == (None, None, "unknown")

    assert catalogue.require_active("I10").code == "I10"
    with pytest.raises(icd10.Icd10CodeError):
        catalogue.require_active("R53.83")
    with pytest.raises(icd10.Icd10CodeError):
        catalogue.require_active("not-a-code")

    diagnosis = SimpleNamespace(code="I10")
    catalogue.decorate(diagnosis)
    assert diagnosis.code_status == "confirmed"
    assert diagnosis.code_description.startswith("ЕСЕНЦИАЛНА")
    assert diagnosis.code_description_en == "Essential (primary) hypertension"
    assert diagnosis.code_version == catalogue.version


@pytest.mark.postgres
async def test_icd10_search_permissions_and_bounds(auth_client, make_user) -> None:
    doctor = await make_user(roles=("doctor",))
    nurse = await make_user(roles=("nurse",))
    admin = await make_user(roles=("clinical_admin",))

    doctor_client = await auth_client(doctor)
    response = await doctor_client.get("/api/v1/icd10/search?q=I10")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"][0]["code"] == "I10"
    assert payload["items"][0]["name_bg"].startswith("ЕСЕНЦИАЛНА")
    assert payload["catalogue"]["version"].startswith("NCPHA-2026")

    nurse_client = await auth_client(nurse)
    denied = await nurse_client.get("/api/v1/icd10/search?q=I10")
    assert denied.status_code == 403

    admin_client = await auth_client(admin)
    assert (await admin_client.get("/api/v1/icd10/search?q=hypertension")).status_code == 200

    short = await doctor_client.get("/api/v1/icd10/search?q=I")
    assert short.status_code == 400


async def _add_diagnosis(
    db_engine,
    patient_id: uuid.UUID,
    *,
    code: str | None,
    name: str,
    status: str = "active",
) -> uuid.UUID:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = Diagnosis(
            patient_id=patient_id,
            code=code,
            name=name,
            status=status,
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest.mark.postgres
async def test_patient_diagnoses_list_includes_catalogue_statuses(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    active_id = await _add_diagnosis(
        db_engine, clinical_data.patient_id, code="I10", name="Хипертония"
    )
    unknown_id = await _add_diagnosis(
        db_engine, clinical_data.patient_id, code="R53.83", name="Друга умора"
    )
    missing_id = await _add_diagnosis(
        db_engine, clinical_data.patient_id, code=None, name="Неуточнена диагноза"
    )
    resolved_id = await _add_diagnosis(
        db_engine,
        clinical_data.patient_id,
        code="E11.9",
        name="Захарен диабет",
        status="resolved",
    )

    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    response = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/diagnoses?limit=4"
    )
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["total"] == 4
    assert len(first["items"]) == 4
    assert first["items"][0]["status"] == "active"  # active-first ordering
    by_id = {item["id"]: item for item in first["items"]}
    assert by_id[str(active_id)]["code_status"] == "confirmed"
    assert by_id[str(active_id)]["code_description"].startswith("ЕСЕНЦИАЛНА")
    assert by_id[str(unknown_id)]["code_status"] == "unknown"
    assert by_id[str(unknown_id)]["code_description"] is None
    assert by_id[str(missing_id)]["code_status"] == "missing"
    assert by_id[str(missing_id)]["code"] is None
    assert by_id[str(resolved_id)]["code_status"] == "confirmed"

    page = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/diagnoses?limit=2&offset=2"
    )
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert len(page.json()["items"]) == 2

    rows = await audit_rows(action="diagnoses.read", outcome="success")
    assert rows


@pytest.mark.postgres
async def test_doctor_can_assign_change_and_clear_code(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    diagnosis_id = await _add_diagnosis(
        db_engine, clinical_data.patient_id, code=None, name="Хипертония"
    )
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    base = f"/api/v1/patients/{clinical_data.patient_id}/diagnoses/{diagnosis_id}"

    assigned = await client.patch(base, json={"code": "i10"}, headers=csrf_headers(client))
    assert assigned.status_code == 200, assigned.text
    body = assigned.json()
    assert body["code"] == "I10"
    assert body["code_status"] == "confirmed"
    assert body["code_description"].startswith("ЕСЕНЦИАЛНА")
    assert body["code_description_en"] == "Essential (primary) hypertension"

    changed = await client.patch(base, json={"code": "E11.9"}, headers=csrf_headers(client))
    assert changed.status_code == 200
    assert changed.json()["code"] == "E11.9"

    cleared = await client.patch(base, json={"code": None}, headers=csrf_headers(client))
    assert cleared.status_code == 200
    assert cleared.json()["code"] is None
    assert cleared.json()["code_status"] == "missing"

    rows = await audit_rows(action="diagnosis.code.update", outcome="success")
    assert len(rows) >= 3


@pytest.mark.postgres
async def test_invalid_code_is_rejected_and_audited(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    diagnosis_id = await _add_diagnosis(
        db_engine, clinical_data.patient_id, code=None, name="Хипертония"
    )
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    base = f"/api/v1/patients/{clinical_data.patient_id}/diagnoses/{diagnosis_id}"

    malformed = await client.patch(base, json={"code": "not-a-code"}, headers=csrf_headers(client))
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "validation_error"
    assert "code" in malformed.json()["error"]["details"]["fields"]

    unknown = await client.patch(base, json={"code": "Z99.9"}, headers=csrf_headers(client))
    assert unknown.status_code == 400

    rows = await audit_rows(action="diagnosis.code.update", outcome="error")
    assert rows


@pytest.mark.postgres
async def test_nurse_cannot_assign_codes_and_wrong_patient_is_concealed(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    diagnosis_id = await _add_diagnosis(
        db_engine, clinical_data.patient_id, code=None, name="Хипертония"
    )
    nurse = await make_user(roles=("nurse",))
    nurse_client = await auth_client(nurse)
    denied = await nurse_client.patch(
        f"/api/v1/patients/{clinical_data.patient_id}/diagnoses/{diagnosis_id}",
        json={"code": "I10"},
        headers=csrf_headers(nurse_client),
    )
    assert denied.status_code == 403

    doctor = await make_user(roles=("doctor",))
    doctor_client = await auth_client(doctor)
    wrong_patient = await doctor_client.patch(
        f"/api/v1/patients/{uuid.uuid4()}/diagnoses/{diagnosis_id}",
        json={"code": "I10"},
        headers=csrf_headers(doctor_client),
    )
    assert wrong_patient.status_code == 404
