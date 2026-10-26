"""Internal MCP server: the tool allowlist and the only tool entry point.

DESIGN DECISION (docs/ARCHITECTURE.md decision log, docs/API.md "MCP tool
contract"): the MCP server is an in-process MODULE of the FastAPI application.
It is never exposed over stdio, HTTP, SSE, or any other transport in this
deployment, and there is no separate MCP service.

SDK approach: this module uses the maintained official `mcp` Python SDK's
typed tool machinery - `mcp.server.fastmcp.tools.base.Tool` (JSON-Schema-typed
tools generated from bounded, annotated function signatures) and
`mcp.server.fastmcp.tools.tool_manager.ToolManager` (`list_tools`/`call_tool`
semantics). It deliberately does NOT spin up a FastMCP *server* object:
FastMCP's request-context machinery exists to serve transport sessions
(stdio/HTTP/SSE), which this deployment never uses. Tools are invoked
programmatically here, and the verified caller context is injected through the
SDK's context-injection channel (`context_kwarg`), which is excluded from the
LLM-facing tool schema - so identity is structurally impossible to supply as a
tool argument.

Hard guarantees enforced here (tested in tests/test_mcp.py):

* exactly six registered tools, all read-only; no `execute_sql`, shell,
  filesystem, or general-purpose database tool exists or may be added,
* the LLM-facing schemas expose typed, bounded arguments only - never
  identity, credentials, SQL, or context,
* unknown tool names are rejected server-side and audited (fixed action
  `mcp.unknown_tool`, outcome `error`, attempted name as resource reference -
  the audit action inventory stays closed and never carries attacker input),
* every call requires a valid server-injected `ToolContext` (verified session
  identity + request-scoped DB session + request id); otherwise it is denied
  and audited,
* authorization is re-executed inside every tool against the same policy and
  access seam as the REST API; tampering with tool arguments cannot escalate
  or bypass it,
* every call is audited exactly once with action `mcp.<tool_name>`, a resource
  reference where meaningful, outcome success|denied|error, and the request id
  - never a clinical payload or prompt,
* results are bounded (`app.mcp.limits`): text fields and whole results are
  truncated with an explicit marker, and each call has a hard timeout,
* errors are controlled and safe: no stack traces, SQL, file paths, or secret
  values ever reach the model or the client.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp.tools.base import Tool
from mcp.server.fastmcp.tools.tool_manager import ToolManager
from pydantic import ValidationError

from app.audit.logger import record_audit_event
from app.mcp import tools as tools_module
from app.mcp.context import ToolContext, is_valid_context
from app.mcp.limits import (
    AUDIT_ACTION_PREFIX,
    AUDIT_TOOL_NAME_MAX_LENGTH,
    TOOL_RESULT_MAX_BYTES,
    TOOL_TIMEOUT_SECONDS,
    TRUNCATION_MARKER,
    UNKNOWN_TOOL_AUDIT_ACTION,
)

_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")

logger = logging.getLogger(__name__)


def audit_action_for(tool_name: str) -> str:
    """Sanitized `mcp.<name>` audit action for a registered tool name.

    Rejected attempts to call a non-existent tool never produce an action from
    model input: they audit under UNKNOWN_TOOL_AUDIT_ACTION (closed inventory).
    """
    safe = _UNSAFE_NAME_RE.sub("_", str(tool_name))[:AUDIT_TOOL_NAME_MAX_LENGTH] or "unknown_tool"
    return f"{AUDIT_ACTION_PREFIX}{safe}"


def enforce_result_budget(payload: dict[str, Any], max_bytes: int = TOOL_RESULT_MAX_BYTES) -> dict[str, Any]:
    """Cap the serialized tool result at `max_bytes` with explicit truncation.

    Largest list entries are dropped first (adding a `truncated` flag), then the
    longest text values are cut with TRUNCATION_MARKER. Deterministic and
    side-effect free on the caller's dict.
    """
    # Reserve room for the truncation marker fields added at the end.
    marker_reserve = 256

    def size_of(value: dict[str, Any]) -> int:
        return len(json.dumps(value, default=str).encode("utf-8"))

    result = json.loads(json.dumps(payload, default=str))
    if size_of(result) <= max_bytes:
        return result

    def list_fields(value: Any) -> list[list[Any]]:
        found: list[list[Any]] = []
        if isinstance(value, dict):
            for item in value.values():
                found.extend(list_fields(item))
        elif isinstance(value, list):
            found.append(value)
            for item in value:
                found.extend(list_fields(item))
        return found

    truncated = False
    while size_of(result) > max_bytes - marker_reserve:
        lists = sorted(list_fields(result), key=len, reverse=True)
        if lists and lists[0]:
            lists[0].pop()
            truncated = True
            continue
        texts = [
            (container, key)
            for container in _walk_dicts(result)
            for key, value in container.items()
            if isinstance(value, str) and len(value) > 16
        ]
        if not texts:
            break
        container, key = max(texts, key=lambda pair: len(pair[0][pair[1]]))
        value = container[key]
        container[key] = value[: len(value) // 2] + TRUNCATION_MARKER
        truncated = True

    if truncated:
        result["truncated"] = True
        result["note"] = f"Result was truncated to fit the size limit.{TRUNCATION_MARKER}"
    return result


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(_walk_dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_dicts(item))
    return found


@dataclass(frozen=True)
class ToolCallResult:
    """One executed (or rejected) tool call.

    `model_payload` is the size-capped JSON handed back to the model;
    `citations` are the synthetic record references for the API response;
    `outcome` is the audited outcome (success | denied | error).
    """

    tool_name: str
    ok: bool
    outcome: str
    model_payload: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, str]] = field(default_factory=list)


class MCPServer:
    """In-process MCP tool registry (internal only - never a transport)."""

    def __init__(
        self,
        manager: ToolManager | None = None,
        *,
        tool_timeout_seconds: float = TOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._manager = manager or build_tool_manager()
        self._tool_timeout_seconds = tool_timeout_seconds

    def tool_names(self) -> tuple[str, ...]:
        """Names of the registered tools - the server-side allowlist."""
        return tuple(sorted(tool.name for tool in self._manager.list_tools()))

    def json_schemas(self) -> dict[str, dict[str, Any]]:
        """Raw JSON Schemas (JSON-Schema-typed arguments with bounds)."""
        return {tool.name: tool.parameters for tool in self._manager.list_tools()}

    def tool_schemas(self) -> list[dict[str, Any]]:
        """OpenAI-compatible function-tool schemas for the LLM provider."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._manager.list_tools()
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        tool_context: ToolContext | None,
    ) -> ToolCallResult:
        """Execute one tool call under allowlist, bounds, audit, and safe errors."""
        tool = self._manager.get_tool(name) if isinstance(name, str) else None
        if tool is None:
            attempted = str(name)[:AUDIT_TOOL_NAME_MAX_LENGTH]
            await record_audit_event(
                actor_user_id=tool_context.principal.user_uuid if is_valid_context(tool_context) else None,
                action=UNKNOWN_TOOL_AUDIT_ACTION,
                resource_type="mcp_tool",
                resource_id=audit_action_for(attempted)[len(AUDIT_ACTION_PREFIX) :],
                outcome="error",
                request_id=tool_context.request_id if is_valid_context(tool_context) else None,
            )
            return ToolCallResult(
                tool_name=attempted,
                ok=False,
                outcome="error",
                model_payload={
                    "ok": False,
                    "error": {"code": "unknown_tool", "message": "Requested tool is not available."},
                },
            )

        if not is_valid_context(tool_context):
            await record_audit_event(
                actor_user_id=None,
                action=audit_action_for(name),
                resource_type="mcp_tool",
                resource_id=None,
                outcome="denied",
                request_id=None,
            )
            return ToolCallResult(
                tool_name=name,
                ok=False,
                outcome="denied",
                model_payload={
                    "ok": False,
                    "error": {
                        "code": "forbidden",
                        "message": "This tool requires a verified caller context; the call was denied.",
                    },
                },
            )

        arguments = arguments if isinstance(arguments, dict) else {}
        try:
            tool.fn_metadata.arg_model.model_validate(arguments)
        except ValidationError:
            # Never echo submitted values: they may contain anything.
            await record_audit_event(
                actor_user_id=tool_context.principal.user_uuid,
                action=audit_action_for(name),
                resource_type="mcp_tool",
                resource_id=None,
                outcome="error",
                request_id=tool_context.request_id,
            )
            return ToolCallResult(
                tool_name=name,
                ok=False,
                outcome="error",
                model_payload={
                    "ok": False,
                    "error": {
                        "code": "invalid_arguments",
                        "message": f"Arguments for {name} are invalid or out of bounds.",
                    },
                },
            )

        try:
            raw = await asyncio.wait_for(
                tool.run(arguments, context=tool_context, convert_result=False),
                timeout=self._tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            await record_audit_event(
                actor_user_id=tool_context.principal.user_uuid,
                action=audit_action_for(name),
                resource_type="mcp_tool",
                resource_id=None,
                outcome="error",
                request_id=tool_context.request_id,
            )
            return ToolCallResult(
                tool_name=name,
                ok=False,
                outcome="error",
                model_payload={
                    "ok": False,
                    "error": {"code": "tool_timeout", "message": "The tool took too long and was cancelled."},
                },
            )
        except Exception as exc:  # noqa: BLE001 - tool crashes must surface as safe errors
            # Diagnostic detail stays in server logs (type only, no payload).
            logger.warning(
                "mcp tool raised",
                extra={"tool": name, "error_type": type(exc).__name__, "request_id": tool_context.request_id},
            )
            await record_audit_event(
                actor_user_id=tool_context.principal.user_uuid,
                action=audit_action_for(name),
                resource_type="mcp_tool",
                resource_id=None,
                outcome="error",
                request_id=tool_context.request_id,
            )
            return ToolCallResult(
                tool_name=name,
                ok=False,
                outcome="error",
                model_payload={
                    "ok": False,
                    "error": {"code": "tool_error", "message": "The tool failed in a controlled way."},
                },
            )

        if not isinstance(raw, dict) or "ok" not in raw:
            return ToolCallResult(
                tool_name=name,
                ok=False,
                outcome="error",
                model_payload={
                    "ok": False,
                    "error": {"code": "tool_error", "message": "The tool returned an unusable result."},
                },
            )

        ok = bool(raw.get("ok"))
        payload: dict[str, Any] = {"ok": ok}
        if ok:
            payload["data"] = raw.get("data") if raw.get("data") is not None else {}
        else:
            error = raw.get("error")
            payload["error"] = (
                error
                if isinstance(error, dict) and {"code", "message"} <= set(error)
                else {"code": "tool_error", "message": "The tool reported a controlled error."}
            )
        return ToolCallResult(
            tool_name=name,
            ok=ok,
            outcome=str(raw.get("outcome") or _outcome_from_envelope(raw)),
            model_payload=enforce_result_budget(payload),
            citations=[c for c in raw.get("citations", []) if isinstance(c, dict)],
        )


def _outcome_from_envelope(raw: dict[str, Any]) -> str:
    """The outcome the executing tool already audited (tools audit their own runs)."""
    if bool(raw.get("ok")):
        return "success"
    error = raw.get("error") or {}
    return "denied" if error.get("code") in ("forbidden", "not_found") else "error"


def build_tool_manager() -> ToolManager:
    """Register exactly the six allowlisted tools.

    `context_kwarg` names the server-injected context parameter; the SDK
    excludes it from the generated JSON Schema and forwards the caller-supplied
    context object verbatim - identity is never a tool argument.
    """
    tools = [
        Tool.from_function(
            tools_module.search_authorized_patients,
            name="search_authorized_patients",
            context_kwarg="tool_context",
        ),
        Tool.from_function(
            tools_module.get_patient_summary,
            name="get_patient_summary",
            context_kwarg="tool_context",
        ),
        Tool.from_function(
            tools_module.get_patient_lab_results,
            name="get_patient_lab_results",
            context_kwarg="tool_context",
        ),
        Tool.from_function(
            tools_module.get_recent_prescriptions,
            name="get_recent_prescriptions",
            context_kwarg="tool_context",
        ),
        Tool.from_function(
            tools_module.find_patients_by_lab,
            name="find_patients_by_lab",
            context_kwarg="tool_context",
        ),
        Tool.from_function(
            tools_module.find_patients_by_medication,
            name="find_patients_by_medication",
            context_kwarg="tool_context",
        ),
    ]
    return ToolManager(warn_on_duplicate_tools=False, tools=tools)


# Singleton used by the LLM tool loop; tests may build their own MCPServer.
mcp_server = MCPServer()
