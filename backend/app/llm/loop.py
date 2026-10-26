"""Server-side tool loop with a strict allowlist and hard bounds.

The model may only *suggest* tool calls; this loop is the gate:

* only the four registered MCP tools can be reached (server-side allowlist in
  app/mcp/server.py); anything else is rejected and audited (`mcp.<name>`,
  outcome `error`) and the model is told the tool is unavailable,
* every executed tool call re-authorizes against the verified session identity
  (authorization check #2, docs/ARCHITECTURE.md) - prompt text or model output
  can never change who the caller is or what they may read,
* bounds are enforced per question: at most `LLM_MAX_TOOL_CALLS` tool-call
  rounds and tool calls, per-tool-result size caps (app/mcp/limits.py),
  `LLM_MAX_ANSWER_TOKENS` completion cap, and a total wall-clock budget of
  `LLM_REQUEST_TIMEOUT_SECONDS` plus a small margin (each model call receives
  at most the remaining budget as its timeout),
* overrunning any bound raises `ToolLoopAbort`, which maps to a controlled
  503 with a safe message - never a stack trace.

The prompt (app/llm/prompt.py) marks the user question as untrusted input and
all record content as DATA, not instructions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Callable

from app.config import Settings
from app.llm.prompt import SYSTEM_PROMPT, assistant_tool_call_message, tool_result_message, user_message
from app.llm.provider import LLMError, LLMProvider, LLMUnavailable
from app.mcp.context import ToolContext
from app.mcp.server import MCPServer

TOTAL_BUDGET_MARGIN_SECONDS = 5.0
MAX_ANSWER_TOKENS = 1024

BUDGET_EXCEEDED_MESSAGE = (
    "The AI request exceeded its processing time budget. Please try a narrower question."
)
TOO_COMPLEX_MESSAGE = (
    "The question required more tool activity than the allowed limit. Please try a narrower question."
)
EMPTY_ANSWER_MESSAGE = "The AI service returned an empty answer. Please try again."


class ToolLoopAbort(LLMError):
    """A server-side bound (rounds, tool calls, or wall clock) was exceeded."""


@dataclass(frozen=True)
class AssistantOutcome:
    """Final answer plus the provenance the API response needs."""

    answer: str
    model: str
    used_tools: list[str] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)


async def run_assistant(
    *,
    provider: LLMProvider,
    mcp: MCPServer,
    question: str,
    patient_id: str,
    tool_context: ToolContext,
    settings: Settings,
    clock: Callable[[], float] = monotonic,
) -> AssistantOutcome:
    """Run the bounded model/tool loop for one question. See module docstring."""
    max_rounds = max(1, int(settings.llm_max_tool_calls))
    max_answer_tokens = MAX_ANSWER_TOKENS
    per_call_timeout = float(settings.llm_request_timeout_seconds)
    deadline = clock() + per_call_timeout + TOTAL_BUDGET_MARGIN_SECONDS

    schemas = mcp.tool_schemas()
    allowlist = set(mcp.tool_names())
    messages: list[dict[str, Any]] = [user_message(question, patient_id=patient_id)]
    used_tools: list[str] = []
    citations: list[dict[str, str]] = []
    tool_calls_made = 0
    rounds = 0

    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ToolLoopAbort(BUDGET_EXCEEDED_MESSAGE)
        result = await provider.complete(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=schemas,
            max_tokens=max_answer_tokens,
            timeout=min(per_call_timeout, remaining),
        )

        if not result.tool_calls:
            answer = result.text.strip()
            if not answer:
                raise LLMUnavailable(EMPTY_ANSWER_MESSAGE)
            return AssistantOutcome(
                answer=answer,
                model=result.model or provider.model_name,
                used_tools=used_tools,
                citations=citations,
            )

        rounds += 1
        if rounds > max_rounds:
            raise ToolLoopAbort(TOO_COMPLEX_MESSAGE)
        messages.append(assistant_tool_call_message(result.text, result.tool_calls))

        for call in result.tool_calls:
            tool_calls_made += 1
            if tool_calls_made > max_rounds:
                raise ToolLoopAbort(TOO_COMPLEX_MESSAGE)
            outcome = await mcp.call_tool(call.name, call.arguments, tool_context)
            messages.append(tool_result_message(call.id, outcome.model_payload))
            if outcome.tool_name in allowlist:
                if outcome.tool_name not in used_tools:
                    used_tools.append(outcome.tool_name)
                for citation in outcome.citations:
                    if citation not in citations:
                        citations.append(citation)
