"""Clinical API tests (docs/API.md Phase 6): patients, summary, labs, prescriptions.

Typed bounded payloads, pagination limits, literal `?q=` search, empty states,
soft-delete/concealment behavior (404, never 403), Decimal-as-string values,
UTC ISO-8601 'Z' timestamps, database-internal provenance, and read auditing.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LabResult, Patient
from conftest import audit_rows, csrf_headers

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# --- pagination and query bounds ------------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?limit=-1", "?offset=-1", "?limit=abc"])
async def test_pagination_bounds_rejected(query, auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/patients{query}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.postgres
async def test_pagination_bounds_accepted(auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    for query in ("?limit=1", "?limit=100", "?offset=0", "?limit=5&offset=3"):
        response = await client.get(f"/api/v1/patients{query}")
        assert response.status_code == 200, query
        body = response.json()
        assert set(body) == {"items", "total", "limit", "offset"}
        assert body["limit"] <= 100


@pytest.mark.postgres
async def test_unknown_query_parameters_are_validation_errors(auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get("/api/v1/patients?q=demo&sort=asc")
    assert response.status_code == 400
    fields = response.json()["error"]["details"]["fields"]
    assert fields == [
        {"loc": "query.sort", "msg": "Unknown query parameter.", "type": "unknown_parameter"}
    ]

    labs = await client.get(f"/api/v1/patients/{uuid.uuid4()}/labs?q=anything")
    assert labs.status_code == 400


@pytest.mark.postgres
async def test_search_query_length_is_bounded(auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/patients?q={'x' * 101}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# --- patient deletion ------------------------------------------------------


@pytest.mark.postgres
async def test_admin_soft_deletes_patient_but_doctor_and_nurse_cannot(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    target = str(clinical_data.patient_id)

    for client in (doctor_client, nurse_client):
        denied = await client.delete(
            f"/api/v1/patients/{target}", headers=csrf_headers(client)
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["code"] == "forbidden"

    deleted = await admin_client.delete(
        f"/api/v1/patients/{target}", headers=csrf_headers(admin_client)
    )
    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""

    assert (await admin_client.get(f"/api/v1/patients/{target}")).status_code == 404
    assert (
        await admin_client.get(f"/api/v1/encounters/{clinical_data.encounter_id}")
    ).status_code == 404
    listed = await admin_client.get(
        f'/api/v1/patients?q={clinical_data.patient_name}'
    )
    assert listed.status_code == 200
    assert target not in {item["id"] for item in listed.json()["items"]}

    # Child clinical rows remain in the database for audit/history; the
    # patient row is soft-deleted, never hard-deleted.
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        row = await db.scalar(select(Patient).where(Patient.id == clinical_data.patient_id))
        assert row is not None and row.deleted_at is not None
        labs = list(
            (
                await db.scalars(
                    select(LabResult.id).where(LabResult.patient_id == clinical_data.patient_id)
                )
            ).all()
        )
        assert labs

    rows = await audit_rows(action="patient.delete", resource_id=target)
    assert len(rows) == 1
    assert rows[0].actor_user_id == admin.id
    assert rows[0].outcome == "success"

    again = await admin_client.delete(
        f"/api/v1/patients/{target}", headers=csrf_headers(admin_client)
    )
    assert again.status_code == 404


# --- patient creation ------------------------------------------------------


@pytest.mark.postgres
async def test_doctor_and_admin_create_patients_but_nurse_cannot(
    db_engine, auth_client, make_user
) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    suffix = uuid.uuid4().hex[:8]

    body = {
        "given_name": f"Нов {suffix}",
        "family_name": "Пациент",
        "date_of_birth": "1990-01-02",
        "sex": "female",
        "blood_type": "A+",
    }
    denied = await nurse_client.post(
        "/api/v1/patients", json=body, headers=csrf_headers(nurse_client)
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    created = await doctor_client.post(
        "/api/v1/patients", json=body, headers=csrf_headers(doctor_client)
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["mrn"].startswith("MRN-")
    assert payload["given_name"] == body["given_name"]
    assert payload["family_name"] == body["family_name"]
    assert payload["display_name"] == f'{body["given_name"]} {body["family_name"]}'
    assert payload["sex"] == "female"
    assert payload["blood_type"] == "A+"
    assert isinstance(payload["age_years"], int) and payload["age_years"] >= 0
    assert "provenance" not in payload
    assert "diagnoses" not in payload and "latest_note" not in payload

    second = await admin_client.post(
        "/api/v1/patients",
        json={
            "given_name": f"Админ {suffix}",
            "family_name": "Пациент",
            "sex": "unknown",
            "display_name": f"Preferred {suffix}",
        },
        headers=csrf_headers(admin_client),
    )
    assert second.status_code == 201, second.text
    assert second.json()["display_name"] == f"Preferred {suffix}"
    assert second.json()["mrn"] != payload["mrn"]

    # Persisted in the database with synthetic provenance.
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        row = await db.scalar(select(Patient).where(Patient.id == uuid.UUID(payload["id"])))
        assert row is not None
        assert row.provenance == "synthetic"
        assert row.mrn == payload["mrn"]

    found = await doctor_client.get(f'/api/v1/patients?q={body["given_name"]}')
    assert found.status_code == 200
    assert payload["id"] in {item["id"] for item in found.json()["items"]}

    created_audit = await audit_rows(action="patient.create", resource_id=payload["id"])
    assert len(created_audit) == 1
    assert created_audit[0].actor_user_id == doctor.id


@pytest.mark.postgres
async def test_patient_create_validates_fields(auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    cases = [
        {"family_name": "Missing given"},
        {"given_name": "Missing family"},
        {"given_name": " ", "family_name": "Blank"},
        {"given_name": "Future", "family_name": "Person", "date_of_birth": "2999-01-01"},
        {"given_name": "Bad", "family_name": "Sex", "sex": "robot"},
        {"given_name": "Bad", "family_name": "Blood", "blood_type": "Z+"},
        {"given_name": "Extra", "family_name": "Field", "mrn": "MRN-HACK"},
    ]
    for body in cases:
        response = await client.post(
            "/api/v1/patients", json=body, headers=csrf_headers(client)
        )
        assert response.status_code == 400, (body, response.text)
        assert response.json()["error"]["code"] == "validation_error"


# --- search behavior -------------------------------------------------------


@pytest.mark.postgres
async def test_q_searches_display_name_and_mrn(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)

    by_name = await client.get("/api/v1/patients", params={"q": clinical_data.token})
    assert by_name.status_code == 200
    body = by_name.json()
    assert body["total"] == 2  # the soft-deleted fixture patient is excluded
    names = {item["display_name"] for item in body["items"]}
    assert names == {clinical_data.patient_name, clinical_data.empty_patient_name}

    by_mrn = await client.get("/api/v1/patients", params={"q": clinical_data.patient_mrn})
    assert by_mrn.json()["total"] == 1
    assert by_mrn.json()["items"][0]["id"] == str(clinical_data.patient_id)


@pytest.mark.postgres
async def test_q_wildcards_are_escaped(auth_client, make_user, clinical_data, db_engine) -> None:
    percent_name = f"EscapeCase {clinical_data.token} 100% A"
    plain_name = f"EscapeCase {clinical_data.token} 100X B"
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        for suffix, name in (("A", percent_name), ("B", plain_name)):
            session.add(
                Patient(
                    mrn=f"MRN-E{clinical_data.token}{suffix}",
                    display_name=name,
                    given_name="Escape",
                    family_name="Case",
                    sex="other",
                    provenance="synthetic",
                )
            )
        await session.commit()

    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    # '%' must match literally: only the 100% patient, never the 100X patient.
    response = await client.get(
        "/api/v1/patients", params={"q": f"{clinical_data.token} 100%"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["display_name"] == percent_name


@pytest.mark.postgres
async def test_search_empty_state(auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get("/api/v1/patients?q=nothing-matches-this-query")
    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


# --- patient detail and summary --------------------------------------------


@pytest.mark.postgres
async def test_patient_detail_shape(auth_client, make_user, clinical_data) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/patients/{clinical_data.patient_id}")
    assert response.status_code == 200
    item = response.json()
    assert item["id"] == str(clinical_data.patient_id)
    assert item["mrn"] == clinical_data.patient_mrn
    assert item["display_name"] == clinical_data.patient_name
    assert item["date_of_birth"] == "1980-01-15"
    assert item["sex"] == "female"
    assert ISO_Z.match(item["created_at"]) and ISO_Z.match(item["updated_at"])


@pytest.mark.postgres
async def test_patient_summary_shape_is_bounded_and_typed(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/patients/{clinical_data.patient_id}/summary")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {
        "patient",
        "encounters",
        "active_problems",
        "problems",
        "latest_labs",
        "active_prescriptions",
        # additive summary extension (2026-10-27, docs/API.md summary payload)
        "age_years",
        "blood_type",
        "vitals",
        "diagnoses",
        "symptoms",
        "assessment",
        "proposed_plan",
        "prescribed_treatment",
        "last_visit",
        "visit_count",
        "medication_responses",
    }
    assert body["patient"]["id"] == str(clinical_data.patient_id)
    assert body["active_problems"] == [] and body["problems"] == []
    assert body["visit_count"] == 2
    assert body["last_visit"]["id"] == str(clinical_data.encounter_id)
    assert body["medication_responses"] == []

    # Encounters: newest first, soft-deleted excluded, bounded list.
    assert [e["id"] for e in body["encounters"]] == [
        str(clinical_data.encounter_id),
        str(clinical_data.old_encounter_id),
    ]
    assert body["encounters"][0]["encounter_type"] == "annual exam"
    assert ISO_Z.match(body["encounters"][0]["occurred_at"])

    # Latest labs: most recent per test code (2 GLU, 1 K, 1 TSH), newest first.
    lab_ids = [lab["id"] for lab in body["latest_labs"]]
    assert lab_ids == [
        str(clinical_data.lab_ids[1]),  # GLU 2026-10-03
        str(clinical_data.lab_ids[2]),  # K 2026-10-02
        str(clinical_data.lab_ids[0]),  # GLU 2026-10-01
        str(clinical_data.lab_ids[3]),  # TSH 2026-09-15
    ]
    glu = body["latest_labs"][0]
    assert glu["value_num"] == "6.100"
    assert glu["ref_low"] == "4.000" and glu["ref_high"] == "6.000"
    assert glu["unit"] == "mg/dL"
    assert ISO_Z.match(glu["collected_at"])

    # Active prescriptions only (completed and soft-deleted excluded).
    assert [rx["id"] for rx in body["active_prescriptions"]] == [
        str(clinical_data.prescription_ids[1]),
        str(clinical_data.prescription_ids[0]),
    ]
    assert body["active_prescriptions"][0]["dose_amount"] == "10.000"


@pytest.mark.postgres
async def test_patient_encounters_pagination_order_and_concealment(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)

    first = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/encounters",
        params={"limit": 1, "offset": 0},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 2  # the soft-deleted encounter is excluded
    assert body["limit"] == 1 and body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [str(clinical_data.encounter_id)]
    assert body["items"][0]["encounter_type"] == "annual exam"
    assert ISO_Z.match(body["items"][0]["occurred_at"])

    second = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/encounters",
        params={"limit": 1, "offset": 1},
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == [
        str(clinical_data.old_encounter_id)
    ]

    missing = await client.get(f"/api/v1/patients/{uuid.uuid4()}/encounters")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    unknown = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/encounters?sort=desc"
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "validation_error"

    rows = await audit_rows(
        action="patient.read",
        resource_type="patient",
        resource_id=str(clinical_data.patient_id),
        request_id=first.headers["x-request-id"],
    )
    assert rows
    assert rows[-1].outcome == "success"


@pytest.mark.postgres
async def test_empty_patient_summary_and_empty_states(auth_client, make_user, clinical_data) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    summary = await client.get(f"/api/v1/patients/{clinical_data.empty_patient_id}/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["encounters"] == []
    assert body["latest_labs"] == []
    assert body["active_prescriptions"] == []
    assert body["problems"] == []

    labs = await client.get(f"/api/v1/patients/{clinical_data.empty_patient_id}/labs")
    assert labs.status_code == 200
    assert labs.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}

    prescriptions = await client.get(
        f"/api/v1/patients/{clinical_data.empty_patient_id}/prescriptions"
    )
    assert prescriptions.status_code == 200
    assert prescriptions.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


@pytest.mark.postgres
async def test_summary_extension_fields_are_additive_and_typed(
    monkeypatch, auth_client, make_user, clinical_data, db_engine
) -> None:
    """The REST summary carries the roundup fields (same facts as the AI tool).

    Patient detail stays demographics-only: the summary transport carriers are
    never serialized, and no pre-existing key changed.
    """
    from datetime import datetime, timezone
    from decimal import Decimal

    from app.models import ClinicalNote, Diagnosis, Encounter
    from app.services import patients as patients_service

    fixed_now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(patients_service, "utc_now", lambda: fixed_now)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        patient = await session.get(Patient, clinical_data.patient_id)
        patient.blood_type = "A+"
        recent = await session.get(Encounter, clinical_data.encounter_id)
        recent.weight_kg = Decimal("70.50")
        recent.height_cm = Decimal("165.00")
        older = await session.get(Encounter, clinical_data.old_encounter_id)
        older.weight_kg = Decimal("72.00")
        older.height_cm = Decimal("165.00")
        session.add(
            Diagnosis(
                patient_id=clinical_data.patient_id,
                encounter_id=clinical_data.encounter_id,
                code="E11.9",
                name="Type 2 diabetes mellitus",
                status="active",
            )
        )
        session.add(
            ClinicalNote(
                patient_id=clinical_data.patient_id,
                encounter_id=clinical_data.encounter_id,
                note_type="progress_note",
                symptoms="fatigue and cold intolerance",
                assessment="hypothyroidism",
                proposed_plan="start levothyroxine; recheck TSH in 6 weeks",
                body="Synthetic demo note.",
            )
        )
        await session.commit()

    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/patients/{clinical_data.patient_id}/summary")
    assert response.status_code == 200
    body = response.json()

    # Demographics additions (derived age; never stored).
    assert body["patient"]["blood_type"] == "A+"
    assert body["patient"]["age_years"] == 46  # dob 1980-01-15 at the frozen date
    assert body["age_years"] == 46
    assert body["blood_type"] == "A+"

    # Vitals trend from the per-visit snapshots (newest first).
    assert body["vitals"] == {
        "weight_kg": "70.50",
        "height_cm": "165.00",
        "measured_at": "2026-10-01T09:30:00Z",
        "previous_weight_kg": "72.00",
        "previous_measured_at": "2026-09-01T14:00:00Z",
        "systolic_bp": None,
        "diastolic_bp": None,
        "heart_rate": None,
        "temperature_c": None,
        "oxygen_saturation": None,
        "previous_systolic_bp": None,
        "previous_diastolic_bp": None,
        "previous_heart_rate": None,
    }
    assert body["encounters"][0]["weight_kg"] == "70.50"
    assert body["encounters"][1]["weight_kg"] == "72.00"

    # Problem list: diagnoses active-first; active_problems is the active subset.
    assert body["diagnoses"] == [
        {
            "code": "E11.9",
            "name": "Type 2 diabetes mellitus",
            "status": "active",
            "onset_date": None,
        }
    ]
    assert body["active_problems"] == [
        {
            "code": "E11.9",
            "display_name": "Type 2 diabetes mellitus",
            "status": "active",
            "onset_date": None,
        }
    ]
    assert body["problems"] == body["active_problems"]

    # Latest note sections (record content, never advice).
    assert body["symptoms"] == "fatigue and cold intolerance"
    assert body["assessment"] == "hypothyroidism"
    assert body["proposed_plan"] == "start levothyroxine; recheck TSH in 6 weeks"

    # Prescribed treatment = active prescriptions (newest start date first).
    assert [item["medication"] for item in body["prescribed_treatment"]] == [
        "Lisinopril",
        "Metformin",
    ]
    assert body["prescribed_treatment"][1]["dose_amount"] == "500.000"
    assert body["prescribed_treatment"][1]["items"] == []

    # Patient detail is demographics-only: transport carriers never serialize.
    detail = await client.get(f"/api/v1/patients/{clinical_data.patient_id}")
    assert detail.status_code == 200
    item = detail.json()
    assert "diagnoses" not in item and "latest_note" not in item
    assert item["blood_type"] == "A+" and item["age_years"] == 46


# --- labs and prescriptions ------------------------------------------------


@pytest.mark.postgres
async def test_labs_pagination_order_and_decimal_strings(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("nurse",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/patients/{clinical_data.patient_id}/labs?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4  # soft-deleted lab excluded
    assert body["limit"] == 2 and body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [
        str(clinical_data.lab_ids[1]),
        str(clinical_data.lab_ids[2]),
    ]
    first = body["items"][0]
    assert first["value_num"] == "6.100"
    assert isinstance(first["value_num"], str)
    assert isinstance(first["ref_low"], str)
    assert first["test_code"] == "GLU"

    page_two = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/labs?limit=2&offset=2"
    )
    assert [item["id"] for item in page_two.json()["items"]] == [
        str(clinical_data.lab_ids[0]),
        str(clinical_data.lab_ids[3]),
    ]
    # Text-valued result stays null-num / text.
    tsh = page_two.json()["items"][1]
    assert tsh["value_num"] is None
    assert tsh["value_text"] == "within range"


@pytest.mark.postgres
async def test_prescriptions_pagination_and_decimal_doses(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/patients/{clinical_data.patient_id}/prescriptions")
    assert response.status_code == 200
    body = response.json()
    # All non-deleted prescriptions (2 active + 1 completed), newest first.
    assert body["total"] == 3
    assert [item["medication_name"] for item in body["items"]] == [
        "Lisinopril",
        "Metformin",
        "Amoxicillin",
    ]
    assert body["items"][1]["dose_amount"] == "500.000"
    assert isinstance(body["items"][1]["dose_amount"], str)
    assert body["items"][0]["start_date"] == "2026-02-01"
    assert body["items"][2]["status"] == "completed"


# --- concealment and IDOR --------------------------------------------------


@pytest.mark.postgres
async def test_soft_deleted_patient_is_404(auth_client, make_user, clinical_data) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    for path in (
        f"/api/v1/patients/{clinical_data.soft_deleted_patient_id}",
        f"/api/v1/patients/{clinical_data.soft_deleted_patient_id}/summary",
        f"/api/v1/patients/{clinical_data.soft_deleted_patient_id}/labs",
        f"/api/v1/patients/{clinical_data.soft_deleted_patient_id}/prescriptions",
    ):
        response = await client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found"


@pytest.mark.postgres
async def test_unknown_patient_id_is_404_not_403(auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    guessed = uuid.uuid4()
    response = await client.get(f"/api/v1/patients/{guessed}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "forbidden" not in response.text.lower()


@pytest.mark.postgres
async def test_any_holder_of_patient_read_can_read_any_synthetic_patient(
    auth_client, make_user, clinical_data
) -> None:
    # Demo policy: patient:read grants access to every synthetic patient.
    doctor = await make_user(roles=("doctor",))
    nurse = await make_user(roles=("nurse",))
    for account in (doctor, nurse):
        client = await auth_client(account)
        response = await client.get(f"/api/v1/patients/{clinical_data.patient_id}")
        assert response.status_code == 200


@pytest.mark.postgres
async def test_user_without_patient_read_cannot_guess_ids(
    auth_client, make_user, clinical_data
) -> None:
    # Every real role holds patient:read in the three-role model, so the
    # permission-level denial (403, not concealment) is exercised with an
    # authenticated account holding no roles at all.
    account = await make_user(roles=())
    client = await auth_client(account)
    guessed = await client.get(f"/api/v1/patients/{clinical_data.patient_id}")
    assert guessed.status_code == 403  # permission-level denial, not concealment
    assert guessed.json()["error"]["code"] == "forbidden"


@pytest.mark.postgres
async def test_encounter_concealment_is_404(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)

    found = await client.get(f"/api/v1/encounters/{clinical_data.encounter_id}")
    assert found.status_code == 200
    item = found.json()
    assert item["id"] == str(clinical_data.encounter_id)
    assert item["patient_id"] == str(clinical_data.patient_id)
    assert item["encounter_type"] == "annual exam"
    assert ISO_Z.match(item["occurred_at"])

    for encounter_id in (clinical_data.soft_deleted_encounter_id, uuid.uuid4()):
        missing = await client.get(f"/api/v1/encounters/{encounter_id}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"


# --- read auditing ---------------------------------------------------------


@pytest.mark.postgres
async def test_patient_reads_are_audited(db_engine, auth_client, make_user, clinical_data) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)

    cases = [
        (f"/api/v1/patients?q={clinical_data.token}", "patient.read", None),
        (f"/api/v1/patients/{clinical_data.patient_id}", "patient.read", clinical_data.patient_id),
        (
            f"/api/v1/patients/{clinical_data.patient_id}/summary",
            "patient.summary.read",
            clinical_data.patient_id,
        ),
        (
            f"/api/v1/patients/{clinical_data.patient_id}/labs",
            "labs.read",
            clinical_data.patient_id,
        ),
        (
            f"/api/v1/patients/{clinical_data.patient_id}/prescriptions",
            "prescriptions.read",
            clinical_data.patient_id,
        ),
    ]
    for path, action, resource_id in cases:
        response = await client.get(path)
        assert response.status_code == 200, path
        rows = await audit_rows(
            request_id=response.headers["X-Request-Id"], action=action
        )
        assert len(rows) == 1, path
        assert rows[0].actor_user_id == account.id
        assert rows[0].outcome == "success"
        assert rows[0].resource_type == "patient"
        assert rows[0].resource_id == (str(resource_id) if resource_id else None)


@pytest.mark.postgres
async def test_concealed_reads_are_audited_as_denied(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(f"/api/v1/encounters/{uuid.uuid4()}")
    assert response.status_code == 404
    rows = await audit_rows(
        request_id=response.headers["X-Request-Id"], action="patient.read"
    )
    assert len(rows) == 1
    assert rows[0].outcome == "denied"
    assert rows[0].resource_type == "encounter"
    assert rows[0].actor_user_id == account.id
