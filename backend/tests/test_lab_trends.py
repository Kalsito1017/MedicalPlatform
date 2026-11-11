"""Numeric lab trend endpoint tests (docs/API.md 2026-11-04 contract)."""
from __future__ import annotations

import uuid

import pytest

from conftest import audit_rows


@pytest.mark.postgres
async def test_lab_trends_shape_and_flags(auth_client, make_user, clinical_data) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/labs/trends",
        params={"days": 365, "tests": "GLU,K"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"patient_id", "window_days", "series", "truncated"}
    assert body["patient_id"] == str(clinical_data.patient_id)
    assert body["window_days"] == 365
    assert body["truncated"] is False
    assert [series["test_code"] for series in body["series"]] == ["GLU", "K"]

    glu = body["series"][0]
    assert glu["test_name"] == "Glucose, fasting"
    assert glu["unit"] == "mg/dL"
    assert glu["points"] == [
        {
            "collected_at": "2026-10-01T07:30:00Z",
            "value_num": "5.500",
            "flag": "normal",
        },
        {
            "collected_at": "2026-10-03T07:30:00Z",
            "value_num": "6.100",
            "flag": "high",
        },
    ]
    assert glu["abnormal_count"] == 1
    assert body["series"][1]["points"] == [
        {
            "collected_at": "2026-10-02T08:00:00Z",
            "value_num": "4.200",
            "flag": "normal",
        }
    ]


@pytest.mark.postgres
async def test_lab_trends_default_window_auto_selects_numeric_tests(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/labs/trends"
    )
    assert response.status_code == 200
    body = response.json()
    assert {series["test_code"] for series in body["series"]} == {"GLU", "K"}


@pytest.mark.postgres
async def test_lab_trends_rejects_bad_bounds_and_unknown_queries(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    base = f"/api/v1/patients/{clinical_data.patient_id}/labs/trends"
    for query in ("?days=0", "?days=366", "?tests=", "?tests=GLU,", "?tests=GLU,K,TSH,HGB,ALT,LDL"):
        response = await client.get(f"{base}{query}")
        assert response.status_code == 400, query
        assert response.json()["error"]["code"] == "validation_error"

    response = await client.get(f"{base}?days=365&sort=asc")
    assert response.status_code == 400
    fields = response.json()["error"]["details"]["fields"]
    assert fields == [
        {"loc": "query.sort", "msg": "Unknown query parameter.", "type": "unknown_parameter"}
    ]


@pytest.mark.postgres
async def test_lab_trends_rejects_unknown_test_code_without_echoing_it(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    submitted = "SECRET-CODE-123"
    response = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/labs/trends",
        params={"tests": submitted},
    )
    assert response.status_code == 400
    assert submitted not in response.text
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.postgres
async def test_lab_trends_are_patient_concealed_and_audited(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)

    missing = await client.get(f"/api/v1/patients/{uuid.uuid4()}/labs/trends")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    response = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/labs/trends"
    )
    assert response.status_code == 200
    rows = await audit_rows(
        action="labs.read",
        resource_type="patient",
        resource_id=str(clinical_data.patient_id),
        request_id=response.headers["x-request-id"],
    )
    assert rows
    assert rows[0].outcome == "success"
    assert rows[0].resource_id == str(clinical_data.patient_id)


@pytest.mark.postgres
async def test_lab_trends_exclude_deleted_and_text_only_results(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await client.get(
        f"/api/v1/patients/{clinical_data.patient_id}/labs/trends"
    )
    assert response.status_code == 200
    body = response.json()
    codes = {series["test_code"] for series in body["series"]}
    assert "TSH" not in codes
    assert "HGB" not in codes
    for series in body["series"]:
        assert series["points"], series["test_code"]
        assert all(point["flag"] in {"high", "low", "normal", "unclassified"} for point in series["points"])
