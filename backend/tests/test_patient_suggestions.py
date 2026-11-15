"""Deterministic transcript patient-suggestion tests."""
from __future__ import annotations

import uuid

import pytest

from app.services.patient_suggestions import _normalize
from conftest import audit_rows, csrf_headers

pytestmark = pytest.mark.postgres

SUGGEST_URL = "/api/v1/appointments/patient-suggestions"


async def _create_patient(client, *, given_name: str, family_name: str) -> dict:
    response = await client.post(
        "/api/v1/patients",
        json={
            "given_name": given_name,
            "family_name": family_name,
            "sex": "female",
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _spaced_mrn(mrn: str) -> str:
    digits = mrn.split("-", 1)[1]
    return "M-R-N-" + "-".join(digits)


async def test_transcript_suggestions_correct_name_and_spaced_mrn(
    auth_client, make_user
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    # The shared test database persists across runs. A unique Cyrillic suffix
    # keeps this assertion on this run's patient rather than older duplicates.
    alphabet = "абвгдежзиклмнопрстуфхцчшщюя"
    suffix = "".join(
        alphabet[int(char, 16) % len(alphabet)]
        for char in uuid.uuid4().hex[:6]
    )
    family_name = f"Ковачева{suffix}"
    display_name = f"Златомира {family_name}"
    latin_family = _normalize(family_name).capitalize()
    latin_candidate = f"Zlatomira {latin_family}h"
    patient = await _create_patient(
        client, given_name="Златомира", family_name=family_name
    )
    spaced_mrn = _spaced_mrn(patient["mrn"])
    text = (
        f"Book 30 minute follow up, visit for {latin_candidate}. "
        f"{spaced_mrn}. Next Monday at 10 o'clock. "
        "Reason, Blood Pressure Review."
    )

    response = await client.post(
        SUGGEST_URL, json={"text": text}, headers=csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "expected a patient suggestion"
    suggestion = items[0]
    assert suggestion["display_name"] == display_name
    assert suggestion["mrn"] == patient["mrn"]
    assert suggestion["matched_name_text"] == latin_candidate
    assert suggestion["matched_mrn_text"] == spaced_mrn
    assert "id" not in suggestion

    # Exact canonical name + canonical MRN needs no correction.
    exact = await client.post(
        SUGGEST_URL,
        json={"text": f"Book a follow-up for {display_name}, {patient['mrn']}."},
        headers=csrf_headers(client),
    )
    assert exact.status_code == 200
    assert all(
        item["mrn"] != patient["mrn"] for item in exact.json()["items"]
    )

    # Name-only typo still resolves without an MRN.
    name_only = await client.post(
        SUGGEST_URL,
        json={"text": f"Book a follow-up for {latin_candidate} next Monday."},
        headers=csrf_headers(client),
    )
    assert name_only.status_code == 200
    name_items = name_only.json()["items"]
    assert any(
        item["mrn"] == patient["mrn"]
        and item["matched_name_text"] == latin_candidate
        for item in name_items
    )

    # A scheduling request with no patient reference gets no invented match.
    unrelated = await client.post(
        SUGGEST_URL,
        json={"text": "Book a 30 minute follow-up next Monday at 10 o'clock."},
        headers=csrf_headers(client),
    )
    assert unrelated.status_code == 200
    assert unrelated.json()["items"] == []

    # Audit is metadata-only: no transcript, name or MRN.
    rows = await audit_rows(action="patient.suggest")
    assert rows
    blob = " ".join(
        str(value)
        for row in rows
        for value in (
            row.action,
            row.resource_type,
            row.resource_id,
            row.outcome,
            row.request_id,
            row.ip,
        )
        if value is not None
    )
    assert "Zlatomira" not in blob
    assert patient["mrn"] not in blob
    assert "Blood Pressure" not in blob
