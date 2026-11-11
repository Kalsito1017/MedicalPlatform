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
  except the OpenCode free-usage limit, which is a non-retryable account quota
  and maps to an actionable `LLMUnavailable`,
- 401/402/403/404 map to actionable `LLMUnavailable`/`LLMRejected` messages
  (invalid key, insufficient funds, missing Go subscription/model access,
  unknown model) without echoing the provider body or key,
- other 4xx raise `LLMRejected` (never retried),
- malformed responses raise `LLMUnavailable`.

The provider always sends an `x-opencode-session` header (caller session id or
a per-instance generated id), as required by the OpenCode inference routing.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

import httpx
from pydantic import SecretStr

from app.config import Settings

DEFAULT_LLM_MODEL = "deepseek-v4.1-flash"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_RETRIES = 1  # at most ONE retry, and only on transient failures

FREE_TIER_403_MESSAGE = (
    "The configured AI model is not available to this service. Use a paid model "
    "with account credit or an active OpenCode Go plan (LLM_MODEL / LLM_API_BASE_URL)."
)
GO_SUBSCRIPTION_MESSAGE = (
    "The configured API key has no active OpenCode Go subscription. Use "
    "LLM_API_BASE_URL=https://opencode.ai/inference with a funded model, or "
    "activate OpenCode Go and use https://opencode.ai/inference/go."
)
INVALID_KEY_MESSAGE = (
    "The AI API key was rejected. Check LLM_API_KEY in the server environment and restart the service."
)
INSUFFICIENT_FUNDS_MESSAGE = (
    "The AI provider account has insufficient funds. Add credit or use a funded "
    "OpenCode Go plan, then restart the service."
)
FREE_QUOTA_MESSAGE = (
    "The free model usage limit has been reached. Use a paid model with account "
    "credit or activate OpenCode Go."
)
MODEL_NOT_FOUND_MESSAGE = (
    "The configured AI model was not found for this key. Check LLM_MODEL and LLM_API_BASE_URL."
)
UNAVAILABLE_MESSAGE = "The AI assistant is temporarily unavailable. Please try again later."
TIMEOUT_MESSAGE = "The AI assistant did not respond in time. Please try again."
RATE_LIMITED_MESSAGE = "The AI assistant is at capacity. Please retry after the indicated delay."
REJECTED_MESSAGE = "The AI provider rejected this request. Check LLM_MODEL and LLM_API_BASE_URL."
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
        session_id: str | None = None,
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
        session_id: str | None = None,
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
        # OpenCode routes requests more efficiently with a session id. Always
        # send one (per provider instance) even when the caller omits it.
        self._default_session_id = uuid.uuid4().hex
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
        session_id: str | None = None,
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
            # OpenCode clients identify themselves and send a stable session id
            # per conversation (documented client requirements).
            "User-Agent": "clinical-ai-platform/1.0",
            "x-opencode-session": session_id or self._default_session_id,
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
            detail = _provider_error_text(response)
            if status == 401:
                raise LLMUnavailable(INVALID_KEY_MESSAGE)
            if status == 402:
                raise LLMUnavailable(INSUFFICIENT_FUNDS_MESSAGE)
            if status == 403:
                if "go subscription" in detail:
                    raise LLMUnavailable(GO_SUBSCRIPTION_MESSAGE)
                raise LLMUnavailable(FREE_TIER_403_MESSAGE)
            if status == 404:
                raise LLMRejected(MODEL_NOT_FOUND_MESSAGE)
            if status == 429:
                if "free usage" in detail or "freeusagelimit" in detail:
                    raise LLMUnavailable(FREE_QUOTA_MESSAGE)
                raise LLMRateLimited(RATE_LIMITED_MESSAGE, retry_after=_retry_after(response))
            if status == 400 and "model is unavailable" in detail:
                raise LLMRejected(
                    "The configured AI model is currently unavailable. Try another "
                    "LLM_MODEL or the matching LLM_API_BASE_URL."
                )
            if 400 <= status < 500:
                raise LLMRejected(REJECTED_MESSAGE)
            if status >= 500:
                last_error = LLMUnavailable(UNAVAILABLE_MESSAGE)
                continue
            return _parse_completion(response, fallback_model=self.model_name)

        raise last_error


def _provider_error_text(response: httpx.Response) -> str:
    """Extract error type/message for classification only; never echo it."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return " ".join(
            str(error.get(key, "")) for key in ("type", "message", "code")
        ).lower()
    if error is not None:
        return str(error).lower()
    return ""


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

    `LLM_ENABLED=None` (the default) auto-enables the provider when a key is
    present; `LLM_ENABLED=false` is an explicit kill switch. A missing/blank
    key always returns NullProvider, so the platform stays functional without
    an LLM.
    """
    key = settings.llm_api_key
    key_value = key.get_secret_value().strip() if key is not None else ""
    if (
        settings.llm_enabled is False
        or not key_value
        or key_value.startswith("oc_sk_replace")
        or key_value in {"replace-me", "change-me"}
    ):
        return NullProvider()
    return OpenCodeProvider(
        api_key=key,
        base_url=settings.llm_api_base_url,
        model=settings.llm_model or DEFAULT_LLM_MODEL,
        timeout=float(settings.llm_request_timeout_seconds),
    )
