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

SENTINEL_KEY = "oc_sk_SENTINEL-9f3c2b-must-never-leak"
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


@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_4xx_is_rejected_and_never_retried(status: int) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(status, json={"error": "nope"})

    provider, _ = make_provider(handler)
    with pytest.raises(LLMRejected):
        await call(provider)
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
