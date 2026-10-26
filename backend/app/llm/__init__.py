"""LLM provider abstraction and bounded assistant orchestration (optional feature).

Modules:
- `app.llm.provider`: the `LLMProvider` protocol, `OpenCodeProvider`
  (OpenAI-compatible, key from LLM_API_KEY env only), `NullProvider` (LLM
  disabled/unconfigured -> the app stays fully functional), and the single
  `build_provider` factory (swap providers there).
- `app.llm.loop`: the server-side tool loop (allowlist, bounds, safe errors).
- `app.llm.prompt`: system prompt and data-vs-question message labelling.
- `app.llm.limiter`: per-user request rate limiting for POST /api/v1/ai/ask.

No API key ever reaches the browser, logs, error messages, or audit rows.
"""
from __future__ import annotations

from app.llm.loop import AssistantOutcome, ToolLoopAbort, run_assistant
from app.llm.provider import (
    LLMError,
    LLMProvider,
    LLMRateLimited,
    LLMRejected,
    LLMResult,
    LLMTimeout,
    LLMUnavailable,
    NullProvider,
    OpenCodeProvider,
    ToolCallRequest,
    build_provider,
)

__all__ = [
    "AssistantOutcome",
    "LLMError",
    "LLMProvider",
    "LLMRateLimited",
    "LLMRejected",
    "LLMResult",
    "LLMTimeout",
    "LLMUnavailable",
    "NullProvider",
    "OpenCodeProvider",
    "ToolCallRequest",
    "ToolLoopAbort",
    "build_provider",
    "run_assistant",
]
