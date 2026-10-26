"""LLM provider abstraction (docs/ARCHITECTURE.md "LLM provider coupling").

The application never talks to a model SDK directly outside this module;
clinical services and the MCP layer never import a provider. Swapping providers
means implementing `LLMProvider` and returning it from `build_provider` (the
single factory) - nothing else changes.

`OpenCodeProvider` speaks the OpenAI-compatible chat-completions protocol to
`{LLM_API_BASE_URL}/openai/v1/chat/completions` (default
https://opencode.ai/inference), with the API key read from the `LLM_API_KEY`
environment variable only. The key is held as a SecretStr and is never logged,
never returned, never placed in an exception message, and never sent to the
browser.

`NullProvider` is used when `LLM_ENABLED=false` or no key is configured: the
platform is fully functional without the LLM and `POST /api/v1/ai/ask` answers
with 503 `dependency_unavailable`.

Failure handling (all errors carry safe, client-presentable messages - never
stack traces, response bodies, headers, or keys):

- transient failures (network error, timeout, 5xx) retry ONCE, then raise
  `LLMTimeout` / `LLMUnavailable`,
- 429 raises `LLMRateLimited` with the provider's Retry-After (never retried),
- 403 raises `LLMUnavailable` (known cause: free-tier models return HTTP 403
  for non-OpenCode clients - a paid model is required),
- other 4xx raise `LLMRejected` (never retried),
- malformed responses raise `LLMUnavailable`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

import httpx
from pydantic import SecretStr

from app.config import Settings

DEFAULT_LLM_MODEL = "glm-5.3-flash"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_RETRIES = 1  # at most ONE retry, and only on transient failures

FREE_TIER_403_MESSAGE = (
    "The configured AI model is not available to this service. "
    "Free-tier models return HTTP 403 for non-OpenCode clients - configure a paid model (LLM_MODEL)."
)
UNAVAILABLE_MESSAGE = "The AI assistant is temporarily unavailable. Please try again later."
TIMEOUT_MESSAGE = "The AI assistant did not respond in time. Please try again."
RATE_LIMITED_MESSAGE = "The AI assistant is at capacity. Please retry after the indicated delay."
REJECTED_MESSAGE = "The AI provider rejected this request."
MALFORMED_MESSAGE = "The AI provider returned an unusable response."


class LLMError(Exception):
    """Base class for provider failures. The message is safe to show clients."""

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


class LLMUnavailable(LLMError):
    """Provider unreachable, misconfigured, or refusing (e.g. free-tier 403)."""


class LLMRateLimited(LLMError):
    """Provider rate limited the request (HTTP 429). `retry_after` in seconds."""


class LLMTimeout(LLMError):
    """Provider did not answer within the timeout budget."""


class LLMRejected(LLMError):
    """Provider rejected the request (non-429 4xx) - retrying will not help."""


@dataclass(frozen=True)
class ToolCallRequest:
    """One model-requested tool call (an untrusted suggestion until executed)."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    """Normalized completion result shared by all providers."""

    text: str
    tool_calls: tuple[ToolCallRequest, ...] = ()
    model: str = ""
    usage: dict[str, int] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """What every provider must offer. Implement this to swap providers."""

    model_name: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
        timeout: float,
    ) -> LLMResult: ...


class NullProvider:
    """Provider used when the optional LLM feature is disabled or unconfigured."""

    model_name = "none"

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
        timeout: float,
    ) -> LLMResult:
        raise LLMUnavailable(
            "The AI assistant is disabled on this server (LLM_ENABLED=false or no LLM_API_KEY). "
            "All other features work normally."
        )


class OpenCodeProvider:
    """OpenAI-compatible chat-completions client (OpenCode Console inference API)."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        base_url: str,
        model: str,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_key = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport
        self._max_retries = max_retries
        self.model_name = model or DEFAULT_LLM_MODEL

    @property
    def endpoint(self) -> str:
        """Chat-completions URL (no secrets)."""
        return f"{self._base_url}/openai/v1/chat/completions"

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
        timeout: float,
    ) -> LLMResult:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": list(tools),
            "tool_choice": "auto",
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

        attempts = self._max_retries + 1
        last_error: LLMError = LLMUnavailable(UNAVAILABLE_MESSAGE)
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=max(timeout, 1.0)
                ) as client:
                    response = await client.post(self.endpoint, json=payload, headers=headers)
            except httpx.TimeoutException:
                last_error = LLMTimeout(TIMEOUT_MESSAGE)
                continue
            except httpx.TransportError:
                last_error = LLMUnavailable(UNAVAILABLE_MESSAGE)
                continue

            status = response.status_code
            if status == 429:
                raise LLMRateLimited(RATE_LIMITED_MESSAGE, retry_after=_retry_after(response))
            if status == 403:
                raise LLMUnavailable(FREE_TIER_403_MESSAGE)
            if 400 <= status < 500:
                raise LLMRejected(REJECTED_MESSAGE)
            if status >= 500:
                last_error = LLMUnavailable(UNAVAILABLE_MESSAGE)
                continue
            return _parse_completion(response, fallback_model=self.model_name)

        raise last_error


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after")
    try:
        value = int(raw) if raw is not None else None
    except ValueError:
        return None
    return max(1, value) if value is not None else None


def _parse_completion(response: httpx.Response, *, fallback_model: str) -> LLMResult:
    try:
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
        text = message.get("content") or ""
        raw_tool_calls = message.get("tool_calls") or []
        model = body.get("model") or fallback_model
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else None
    except (ValueError, KeyError, TypeError, IndexError):
        raise LLMUnavailable(MALFORMED_MESSAGE) from None

    tool_calls: list[ToolCallRequest] = []
    for entry in raw_tool_calls:
        try:
            function = entry["function"]
            name = str(function["name"])
            call_id = str(entry.get("id") or f"call_{len(tool_calls)}")
        except (TypeError, KeyError):
            raise LLMUnavailable(MALFORMED_MESSAGE) from None
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
        except (ValueError, TypeError):
            # Malformed model-produced arguments: hand downstream validation a
            # controlled failure instead of crashing here.
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append(ToolCallRequest(id=call_id, name=name, arguments=arguments))

    return LLMResult(
        text=str(text),
        tool_calls=tuple(tool_calls),
        model=str(model),
        usage={str(k): int(v) for k, v in usage.items() if isinstance(v, (int, float))} if usage else None,
    )


def build_provider(settings: Settings) -> LLMProvider:
    """The single provider factory (swap providers here).

    Returns NullProvider when the optional LLM feature is disabled or no API
    key is configured - the platform must stay fully functional without an LLM.
    """
    key = settings.llm_api_key
    if not settings.llm_enabled or key is None or not key.get_secret_value().strip():
        return NullProvider()
    return OpenCodeProvider(
        api_key=key,
        base_url=settings.llm_api_base_url,
        model=settings.llm_model or DEFAULT_LLM_MODEL,
        timeout=float(settings.llm_request_timeout_seconds),
    )
