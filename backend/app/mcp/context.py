"""Server-injected tool execution context (docs/ARCHITECTURE.md trust boundary 3).

`ToolContext` carries the verified acting identity and request correlation into
the MCP tool layer through a trusted in-process channel. It is built ONLY by
the backend caller from the verified server-side session
(`app.auth.identity.resolve_principal` / `request.state.principal`) and is
never a tool argument: the LLM-facing tool schema exposes no identity or
context parameter (see app/mcp/server.py).

A tool invoked without a valid ToolContext must deny the call and audit it;
there is no fallback identity and no anonymous data access.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.policy import Principal


@dataclass(frozen=True)
class ToolContext:
    """Verified caller context, injected by the backend orchestrator."""

    principal: Principal
    db: AsyncSession
    request_id: str | None = None
    ip: str | None = None


def is_valid_context(tool_context: object) -> bool:
    """True when `tool_context` is a usable, verified caller context."""
    if not isinstance(tool_context, ToolContext):
        return False
    principal = tool_context.principal
    if principal is None or not principal.is_active or not principal.user_id:
        return False
    return tool_context.db is not None
