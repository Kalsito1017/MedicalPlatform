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

# get_my_recent_patients (the practitioner panel roundup).
# The LLM-facing schema exposes ONLY `days` and `limit`; the acting identity
# comes from the injected ToolContext (never a tool argument).
PANEL_DAYS_MIN = 1
PANEL_DAYS_MAX = 365
PANEL_DAYS_DEFAULT = 7
PANEL_LIMIT_MIN = 1
PANEL_LIMIT_MAX = 50
PANEL_LIMIT_DEFAULT = 25
# One compact row per patient; every string in it is bounded below.
PANEL_ROW_TEXT_MAX_LENGTH = 200  # symptoms / proposed plan
PANEL_ITEM_MAX_LENGTH = 100  # diagnosis names, key results, prescribed treatments
PANEL_DIAGNOSIS_MAX = 3
PANEL_KEY_RESULTS_MAX = 5
PANEL_TREATMENT_MAX = 3
# Per-patient fetch bounds for the row aggregates (key-result selection scans
# at most this many latest-per-test results before picking the bounded few).
PANEL_LABS_PER_PATIENT_MAX = 12
PANEL_WEIGHT_LOOKBACK = 1  # most recent encounter carrying vitals
# Visibility filtering runs the check_patient_access seam per candidate and
# pages past non-visible rows within this bounded candidate scan, so concealed
# rows never crowd out visible ones (same rule as the lookup tools).
PANEL_CANDIDATE_SCAN_LIMIT = 200

# get_patient_summary extension bounds (docs/API.md summary payload).
SUMMARY_DIAGNOSIS_MAX = 10
SUMMARY_PRESCRIPTION_ITEMS_MAX = 5

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
