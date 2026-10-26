"""Bounds applied to every MCP tool (docs/ARCHITECTURE.md "Tool bounds").

Every tool argument is typed and bounded, every result is bounded twice:
individual text fields are truncated at `TEXT_FIELD_MAX_LENGTH`, and the whole
serialized tool result is capped at `TOOL_RESULT_MAX_BYTES` (largest list items
dropped first, then text truncated), each with an explicit `[truncated]` marker.
"""
from __future__ import annotations

# Argument bounds.
SEARCH_QUERY_MAX_LENGTH = 100
SEARCH_LIMIT_MIN = 1
SEARCH_LIMIT_MAX = 25
SEARCH_LIMIT_DEFAULT = 10

SUMMARY_LIMIT_MIN = 1
SUMMARY_LIMIT_MAX = 25
SUMMARY_LIMIT_DEFAULT = 10

LIST_LIMIT_MIN = 1
LIST_LIMIT_MAX = 50
LIST_LIMIT_DEFAULT = 20

PATIENT_ID_MAX_LENGTH = 64

# Cross-patient lookup tools (find_patients_by_lab / find_patients_by_medication).
LOOKUP_LIMIT_MIN = 1
LOOKUP_LIMIT_MAX = 50
LOOKUP_LIMIT_DEFAULT = 25
TEST_CODE_MAX_LENGTH = 32
MEDICATION_NAME_MAX_LENGTH = 100
# Short "why it matched" reason string (e.g. "potassium 5.8 mmol/L high, 2026-09-24").
LOOKUP_MATCHED_MAX_LENGTH = 120
# The known-test-code allowlist is derived from the seeded catalog with one
# bounded query; this caps how many codes (and error-message hints) it carries.
LOOKUP_CATALOG_MAX_CODES = 50
# Lookup visibility filtering drops rows the access seam denies; paging past
# those rows is bounded by this candidate-scan cap (returned rows stay <= limit).
LOOKUP_MAX_SCAN = 200

# Result bounds.
TEXT_FIELD_MAX_LENGTH = 500
TOOL_RESULT_MAX_BYTES = 8192
TRUNCATION_MARKER = " [truncated]"

# Per-tool wall-clock bound (docs/API.md: "explicit timeouts").
TOOL_TIMEOUT_SECONDS = 10.0

# Audit action prefix and name sanitation for model-supplied tool names.
AUDIT_ACTION_PREFIX = "mcp."
AUDIT_TOOL_NAME_MAX_LENGTH = 64
# Rejected attempts to call a NON-EXISTENT tool audit under one fixed action
# (with the attempted name as resource reference) so the audit action
# inventory stays closed and never carries attacker-controlled strings.
UNKNOWN_TOOL_AUDIT_ACTION = "mcp.unknown_tool"
