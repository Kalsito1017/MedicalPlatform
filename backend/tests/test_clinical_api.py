"""Clinical API tests (docs/API.md Phase 6): patients, summary, labs, prescriptions.

Typed bounded payloads, pagination limits, literal `?q=` search, empty states,
soft-delete/concealment behavior (404, never 403), Decimal-as-string values,
UTC ISO-8601 'Z' timestamps, provenance labels, and read auditing.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Patient
from conftest import audit_rows

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
                    mrn=f"DEMO-E{clinical_data.token}{suffix}",
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
async def test_patient_detail_shape_and_provenance(auth_client, make_user, clinical_data) -> None:
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
    assert item["provenance"] == "synthetic"
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
        "provenance",
    }
    assert body["patient"]["id"] == str(clinical_data.patient_id)
    assert body["provenance"] == "synthetic"
    assert body["active_problems"] == [] and body["problems"] == []

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
    assert first["provenance"] == "synthetic"

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
    assert body["items"][0]["provenance"] == "synthetic"


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
    assert "403" not in response.text


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
    assert item["provenance"] == "synthetic"

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
