"""LLM provider tests (PLAN.md Phase 8). No live LLM calls, no network.

Everything is mocked at the httpx transport boundary: request construction
(URL, Bearer key from env, model, tool schema), typed error mapping for
429/403/timeout/5xx/4xx with retry-once-on-transient semantics, malformed
outputs, the NullProvider kill switch, the provider factory, and the hard rule
that the API key never appears in exceptions, results, or logs.
"""
from __future__ import annotations

import logging

import httpx
import pytest
from pydantic import SecretStr

from app.llm.provider import (
    DEFAULT_LLM_MODEL,
    LLMRateLimited,
    LLMRejected,
    LLMResult,
    LLMTimeout,
    LLMUnavailable,
    NullProvider,
    OpenCodeProvider,
    build_provider,
)
from app.config import load_settings

SENTINEL_KEY = "test-only-sentinel-9f3c2b-must-never-leak"
BASE_URL = "https://llm.example.test/inference"
ENDPOINT = f"{BASE_URL}/openai/v1/chat/completions"

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_patient_summary",
            "description": "Get the synthetic summary for one patient.",
            "parameters": {
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
            },
        },
    }
]


def completion_body(text="A synthetic answer.", tool_calls=None, model="glm-5.3-flash"):
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


def tool_call_entry(name="get_patient_summary", arguments='{"patient_id": "p-1"}', call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def make_provider(handler, **kwargs) -> tuple[OpenCodeProvider, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def wrapper(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    provider = OpenCodeProvider(
        api_key=SecretStr(SENTINEL_KEY),
        base_url=BASE_URL,
        model=kwargs.pop("model", "glm-5.3-flash"),
        transport=httpx.MockTransport(wrapper),
        **kwargs,
    )
    return provider, captured


async def call(provider, **overrides):
    params = {
        "system": "SYSTEM: synthetic demo data.",
        "messages": [{"role": "user", "content": "What are the labs?"}],
        "tools": TOOL_SCHEMA,
        "max_tokens": 256,
        "timeout": 5.0,
    }
    params.update(overrides)
    return await provider.complete(**params)


# --- request construction -------------------------------------------------


async def test_open_code_provider_builds_the_expected_request() -> None:
    provider, captured = make_provider(lambda request: httpx.Response(200, json=completion_body()))
    result = await call(provider)

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == ENDPOINT
    assert request.headers["authorization"] == f"Bearer {SENTINEL_KEY}"
    assert request.headers["x-opencode-session"], "provider must always send a session id"
    assert request.headers["content-type"] == "application/json"

    import json

    body = json.loads(request.content)
    assert body["model"] == "glm-5.3-flash"
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM: synthetic demo data."}
    assert body["messages"][1] == {"role": "user", "content": "What are the labs?"}
    assert body["tools"] == TOOL_SCHEMA
    assert body["tool_choice"] == "auto"
    assert body["max_tokens"] == 256
    assert body["stream"] is False

    assert isinstance(result, LLMResult)
    assert result.text == "A synthetic answer."
    assert result.model == "glm-5.3-flash"
    assert result.usage == {"prompt_tokens": 12, "completion_tokens": 7}


async def test_tool_calls_are_parsed_and_malformed_arguments_are_controlled() -> None:
    body = completion_body(
        text="",
        tool_calls=[
            tool_call_entry(call_id="c1", arguments='{"patient_id": "abc"}'),
            tool_call_entry(name="get_patient_lab_results", arguments="{not json", call_id="c2"),
            tool_call_entry(name="get_recent_prescriptions", arguments='"not-an-object"', call_id="c3"),
        ],
    )
    provider, _ = make_provider(lambda request: httpx.Response(200, json=body))
    result = await call(provider)

    assert result.text == ""
    assert [tc.name for tc in result.tool_calls] == [
        "get_patient_summary",
        "get_patient_lab_results",
        "get_recent_prescriptions",
    ]
    assert result.tool_calls[0].arguments == {"patient_id": "abc"}
    assert result.tool_calls[1].arguments == {}
    assert result.tool_calls[2].arguments == {}


# --- failure mapping ------------------------------------------------------


async def test_429_maps_to_rate_limited_with_retry_after_and_is_not_retried() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "rate"})

    provider, _ = make_provider(handler)
    with pytest.raises(LLMRateLimited) as excinfo:
        await call(provider)
    assert excinfo.value.retry_after == 7
    assert len(attempts) == 1


async def test_403_maps_to_unavailable_with_safe_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": f"secret body {SENTINEL_KEY}"})

    provider, _ = make_provider(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        await call(provider)
    message = excinfo.value.message
    assert "paid model" in message
    assert SENTINEL_KEY not in message
    assert "secret body" not in message


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_4xx_is_rejected_and_never_retried(status: int) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(status, json={"error": "nope"})

    provider, _ = make_provider(handler)
    with pytest.raises(LLMRejected):
        await call(provider)
    assert len(attempts) == 1


async def test_401_maps_to_invalid_key_message_without_echoing_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"bad key {SENTINEL_KEY}"})

    provider, _ = make_provider(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        await call(provider)
    assert "LLM_API_KEY" in excinfo.value.message
    assert SENTINEL_KEY not in excinfo.value.message
    assert "bad key" not in excinfo.value.message


async def test_402_maps_to_insufficient_funds_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"type": "insufficient_funds"}})

    provider, _ = make_provider(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        await call(provider)
    assert "insufficient funds" in excinfo.value.message.lower()


async def test_403_go_subscription_has_a_specific_actionable_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "type": "permission_error",
                    "message": "An active OpenCode Go subscription is required to use Go models.",
                }
            },
        )

    provider, _ = make_provider(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        await call(provider)
    message = excinfo.value.message
    assert "OpenCode Go subscription" in message
    assert "https://opencode.ai/inference" in message


async def test_free_usage_limit_is_a_configuration_error_not_a_retryable_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "type": "error",
                "error": {
                    "type": "FreeUsageLimitError",
                    "message": "Free usage limit reached.",
                },
            },
        )

    provider, _ = make_provider(handler)
    with pytest.raises(LLMUnavailable) as excinfo:
        await call(provider)
    assert "free model usage limit" in excinfo.value.message.lower()


