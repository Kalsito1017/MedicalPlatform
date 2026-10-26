"""Internal MCP tool server (never publicly exposed).

The MCP server is an in-process module of the FastAPI application: it is never
exposed over stdio, HTTP, SSE, or any other transport in this deployment, and
it holds no database credentials of its own - tools execute through the shared
`app.services` layer under the same least-privilege role as the REST API.

Public surface (see app/mcp/server.py for the design decision record):

- `MCPServer` / `mcp_server`: the tool registry (list_tools/call_tool
  semantics, JSON-Schema-typed bounded arguments, safe errors, auditing).
- `ToolContext`: the server-injected verified caller context (principal, db
  session, request id) - never a tool argument.
- Exactly four read-only tools (`app.mcp.tools`): `search_authorized_patients`,
  `get_patient_summary`, `get_patient_lab_results`, `get_recent_prescriptions`.
  No `execute_sql`, shell, filesystem, or general-purpose database tool exists
  or may be added (docs/API.md MCP tool contract).
"""
from __future__ import annotations

from app.mcp.context import ToolContext, is_valid_context
from app.mcp.server import MCPServer, ToolCallResult, mcp_server

__all__ = [
    "MCPServer",
    "ToolCallResult",
    "ToolContext",
    "is_valid_context",
    "mcp_server",
]
