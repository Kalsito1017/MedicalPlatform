"""Transcription correction analysis: read-only, validated, doctor-controlled."""
from __future__ import annotations

import json

import pytest

from app.llm.provider import LLMResult, LLMUnavailable
from app.services import transcription_corrections
from conftest import audit_rows, csrf_headers

pytestmark = pytest.mark.postgres

URL = "/api/v1/appointments/transcription-corrections"


class FakeProvider:
    model_name = "fake-correction-model"

    def __init__(self, payload=None, *, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools, max_tokens, timeout, session_id=None):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "session_id": session_id,
            }
        )
        if self.error is not None:
            raise self.error
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return LLMResult(text=text, model=self.model_name)


@pytest.fixture(autouse=True)
def reset_correction_limiter():
    transcription_corrections.correction_rate_limiter.reset()
    yield
    transcription_corrections.correction_rate_limiter.reset()


async def _post(client, text: str, language: str = "bg"):
    return await client.post(
        URL,
        json={"text": text, "language": language},
        headers=csrf_headers(client),
    )


def test_appointment_type_fuzzy_helper_is_conservative() -> None:
    suggestion = transcription_corrections.suggest_appointment_type(
        "Book a Consultaton tomorrow at 10:00.",
        ["Consultation", "General Consultation"],
    )
    assert suggestion is not None
    assert suggestion.original_text == "Consultaton"
    assert suggestion.suggested_text in {"Consultation", "General Consultation"}
    assert suggestion.category == "appointment_type"

    assert (
        transcription_corrections.suggest_appointment_type(
            "Book a Consultation tomorrow at 10:00.",
            ["Consultation"],
        )
        is None
    )


async def test_valid_suggestions_are_bounded_and_patient_data_is_redacted(
    auth_client, make_user, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    provider = FakeProvider(
        {
            "suggestions": [
                {
                    "original_text": "next Tuesday",
                    "suggested_text": "next Monday",
                    "reason": "Possible weekday mismatch",
                    "confidence": "medium",
                    "category": "weekday",
                },
                {
                    "original_text": "follow-up",
                    "suggested_text": "Follow-up",
                    "reason": "capitalization only",
                    "confidence": "low",
                    "category": "appointment_type",
                },
                {
                    "original_text": "not in the transcript",
                    "suggested_text": "invented",
                    "reason": "bad",
                    "confidence": "low",
                    "category": "other",
                },
                {
                    "original_text": "10:00",
                    "suggested_text": "<b>11:00</b>",
                    "reason": "bad HTML",
                    "confidence": "low",
                    "category": "time",
                },
                {
                    "original_text": clinical_data.patient_name,
                    "suggested_text": "Someone Else",
                    "reason": "patient names are handled separately",
                    "confidence": "low",
                    "category": "patient_name",
                },
                {
                    "original_text": "[PATIENT_NAME]",
                    "suggested_text": "Someone Else",
                    "reason": "placeholder",
                    "confidence": "low",
                    "category": "other",
                },
            ]
        }
    )
    monkeypatch.setattr(
        transcription_corrections, "build_provider", lambda settings: provider
    )
    text = (
        f"Book a follow-up for {clinical_data.patient_name}, "
        f"{clinical_data.patient_mrn}, next Tuesday at 10:00."
    )

    response = await _post(client, text)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["language"] == "bg"
    assert payload["analysis_available"] is True
    assert payload["model"] == "fake-correction-model"
    suggestions = payload["suggestions"]
    assert any(
        item["original_text"] == "next Tuesday"
        and item["suggested_text"] == "next Monday"
        and item["category"] == "weekday"
        for item in suggestions
    )
    assert all(item["original_text"] in text for item in suggestions)
    assert all("<b>" not in item["suggested_text"] for item in suggestions)
    assert all(item["category"] != "patient_name" for item in suggestions)
    assert not any(
        item["original_text"] == "follow-up" for item in suggestions
    ), "case-only cosmetic suggestions must be rejected"

    assert provider.calls, "correction provider was not called"
    prompt = provider.calls[0]["system"]
    messages = provider.calls[0]["messages"]
    sent_text = "\n".join(str(message.get("content", "")) for message in messages)
    assert clinical_data.patient_name not in sent_text
    assert clinical_data.patient_mrn not in sent_text
    assert "[PATIENT_NAME]" in sent_text
    assert "[MRN]" in sent_text
    assert "untrusted" in prompt.lower()
    assert provider.calls[0]["tools"] == []

    rows = await audit_rows(action="appointment.correction", outcome="success")
    assert rows, "correction analysis must be audited without transcript content"


async def test_malformed_model_output_degrades_without_blocking(
    auth_client, make_user, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    provider = FakeProvider("this is not json")
    monkeypatch.setattr(
        transcription_corrections, "build_provider", lambda settings: provider
    )

    response = await _post(
        client,
        f"Book a follow-up for {clinical_data.patient_name} tomorrow at 10:00.",
        "en",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["language"] == "en"
    assert payload["analysis_available"] is False
    assert payload["suggestions"] == []


async def test_provider_unavailable_keeps_the_feature_optional(
    auth_client, make_user, clinical_data, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    provider = FakeProvider(error=LLMUnavailable("provider down"))
    monkeypatch.setattr(
        transcription_corrections, "build_provider", lambda settings: provider
    )

    response = await _post(
        client,
        f"Book a follow-up for {clinical_data.patient_name} tomorrow at 10:00.",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["analysis_available"] is False
    assert payload["suggestions"] == []