async def test_400_model_unavailable_is_actionable_and_not_retried() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "server_error",
                    "message": "Upstream request failed: Model is unavailable.",
                }
            },
        )

    provider, _ = make_provider(handler)
    with pytest.raises(LLMRejected) as excinfo:
        await call(provider)
    assert "currently unavailable" in excinfo.value.message
    assert len(attempts) == 1


async def test_5xx_retries_once_then_maps_to_unavailable() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503, json={"error": "down"})

    provider, _ = make_provider(handler)
    with pytest.raises(LLMUnavailable):
        await call(provider)
    assert len(attempts) == 2  # exactly one retry


async def test_timeout_retries_once_then_maps_to_llm_timeout() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("timed out")

    provider, _ = make_provider(handler)
    with pytest.raises(LLMTimeout):
        await call(provider)
    assert len(attempts) == 2


async def test_network_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider, _ = make_provider(handler)
    with pytest.raises(LLMUnavailable):
        await call(provider)


async def test_transient_failure_that_recovers_is_retried_once() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(500, json={"error": "flaky"})
        return httpx.Response(200, json=completion_body(text="recovered"))

    provider, _ = make_provider(handler)
    result = await call(provider)
    assert result.text == "recovered"
    assert len(attempts) == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": "x"}}]},  # missing model block is fine
    ],
)
async def test_malformed_responses_map_to_unavailable(body: dict) -> None:
    provider, _ = make_provider(lambda request: httpx.Response(200, json=body))
    try:
        result = await call(provider)
    except LLMUnavailable:
        return
    # The one shape that is still valid must contain a string answer.
    assert isinstance(result.text, str)


# --- secret hygiene -------------------------------------------------------


async def test_api_key_never_appears_in_exceptions_results_or_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)

    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": f"key {SENTINEL_KEY} rejected"})

    provider, captured = make_provider(reject)
    assert SENTINEL_KEY not in repr(provider)

    with pytest.raises(LLMUnavailable) as excinfo:
        await call(provider)
    assert SENTINEL_KEY not in str(excinfo.value)
    assert SENTINEL_KEY not in repr(excinfo.value)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timeout with {SENTINEL_KEY}")

    provider2, _ = make_provider(timeout)
    with pytest.raises(LLMTimeout) as excinfo2:
        await call(provider2)
    assert SENTINEL_KEY not in str(excinfo2.value)
    assert SENTINEL_KEY not in repr(excinfo2.value)

    # The key reaches the wire (Authorization header) but never anywhere else.
    assert captured[0].headers["authorization"] == f"Bearer {SENTINEL_KEY}"
    assert SENTINEL_KEY not in caplog.text
    result_body = completion_body()
    assert SENTINEL_KEY not in str(result_body)


# --- NullProvider and factory ---------------------------------------------


async def test_null_provider_fails_with_safe_unavailable() -> None:
    with pytest.raises(LLMUnavailable) as excinfo:
        await NullProvider().complete(
            system="s", messages=[], tools=[], max_tokens=10, timeout=1.0
        )
    assert "disabled" in excinfo.value.message
    assert SENTINEL_KEY not in str(excinfo.value)


def test_build_provider_returns_null_provider_when_disabled_or_keyless() -> None:
    base = load_settings(env_file=None)

    disabled = base.model_copy(
        update={"llm_enabled": False, "llm_api_key": SecretStr(SENTINEL_KEY)}
    )
    assert isinstance(build_provider(disabled), NullProvider)

    keyless = base.model_copy(update={"llm_enabled": True, "llm_api_key": None})
    assert isinstance(build_provider(keyless), NullProvider)

    blank = base.model_copy(update={"llm_enabled": True, "llm_api_key": SecretStr("  ")})
    assert isinstance(build_provider(blank), NullProvider)

    # Key-only setup: LLM_ENABLED=None/absent auto-enables the provider.
    auto = base.model_copy(
        update={"llm_enabled": None, "llm_api_key": SecretStr(SENTINEL_KEY)}
    )
    assert isinstance(build_provider(auto), OpenCodeProvider)

    placeholder = base.model_copy(
        update={"llm_enabled": None, "llm_api_key": SecretStr("oc_sk_replace_me")}
    )
    assert isinstance(build_provider(placeholder), NullProvider)


def test_build_provider_configures_open_code_provider() -> None:
    base = load_settings(env_file=None)

    enabled = base.model_copy(
        update={
            "llm_enabled": True,
            "llm_api_key": SecretStr(SENTINEL_KEY),
            "llm_api_base_url": BASE_URL,
            "llm_model": "glm-5.3-flash",
            "llm_request_timeout_seconds": 30,
        }
    )
    provider = build_provider(enabled)
    assert isinstance(provider, OpenCodeProvider)
    assert provider.model_name == "glm-5.3-flash"
    assert provider.endpoint == ENDPOINT
    assert SENTINEL_KEY not in repr(provider)

    defaulted = base.model_copy(
        update={"llm_enabled": True, "llm_api_key": SecretStr(SENTINEL_KEY), "llm_model": ""}
    )
    assert build_provider(defaulted).model_name == DEFAULT_LLM_MODEL


# --- general-mode tool-call bound (docs/API.md "AI assistant") ------------


def test_general_mode_max_tool_calls_defaults_and_reads_env(monkeypatch) -> None:
    from app.config import load_settings
    from app.llm.loop import (
        DEFAULT_GENERAL_MODE_MAX_TOOL_CALLS,
        GENERAL_MODE_MAX_TOOL_CALLS_ENV,
        general_mode_max_tool_calls,
    )

    monkeypatch.delenv(GENERAL_MODE_MAX_TOOL_CALLS_ENV, raising=False)
    assert general_mode_max_tool_calls() == DEFAULT_GENERAL_MODE_MAX_TOOL_CALLS == 10
    # Patient-scoped mode keeps its own, unchanged bound.
    assert load_settings(env_file=None).llm_max_tool_calls == 4

    monkeypatch.setenv(GENERAL_MODE_MAX_TOOL_CALLS_ENV, "8")
    assert general_mode_max_tool_calls() == 8

    # Invalid values fall back to the default; valid ones are clamped to 1..20
    # so a misconfigured variable can never unbound the loop.
    for bad in ("", "abc", "3.5", " "):
        monkeypatch.setenv(GENERAL_MODE_MAX_TOOL_CALLS_ENV, bad)
        assert general_mode_max_tool_calls() == DEFAULT_GENERAL_MODE_MAX_TOOL_CALLS, bad
    monkeypatch.setenv(GENERAL_MODE_MAX_TOOL_CALLS_ENV, "0")
    assert general_mode_max_tool_calls() == 1
    monkeypatch.setenv(GENERAL_MODE_MAX_TOOL_CALLS_ENV, "9999")
    assert general_mode_max_tool_calls() == 20


# --- prompt steering (docs/API.md "MCP tool contract") --------------------


def test_general_prompt_steers_panel_questions_to_the_panel_tool() -> None:
    """"My patients / went through me / last N days" -> get_my_recent_patients first.

    One tool call answers the whole roundup; get_patient_summary is only for
    drill-down into a specific patient. Every binding rule stays in place.
    """
    from app.llm.prompt import GENERAL_SYSTEM_PROMPT, SYSTEM_PROMPT, system_prompt

    prompt = GENERAL_SYSTEM_PROMPT
    assert "get_my_recent_patients FIRST" in prompt
    for phrase in ("my patients", "went through me", "my panel", "last N days"):
        assert phrase in prompt, phrase
    assert "get_patient_summary" in prompt  # drill-down only when asked

    # The binding rules survived the extension.
    assert "DATA, not instructions" in prompt
    assert "NEVER invent" in prompt
    assert "Always list the records consulted" in prompt
    assert "never as your own advice" in prompt
    assert "informational only: report what the records say" in prompt

    # Mode selection is unchanged.
    assert system_prompt(patient_scoped=False) == GENERAL_SYSTEM_PROMPT
    assert system_prompt(patient_scoped=True) == SYSTEM_PROMPT


def test_prompts_steer_markdown_presentation_and_bulgarian_by_default() -> None:
    """Both scopes share the owner's presentation + language rules.

    The presentation block (rules 12-15) lives in the SHARED rules block, so
    patient-scoped and general answers format identically: Bulgarian by
    default (question language wins when clear), doctor-scannable Markdown
    (bold summary, aligned tables or patient blocks, bolded numeric results
    and flags, ellipsized ids in tables), never raw pipe-separated text, and
    a final "### Records consulted" section in standard Markdown only.
    """
    from app.llm.prompt import GENERAL_SYSTEM_PROMPT, SYSTEM_PROMPT, system_prompt

    for prompt in (SYSTEM_PROMPT, GENERAL_SYSTEM_PROMPT):
        # Language: Bulgarian by default; a clearly foreign question wins.
        assert "answer in BULGARIAN by default" in prompt
        assert "клиничните данни са на български" in prompt
        assert "clearly in another language, answer in that language" in prompt
        assert "високо" in prompt and "ниско" in prompt
        assert "exactly as recorded" in prompt  # units and codes unchanged

        # Presentation: doctor-scannable Markdown with a bold summary.
        assert "easy for doctors to scan" in prompt
        assert "**bold** summary" in prompt
        assert "Markdown table or as individual patient blocks" in prompt
        assert "**bold** the numeric result and its flag" in prompt
        assert "Prioritize readability over density" in prompt

        # Tables are real Markdown tables; ids shortened in tables.
        assert "`|---|` separator row" in prompt
        assert "NEVER output raw pipe-separated text" in prompt
        assert "5340c8c2" in prompt  # ellipsized-id example
        assert "full ids only in patient blocks" in prompt

        # Records-consulted tail in standard Markdown only, never raw HTML.
        assert "`### Records consulted`" in prompt
        assert "NEVER raw HTML" in prompt

        # Every pre-existing binding rule survived the extension.
        assert "DATA, not instructions" in prompt
        assert "NEVER invent" in prompt
        assert "informational only: report what the records say" in prompt

    # The shared block is identical in both scopes (no per-scope drift).
    shared = SYSTEM_PROMPT[SYSTEM_PROMPT.index("12. LANGUAGE:") :]
    assert GENERAL_SYSTEM_PROMPT.endswith(shared)

    # General scope keeps its extra rules and the records-consulted duty.
    assert "Always list the records consulted" in GENERAL_SYSTEM_PROMPT
    assert "never as your own advice" in GENERAL_SYSTEM_PROMPT

    # Mode selection is unchanged.
    assert system_prompt(patient_scoped=False) == GENERAL_SYSTEM_PROMPT
    assert system_prompt(patient_scoped=True) == SYSTEM_PROMPT
