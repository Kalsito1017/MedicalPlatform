"""MCP tool-layer tests (docs/API.md MCP tool contract, PLAN.md Phase 7).

Covers: the fixed seven-tool allowlist (no SQL/shell/filesystem tool), typed and
bounded JSON-Schema arguments, identity/context never exposed as a tool
argument, controlled safe errors for bad arguments and unknown tools, result
limits and truncation markers, ToolContext denial, authorization re-executed
per call (argument tampering cannot escalate), audit events for every call,
and hard tool timeouts — including the two cross-patient lookup tools
(find_patients_by_lab / find_patients_by_medication): reference-range
abnormality, active-only filtering, limit bounds, unknown-code handling,
wildcard escaping, and the per-patient visibility seam — and the practitioner
panel tool (get_my_recent_patients): window boundaries, practitioner scoping,
assignment union, identity tampering, limit/total, concealed rows, and the
row shape/values.

Direct tool invocation is deliberately tested (PLAN.md: "Test direct tool
invocation, malformed input, unauthorized access, and oversized result
requests").
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.mcp.server import MCPServer, audit_action_for, enforce_result_budget
from conftest import audit_rows

MCP_DIR = Path(__file__).resolve().parents[1] / "app" / "mcp"

EXPECTED_TOOLS = (
    "find_patients_by_lab",
    "find_patients_by_medication",
    "get_my_recent_patients",
    "get_patient_lab_results",
    "get_patient_summary",
    "get_recent_prescriptions",
    "search_authorized_patients",
)

# Every LLM-facing argument across the seven tools.
ALLOWED_SCHEMA_ARGS = {
    "query",
    "limit",
    "patient_id",
    "test_code",
    "abnormal_only",
    "medication_name",
    "active_only",
    "days",
}

FORBIDDEN_TOOL_MARKERS = (
    "execute_sql",
    "execute_query",
    "run_sql",
    "shell",
    "bash",
    "subprocess",
    "read_file",
    "write_file",
)

# The MCP layer must never reach a shell or write raw SQL. These imports are
# the only realistic gateways (beyond the registry test above).
FORBIDDEN_IMPORTS = (
    "import os",
    "import subprocess",
    "import shutil",
    "import socket",
    "from os import",
    "from subprocess",
    "from shutil",
    "from socket",
    "create_engine",
    "sqlalchemy.text",
    "text(",
)


# --- registry shape (no database needed) ----------------------------------


def test_registry_exposes_exactly_the_allowlisted_tools() -> None:
    from app.mcp.server import mcp_server

    assert mcp_server.tool_names() == EXPECTED_TOOLS


def test_no_sql_shell_or_filesystem_tool_exists() -> None:
    from app.mcp.server import mcp_server

    names = mcp_server.tool_names()
    for marker in FORBIDDEN_TOOL_MARKERS:
        assert all(marker not in name for name in names), marker
        assert all(
            marker not in json.dumps(schema).lower() for schema in mcp_server.tool_schemas()
        ), marker
    # Structural guarantee: no gateway to a shell, raw SQL, or the filesystem.
    for path in MCP_DIR.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            for marker in FORBIDDEN_IMPORTS:
                assert marker not in stripped, f"{path.name} imports {marker!r}"


def test_tool_schemas_are_typed_and_bounded() -> None:
    from app.mcp.limits import SEARCH_LIMIT_MAX, SEARCH_QUERY_MAX_LENGTH
    from app.mcp.server import mcp_server

    schemas = mcp_server.json_schemas()
    assert set(schemas) == set(EXPECTED_TOOLS)

    search = schemas["search_authorized_patients"]
    assert search["type"] == "object"
    assert search["properties"]["query"]["type"] == "string"
    assert search["properties"]["query"]["maxLength"] == SEARCH_QUERY_MAX_LENGTH
    assert search["properties"]["limit"]["type"] == "integer"
    assert search["properties"]["limit"]["minimum"] == 1
    assert search["properties"]["limit"]["maximum"] == SEARCH_LIMIT_MAX
    assert "required" in search and "query" in search["required"]

    assert schemas["get_patient_summary"]["properties"]["patient_id"]["type"] == "string"
    assert schemas["get_patient_summary"]["properties"]["limit"]["maximum"] == 25
    for name in ("get_patient_lab_results", "get_recent_prescriptions"):
        assert schemas[name]["properties"]["patient_id"]["type"] == "string"
        assert schemas[name]["properties"]["limit"]["minimum"] == 1
        assert schemas[name]["properties"]["limit"]["maximum"] == 50

    # The two lookup tools: typed, bounded, no identity or free-text SQL.
    from app.mcp.limits import (
        LOOKUP_LIMIT_DEFAULT,
        LOOKUP_LIMIT_MAX,
        LOOKUP_LIMIT_MIN,
        MEDICATION_NAME_MAX_LENGTH,
        TEST_CODE_MAX_LENGTH,
    )

    by_lab = schemas["find_patients_by_lab"]
    assert by_lab["properties"]["test_code"]["type"] == "string"
    assert by_lab["properties"]["test_code"]["maxLength"] == TEST_CODE_MAX_LENGTH
    assert by_lab["properties"]["abnormal_only"]["type"] == "boolean"
    assert by_lab["properties"]["limit"]["type"] == "integer"
    assert by_lab["properties"]["limit"]["minimum"] == LOOKUP_LIMIT_MIN
    assert by_lab["properties"]["limit"]["maximum"] == LOOKUP_LIMIT_MAX
    assert by_lab["properties"]["limit"]["default"] == LOOKUP_LIMIT_DEFAULT
    assert "test_code" in by_lab.get("required", [])

    by_med = schemas["find_patients_by_medication"]
    assert by_med["properties"]["medication_name"]["type"] == "string"
    assert by_med["properties"]["medication_name"]["maxLength"] == MEDICATION_NAME_MAX_LENGTH
    assert by_med["properties"]["active_only"]["type"] == "boolean"
    assert by_med["properties"]["limit"]["minimum"] == LOOKUP_LIMIT_MIN
    assert by_med["properties"]["limit"]["maximum"] == LOOKUP_LIMIT_MAX
    assert by_med["properties"]["limit"]["default"] == LOOKUP_LIMIT_DEFAULT
    assert "medication_name" in by_med.get("required", [])

    # The panel tool: typed, bounded, and only `days`/`limit` (identity is
    # server-injected and can never be a tool argument).
    from app.mcp.limits import (
        PANEL_DAYS_DEFAULT,
        PANEL_DAYS_MAX,
        PANEL_DAYS_MIN,
        PANEL_LIMIT_DEFAULT,
        PANEL_LIMIT_MAX,
        PANEL_LIMIT_MIN,
    )

    panel = schemas["get_my_recent_patients"]
    assert set(panel["properties"]) == {"days", "limit"}
    assert panel["properties"]["days"]["type"] == "integer"
    assert panel["properties"]["days"]["minimum"] == PANEL_DAYS_MIN
    assert panel["properties"]["days"]["maximum"] == PANEL_DAYS_MAX
    assert panel["properties"]["days"]["default"] == PANEL_DAYS_DEFAULT
    assert panel["properties"]["limit"]["type"] == "integer"
    assert panel["properties"]["limit"]["minimum"] == PANEL_LIMIT_MIN
    assert panel["properties"]["limit"]["maximum"] == PANEL_LIMIT_MAX
    assert panel["properties"]["limit"]["default"] == PANEL_LIMIT_DEFAULT
    assert panel.get("required", []) == []

    for name, schema in schemas.items():
        description = schema.get("description") or json.dumps(schema)
        assert len(description) > 40, f"{name} needs an explicit model-facing description"


def test_tool_schemas_never_expose_identity_or_context() -> None:
    from app.mcp.server import mcp_server

    forbidden_keys = (
        "principal",
        "user",
        "user_id",
        "actor",
        "identity",
        "role",
        "roles",
        "db",
        "session",
        "request_id",
        "tool_context",
        "context",
        "token",
        "api_key",
        "password",
        "sql",
        "query_sql",
    )
    for name, schema in mcp_server.json_schemas().items():
        props = set(schema.get("properties", {}))
        for key in forbidden_keys:
            assert key not in props, f"{name} exposes {key!r}"
        assert props <= ALLOWED_SCHEMA_ARGS, f"{name} has unexpected args {props}"


def test_text_truncation_marker() -> None:
    from app.mcp.limits import TEXT_FIELD_MAX_LENGTH, TRUNCATION_MARKER
    from app.mcp.tools import truncate_text

    assert truncate_text("short") == "short"
    long_value = "x" * (TEXT_FIELD_MAX_LENGTH * 3)
    cut = truncate_text(long_value)
    assert cut.endswith(TRUNCATION_MARKER)
    assert len(cut) <= TEXT_FIELD_MAX_LENGTH


def test_result_budget_truncates_oversized_payloads() -> None:
    from app.mcp.limits import TOOL_RESULT_MAX_BYTES

    payload = {
        "ok": True,
        "data": {
            "lab_results": [
                {"id": str(uuid.uuid4()), "test_name": "T" * 300, "value": "5.5"} for _ in range(60)
            ],
            "total": 60,
        },
    }
    result = enforce_result_budget(payload)
    assert len(json.dumps(result, default=str).encode("utf-8")) <= TOOL_RESULT_MAX_BYTES
    assert result["truncated"] is True
    assert "truncated" in result["note"].lower()
    assert len(result["data"]["lab_results"]) < 60
    # The caller's dict is untouched.
    assert len(payload["data"]["lab_results"]) == 60


def test_result_budget_truncates_long_text_fields() -> None:
    from app.mcp.limits import TOOL_RESULT_MAX_BYTES, TRUNCATION_MARKER

    payload = {"ok": True, "data": {"summary": "s" * (TOOL_RESULT_MAX_BYTES * 2)}}
    result = enforce_result_budget(payload, max_bytes=1000)
    assert len(json.dumps(result, default=str).encode("utf-8")) <= 1000
    assert result["data"]["summary"].endswith(TRUNCATION_MARKER)


def test_audit_action_sanitizes_model_supplied_names() -> None:
    assert audit_action_for("execute_sql") == "mcp.execute_sql"
    sanitized = audit_action_for("bad name; DROP TABLE--")
    assert sanitized.startswith("mcp.")
    assert " " not in sanitized and ";" not in sanitized
    assert len(sanitized) <= len("mcp.") + 64


# --- direct tool invocation with a real database ---------------------------


@pytest.fixture
async def tool_sessions(db_engine):
    """Open request-scoped sessions for direct tool calls; close them after."""
    from sqlalchemy.ext.asyncio import AsyncSession

    sessions: list[AsyncSession] = []

    def _open() -> AsyncSession:
        session = AsyncSession(db_engine, expire_on_commit=False)
        sessions.append(session)
        return session

    yield _open
    for session in sessions:
        await session.close()


@pytest.fixture
def make_tool_context(tool_sessions, make_user):
    """Build a verified ToolContext for a test account (server-injected, never an arg)."""
    from app.authorization.policy import Principal, permissions_for_roles
    from app.mcp.context import ToolContext

    async def _make(account, *, request_id: str = "test-request-mcp"):
        principal = Principal(
            user_id=str(account.id),
            roles=account.roles,
            is_active=True,
            email=account.email,
            permissions=permissions_for_roles(account.roles),
        )
        return ToolContext(
            principal=principal,
            db=tool_sessions(),
            request_id=request_id,
            ip="127.0.0.1",
        )

    return _make


@pytest.mark.postgres
async def test_unknown_tool_is_rejected_and_audited(make_user, make_tool_context) -> None:
    from app.mcp.limits import UNKNOWN_TOOL_AUDIT_ACTION
    from app.mcp.server import mcp_server

    ctx = await make_tool_context(await make_user(roles=("doctor",)))
    result = await mcp_server.call_tool("execute_sql", {"query": "SELECT 1"}, ctx)
    assert result.ok is False
    assert result.outcome == "error"
    assert result.model_payload["error"]["code"] == "unknown_tool"
    assert "SELECT" not in json.dumps(result.model_payload)

    rows = await audit_rows(action=UNKNOWN_TOOL_AUDIT_ACTION, outcome="error")
    assert rows, "unknown tool calls must be audited as error"
    assert rows[-1].request_id == "test-request-mcp"
    assert rows[-1].resource_id == "execute_sql"
    # The attempted name never becomes an audit action (closed inventory).
    assert not await audit_rows(action="mcp.execute_sql", request_id="test-request-mcp")


@pytest.mark.postgres
@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "x", "limit": 999},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": "not-a-number"},
        {"limit": 5},
        {"query": "SENTINEL-QUERY-9f3c" + "y" * 200},
    ],
)
async def test_bad_arguments_are_controlled_and_never_echo_input(
    arguments, make_user, make_tool_context
) -> None:
    from app.mcp.server import mcp_server

    ctx = await make_tool_context(await make_user(roles=("doctor",)))
    result = await mcp_server.call_tool("search_authorized_patients", arguments, ctx)
    assert result.ok is False
    assert result.outcome == "error"
    assert result.model_payload["error"]["code"] == "invalid_arguments"
    serialized = json.dumps(result.model_payload)
    assert "SENTINEL-QUERY" not in serialized
    assert "Traceback" not in serialized and "sql" not in serialized.lower()

    rows = await audit_rows(action="mcp.search_authorized_patients", outcome="error")
    assert rows


@pytest.mark.postgres
async def test_tool_call_without_context_is_denied_and_audited() -> None:
    from app.mcp.server import mcp_server

    result = await mcp_server.call_tool(
        "get_patient_summary", {"patient_id": str(uuid.uuid4())}, None
    )
    assert result.ok is False
    assert result.outcome == "denied"
    assert result.model_payload["error"]["code"] == "forbidden"

    rows = await audit_rows(action="mcp.get_patient_summary", outcome="denied")
    assert rows
    assert rows[-1].actor_user_id is None

    # Direct invocation (bypassing the registry) must deny too.
    from app.mcp.tools import get_patient_summary

    envelope = await get_patient_summary(str(uuid.uuid4()), tool_context=None)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "forbidden"


@pytest.mark.postgres
async def test_search_returns_only_authorized_patients(
    clinical_data, make_user, make_tool_context
) -> None:
    from app.mcp.server import mcp_server

    doctor_ctx = await make_tool_context(await make_user(roles=("doctor",)))
    result = await mcp_server.call_tool(
        "search_authorized_patients", {"query": clinical_data.token, "limit": 25}, doctor_ctx
    )
    assert result.ok is True
    found = {item["id"] for item in result.model_payload["data"]["patients"]}
    assert str(clinical_data.patient_id) in found
    assert str(clinical_data.empty_patient_id) in found
    assert str(clinical_data.soft_deleted_patient_id) not in found
    assert {c["id"] for c in result.citations} == found

    admin_ctx = await make_tool_context(await make_user(roles=()))
    denied = await mcp_server.call_tool(
        "search_authorized_patients", {"query": clinical_data.token, "limit": 25}, admin_ctx
    )
    assert denied.ok is True  # the call completed ...
    assert denied.model_payload["data"]["patients"] == []  # ... but sees nothing
    assert denied.citations == []
    rows = await audit_rows(action="mcp.search_authorized_patients", outcome="denied")
    assert rows


@pytest.mark.postgres
async def test_patient_tools_return_bounded_synthetic_data(
    clinical_data, make_user, make_tool_context
) -> None:
    from app.mcp.server import mcp_server

    ctx = await make_tool_context(
        await make_user(roles=("doctor",)), request_id="test-request-data"
    )
    patient_id = str(clinical_data.patient_id)

    summary = await mcp_server.call_tool(
        "get_patient_summary", {"patient_id": patient_id, "limit": 10}, ctx
    )
    assert summary.ok is True
    data = summary.model_payload["data"]
    assert data["patient"]["id"] == patient_id
    assert data["patient"]["provenance"] == "synthetic"
    assert "created_at" not in data["patient"]
    assert {item["id"] for item in data["encounters"]} == {
        str(clinical_data.encounter_id),
        str(clinical_data.old_encounter_id),
    }
    assert len(data["latest_labs"]) <= 20
    assert len(data["active_prescriptions"]) <= 20
    citation_types = {c["type"] for c in summary.citations}
    assert {"patient", "encounter", "lab_result", "prescription"} <= citation_types

    labs = await mcp_server.call_tool(
        "get_patient_lab_results", {"patient_id": patient_id, "limit": 50}, ctx
    )
    assert labs.ok is True
    lab_ids = {item["id"] for item in labs.model_payload["data"]["lab_results"]}
    assert str(clinical_data.lab_ids[0]) in lab_ids
    assert str(clinical_data.soft_deleted_lab_id) not in lab_ids

    prescriptions = await mcp_server.call_tool(
        "get_recent_prescriptions", {"patient_id": patient_id, "limit": 50}, ctx
    )
    assert prescriptions.ok is True
    medication_names = {
        item["medication_name"] for item in prescriptions.model_payload["data"]["prescriptions"]
    }
    assert {"Metformin", "Lisinopril", "Amoxicillin"} <= medication_names

    rows = await audit_rows(action="mcp.get_patient_summary", outcome="success")
    assert rows
    row = rows[-1]
    assert row.resource_type == "patient"
    assert row.resource_id == patient_id
    assert row.request_id == "test-request-data"
    # Audit rows never carry clinical payloads.
    assert clinical_data.patient_name not in repr(row)
    assert "Metformin" not in repr(row)


@pytest.mark.postgres
async def test_argument_tampering_cannot_bypass_authorization(
    clinical_data, make_user, make_tool_context
) -> None:
    """Changing model-supplied arguments never changes what the caller may read."""
    from app.mcp.server import mcp_server

    restricted_ctx = await make_tool_context(
        await make_user(roles=()), request_id="test-request-tamper"
    )
    # A real, existing patient id in the arguments grants nothing.
    for tool_name, arguments in (
        ("get_patient_summary", {"patient_id": str(clinical_data.patient_id)}),
        ("get_patient_lab_results", {"patient_id": str(clinical_data.patient_id), "limit": 50}),
        ("get_recent_prescriptions", {"patient_id": str(clinical_data.patient_id), "limit": 50}),
    ):
        result = await mcp_server.call_tool(tool_name, arguments, restricted_ctx)
        assert result.ok is False, tool_name
        assert result.outcome == "denied", tool_name
        assert result.model_payload["error"]["code"] == "forbidden", tool_name

    # Extra "identity" arguments are ignored: identity comes from ToolContext.
    result = await mcp_server.call_tool(
        "get_patient_summary",
        {
            "patient_id": str(clinical_data.patient_id),
            "user_id": "00000000-0000-0000-0000-00000000dead",
            "acting_as": "admin",
        },
        restricted_ctx,
    )
    assert result.ok is False
    assert result.outcome == "denied"

    rows = await audit_rows(action="mcp.get_patient_lab_results", outcome="denied")
    assert rows
    assert rows[-1].request_id == "test-request-tamper"


@pytest.mark.postgres
async def test_missing_or_concealed_patients_are_not_found(
    clinical_data, make_user, make_tool_context
) -> None:
    from app.mcp.server import mcp_server

    ctx = await make_tool_context(await make_user(roles=("doctor",)))
    for bad_id in (str(uuid.uuid4()), str(clinical_data.soft_deleted_patient_id), "not-a-uuid"):
        result = await mcp_server.call_tool("get_patient_summary", {"patient_id": bad_id}, ctx)
        assert result.ok is False
        assert result.model_payload["error"]["code"] == "not_found", bad_id
        assert result.outcome == "denied"
    rows = await audit_rows(action="mcp.get_patient_summary", outcome="denied")
    assert rows


@pytest.mark.postgres
async def test_summary_text_fields_are_truncated_with_marker(
    db_engine, clinical_data, make_user, make_tool_context
) -> None:
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.mcp.limits import TEXT_FIELD_MAX_LENGTH, TRUNCATION_MARKER
    from app.mcp.server import mcp_server
    from app.models import Encounter

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(
            Encounter(
                id=uuid.uuid4(),
                patient_id=clinical_data.patient_id,
                occurred_at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
                encounter_type="progress note",
                summary="Long note. " + "x" * 5000,
            )
        )
        await session.commit()

    ctx = await make_tool_context(await make_user(roles=("doctor",)))
    result = await mcp_server.call_tool(
        "get_patient_summary", {"patient_id": str(clinical_data.patient_id), "limit": 25}, ctx
    )
    assert result.ok is True
    summaries = [item["summary"] for item in result.model_payload["data"]["encounters"]]
    assert any(value.endswith(TRUNCATION_MARKER) for value in summaries)
    assert all(len(value) <= TEXT_FIELD_MAX_LENGTH for value in summaries)


@pytest.mark.postgres
async def test_oversized_tool_result_is_capped_with_marker(
    db_engine, clinical_data, make_user, make_tool_context
) -> None:
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.mcp.limits import TOOL_RESULT_MAX_BYTES
    from app.mcp.server import mcp_server
    from app.models import LabResult

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                LabResult(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    test_code=f"T{i:03d}",
                    test_name="Long test name " + "n" * 300,
                    value_text="within range",
                    unit="mmol/L",
                    status="final",
                    collected_at=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
                )
                for i in range(40)
            ]
        )
        await session.commit()

    ctx = await make_tool_context(await make_user(roles=("doctor",)))
    result = await mcp_server.call_tool(
        "get_patient_lab_results", {"patient_id": str(clinical_data.patient_id), "limit": 50}, ctx
    )
    assert result.ok is True
    serialized = json.dumps(result.model_payload, default=str).encode("utf-8")
    assert len(serialized) <= TOOL_RESULT_MAX_BYTES
    assert result.model_payload.get("truncated") is True
    assert result.model_payload["data"]["total"] == 44


@pytest.mark.postgres
async def test_tool_timeout_is_controlled_and_audited(make_user, make_tool_context) -> None:
    from mcp.server.fastmcp.tools.base import Tool
    from mcp.server.fastmcp.tools.tool_manager import ToolManager

    async def slow_get_patient_summary(patient_id: str, tool_context=None) -> dict:
        await asyncio.sleep(0.5)
        return {"ok": True, "data": {}}

    manager = ToolManager(
        warn_on_duplicate_tools=False,
        tools=[
            Tool.from_function(
                slow_get_patient_summary,
                name="get_patient_summary",
                context_kwarg="tool_context",
            )
        ],
    )
    server = MCPServer(manager, tool_timeout_seconds=0.05)
    ctx = await make_tool_context(
        await make_user(roles=("doctor",)), request_id="test-request-timeout"
    )
    result = await server.call_tool("get_patient_summary", {"patient_id": "p-1"}, ctx)
    assert result.ok is False
    assert result.model_payload["error"]["code"] == "tool_timeout"
    assert result.outcome == "error"
    rows = await audit_rows(
        action="mcp.get_patient_summary", outcome="error", request_id="test-request-timeout"
    )
    assert rows


# --- cross-patient lookup tools -------------------------------------------


async def _add_patients_and_labs(db_engine, clinical_data, token: str):
    """Fixture patients + deterministic lookup rows under unique test codes.

    Rows for the soft-deleted patient are deliberately included so tests prove
    the visibility seam (not the SQL) drops them. Returns (gamma_id, code,
    text_code, lab_ids).
    """
    from datetime import datetime, timezone
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import LabResult, Patient

    gamma_id = uuid.uuid4()
    code = f"XL{token}"
    text_code = f"XT{token}"
    lab_ids = [uuid.uuid4() for _ in range(7)]

    def lab(i, patient_id, *, value=None, text=None, collected, ref_low="3.500", ref_high="5.100"):
        return LabResult(
            id=lab_ids[i],
            patient_id=patient_id,
            test_code=code if text is None else text_code,
            test_name=f"LookupTest {token}" if text is None else f"TextTest {token}",
            value_num=Decimal(value) if value is not None else None,
            value_text=text,
            unit="mmol/L" if text is None else "mIU/L",
            ref_low=Decimal(ref_low) if text is None else None,
            ref_high=Decimal(ref_high) if text is None else None,
            status="final",
            collected_at=datetime.fromisoformat(collected).replace(tzinfo=timezone.utc),
        )

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(
            Patient(
                id=gamma_id,
                mrn=f"DEMO-T{token}G",
                display_name=f"Fixture Gamma {token}",
                given_name="Fixture",
                family_name=f"Gamma {token}",
                sex="other",
                provenance="synthetic",
            )
        )
        await session.flush()
        session.add_all(
            [
                # Alpha: normal, then abnormal high, then normal again.
                lab(0, clinical_data.patient_id, value="4.500", collected="2026-10-01T08:00:00"),
                lab(1, clinical_data.patient_id, value="5.800", collected="2026-10-02T08:00:00"),
                lab(2, clinical_data.patient_id, value="4.600", collected="2026-10-05T08:00:00"),
                # Beta: only a low result.
                lab(3, clinical_data.empty_patient_id, value="3.000", collected="2026-10-03T08:00:00"),
                # Gamma: only a normal numeric result and a text-only result.
                lab(4, gamma_id, value="4.600", collected="2026-10-04T08:00:00"),
                lab(5, gamma_id, text="within range", collected="2026-10-01T08:00:00"),
                # Soft-deleted patient: matches the query but must never be listed.
                lab(6, clinical_data.soft_deleted_patient_id, value="9.900", collected="2026-10-06T08:00:00"),
            ]
        )
        await session.commit()
    return gamma_id, code, text_code, lab_ids


@pytest.mark.postgres
async def test_find_patients_by_lab_abnormal_only_uses_reference_ranges(
    db_engine, clinical_data, make_user, make_tool_context
) -> None:
    """value_num vs ref_low/ref_high is authoritative; `status` is not a flag.

    The seeded fixture's deliberate pattern (Glucose 6.100 above 4.000-6.000)
    is exactly what this filter detects; the unique-code rows below pin the
    exact behavior (high/low direction, most-recent-matching selection,
    text-only exclusion, soft-deleted patients never listed).
    """
    from app.mcp.server import mcp_server

    token = clinical_data.token
    gamma_id, code, text_code, lab_ids = await _add_patients_and_labs(
        db_engine, clinical_data, token
    )
    alpha = str(clinical_data.patient_id)
    beta = str(clinical_data.empty_patient_id)

    ctx = await make_tool_context(
        await make_user(roles=("doctor",)), request_id="test-request-lab-lookup"
    )
    abnormal = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": code, "abnormal_only": True, "limit": 50}, ctx
    )
    assert abnormal.ok is True
    assert abnormal.outcome == "success"
    rows = abnormal.model_payload["data"]["patients"]
    # Exact set and order: only abnormal results qualify, most recent first.
    assert [row["patient_id"] for row in rows] == [beta, alpha]
    assert rows[0]["matched"] == f"LookupTest {token} 3 mmol/L low, 2026-10-03"
    assert rows[1]["matched"] == f"LookupTest {token} 5.8 mmol/L high, 2026-10-02"
    for row in rows:
        assert set(row) == {"patient_id", "display_name", "mrn", "matched"}
        assert row["mrn"].startswith("DEMO-")
    # Gamma (normal only) and the soft-deleted patient are never listed.
    listed = {row["patient_id"] for row in rows}
    assert str(gamma_id) not in listed
    assert str(clinical_data.soft_deleted_patient_id) not in listed
    serialized_rows = json.dumps(rows)
    assert f"Fixture Gone {token}" not in serialized_rows  # concealed name leaks nothing
    citation_ids = {(c["type"], c["id"]) for c in abnormal.citations}
    assert ("patient", alpha) in citation_ids
    assert ("lab_result", str(lab_ids[1])) in citation_ids

    all_rows = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": code, "abnormal_only": False, "limit": 50}, ctx
    )
    assert all_rows.ok is True
    rows = all_rows.model_payload["data"]["patients"]
    assert [row["patient_id"] for row in rows] == [alpha, str(gamma_id), beta]
    # Most recent matching result is shown: Alpha's later normal value, no flag.
    assert rows[0]["matched"] == f"LookupTest {token} 4.6 mmol/L, 2026-10-05"
    assert "high" not in rows[0]["matched"] and "low" not in rows[0]["matched"]
    assert str(clinical_data.soft_deleted_patient_id) not in {
        row["patient_id"] for row in rows
    }

    # Text-only results match only without abnormal_only and never flag.
    text_rows = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": text_code, "abnormal_only": False, "limit": 50}, ctx
    )
    assert [row["matched"] for row in text_rows.model_payload["data"]["patients"]] == [
        f"TextTest {token} within range mIU/L, 2026-10-01"
    ]
    no_text_abnormal = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": text_code, "abnormal_only": True, "limit": 50}, ctx
    )
    assert no_text_abnormal.model_payload["data"]["patients"] == []

    # The deliberate fixture pattern is detected by the same rule.
    glu = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": "GLU", "abnormal_only": True, "limit": 50}, ctx
    )
    glu_rows = {
        row["patient_id"]: row["matched"] for row in glu.model_payload["data"]["patients"]
    }
    if alpha in glu_rows:  # the persistent test DB may hold many GLU patients
        assert "high" in glu_rows[alpha] and "6.1" in glu_rows[alpha]


@pytest.mark.postgres
async def test_find_patients_by_lab_unknown_test_code_is_a_controlled_error(
    clinical_data, make_user, make_tool_context
) -> None:
    from app.mcp.server import mcp_server

    ctx = await make_tool_context(
        await make_user(roles=("doctor",)), request_id="test-request-unknown-code"
    )
    submitted = f"NOPE-{clinical_data.token}"
    result = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": submitted, "limit": 25}, ctx
    )
    assert result.ok is False
    assert result.outcome == "error"
    error = result.model_payload["error"]
    assert error["code"] == "unknown_test_code"
    serialized = json.dumps(result.model_payload)
    # The submitted value is never echoed; the known codes are catalog data.
    assert submitted not in serialized
    assert "NOPE" not in serialized
    assert "GLU" in error["message"] and "Potassium" in error["message"]
    assert "Traceback" not in serialized and "SELECT" not in serialized

    rows = await audit_rows(
        action="mcp.find_patients_by_lab", outcome="error", request_id="test-request-unknown-code"
    )
    assert rows
    assert submitted not in repr(rows[-1])


@pytest.mark.postgres
async def test_find_patients_by_lab_limit_bounds_and_clamping(
    db_engine, clinical_data, make_user, make_tool_context
) -> None:
    from app.mcp.server import mcp_server

    token = clinical_data.token
    _, code, _, _ = await _add_patients_and_labs(db_engine, clinical_data, token)
    ctx = await make_tool_context(await make_user(roles=("doctor",)))

    # Through the registry an out-of-range limit is a controlled error that
    # never echoes the input (the same contract as the other tools).
    for bad_limit in (0, 51):
        result = await mcp_server.call_tool(
            "find_patients_by_lab", {"test_code": code, "limit": bad_limit}, ctx
        )
        assert result.ok is False
        assert result.model_payload["error"]["code"] == "invalid_arguments"
        assert code not in json.dumps(result.model_payload)

    one = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": code, "abnormal_only": False, "limit": 1}, ctx
    )
    rows = one.model_payload["data"]["patients"]
    assert [row["patient_id"] for row in rows] == [str(clinical_data.patient_id)]
    # The most recent row overall belongs to the soft-deleted patient (2026-10-06);
    # the visibility seam drops it and paging continues to the next visible row.
    assert rows[0]["matched"] == f"LookupTest {clinical_data.token} 4.6 mmol/L, 2026-10-05"

    # Direct invocation clamps to 1..50 (defense in depth).
    from app.mcp.tools import find_patients_by_lab

    envelope = await find_patients_by_lab(code, abnormal_only=False, limit=9999, tool_context=ctx)
    assert len(envelope["data"]["patients"]) == 3  # the rows above, bounded by data
    tiny = await find_patients_by_lab(code, abnormal_only=False, limit=0, tool_context=ctx)
    assert len(tiny["data"]["patients"]) == 1


@pytest.mark.postgres
async def test_find_patients_by_medication_matches_names_codes_and_active_only(
    db_engine, clinical_data, make_user, make_tool_context
) -> None:
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.mcp.server import mcp_server
    from app.models import Medication, Prescription

    token = clinical_data.token
    med_name = f"MediTest {token}"
    brand_name = f"BrandName {token}"
    med_code = f"DEMO-MED-{token}"
    med_id = uuid.uuid4()
    rx_ids = [uuid.uuid4() for _ in range(4)]
    alpha = str(clinical_data.patient_id)
    beta = str(clinical_data.empty_patient_id)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add(
            Medication(
                id=med_id,
                code=med_code,
                display_name=brand_name,
                form="tablet",
            )
        )
        await session.flush()
        session.add_all(
            [
                Prescription(
                    id=rx_ids[0],
                    patient_id=clinical_data.patient_id,
                    medication_name=med_name,
                    dose_amount=Decimal("10.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="once daily",
                    status="active",
                    start_date=date(2026, 2, 1),
                ),
                Prescription(
                    id=rx_ids[1],
                    patient_id=clinical_data.empty_patient_id,
                    medication_name=med_name,
                    dose_amount=Decimal("10.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="once daily",
                    status="completed",
                    start_date=date(2026, 3, 1),
                ),
                # Soft-deleted patient: matches the query but must never be listed.
                Prescription(
                    id=rx_ids[2],
                    patient_id=clinical_data.soft_deleted_patient_id,
                    medication_name=med_name,
                    dose_amount=Decimal("10.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="once daily",
                    status="active",
                    start_date=date(2026, 4, 1),
                ),
                # Catalog-linked brand prescription matched via the catalog code.
                Prescription(
                    id=rx_ids[3],
                    patient_id=clinical_data.patient_id,
                    medication_id=med_id,
                    medication_name=brand_name,
                    dose_amount=Decimal("25.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="once daily",
                    status="active",
                    start_date=date(2026, 5, 1),
                ),
            ]
        )
        await session.commit()

    ctx = await make_tool_context(
        await make_user(roles=("doctor",)), request_id="test-request-med-lookup"
    )
    active = await mcp_server.call_tool(
        "find_patients_by_medication",
        {"medication_name": med_name.lower(), "active_only": True, "limit": 50},
        ctx,
    )
    assert active.ok is True and active.outcome == "success"
    rows = active.model_payload["data"]["patients"]
    # Case-insensitive substring match; completed and concealed rows excluded.
    assert [row["patient_id"] for row in rows] == [alpha]
    assert rows[0]["matched"] == f"{med_name} 10 mg, active"
    assert set(rows[0]) == {"patient_id", "display_name", "mrn", "matched"}

    all_rows = await mcp_server.call_tool(
        "find_patients_by_medication",
        {"medication_name": med_name, "active_only": False, "limit": 50},
        ctx,
    )
    rows = all_rows.model_payload["data"]["patients"]
    # One row per patient, most recent matching prescription first.
    assert [row["patient_id"] for row in rows] == [beta, alpha]
    assert rows[0]["matched"] == f"{med_name} 10 mg, completed"

    by_code = await mcp_server.call_tool(
        "find_patients_by_medication", {"medication_name": med_code, "limit": 50}, ctx
    )
    rows = by_code.model_payload["data"]["patients"]
    assert [row["patient_id"] for row in rows] == [alpha]
    assert rows[0]["matched"] == f"{brand_name} 25 mg, active"

    # LIKE wildcards in the input are escaped to literals: no match, no blast.
    wildcard = await mcp_server.call_tool(
        "find_patients_by_medication",
        {"medication_name": f"%{med_name}%", "limit": 50},
        ctx,
    )
    assert wildcard.model_payload["data"]["patients"] == []

    # Audit rows carry no search term, name, or clinical payload.
    audited = await audit_rows(
        action="mcp.find_patients_by_medication",
        outcome="success",
        request_id="test-request-med-lookup",
    )
    assert audited
    for row in audited:
        text = repr(row)
        assert med_name not in text and brand_name not in text
        assert token not in text
        assert clinical_data.patient_name not in text
        assert "Metformin" not in text
        assert row.resource_type == "patient"


@pytest.mark.postgres
async def test_lookup_tools_permission_denial_and_identity_args(
    db_engine, clinical_data, make_user, make_tool_context
) -> None:
    """patient:read + record-type permission are mandatory; identity args are inert."""
    from app.mcp.server import mcp_server

    _, code, _, _ = await _add_patients_and_labs(db_engine, clinical_data, clinical_data.token)
    smuggled = {
        "user_id": "00000000-0000-4000-8000-00000000dead",
        "role": "admin",
        "acting_as": "root",
    }

    admin_ctx = await make_tool_context(
        await make_user(roles=()), request_id="test-request-lookup-denied"
    )
    for tool_name, arguments in (
        ("find_patients_by_lab", {"test_code": code, **smuggled}),
        ("find_patients_by_medication", {"medication_name": clinical_data.token, **smuggled}),
    ):
        result = await mcp_server.call_tool(tool_name, arguments, admin_ctx)
        assert result.ok is True, tool_name
        assert result.outcome == "denied", tool_name
        assert result.model_payload["data"]["patients"] == [], tool_name
        assert result.citations == []
        rows = await audit_rows(
            action=f"mcp.{tool_name}", outcome="denied", request_id="test-request-lookup-denied"
        )
        assert rows, tool_name

    # The same smuggled arguments change nothing for an authorized caller.
    doctor_ctx = await make_tool_context(
        await make_user(roles=("doctor",)), request_id="test-request-lookup-tamper"
    )
    clean = await mcp_server.call_tool(
        "find_patients_by_lab", {"test_code": code, "abnormal_only": False, "limit": 50}, doctor_ctx
    )
    tampered = await mcp_server.call_tool(
        "find_patients_by_lab",
        {"test_code": code, "abnormal_only": False, "limit": 50, **smuggled},
        doctor_ctx,
    )
    assert tampered.model_payload["data"] == clean.model_payload["data"]
    assert clean.model_payload["data"]["patients"], "the fixture rows must be visible to a doctor"


@pytest.mark.postgres
async def test_lookup_tools_without_context_are_denied_and_audited() -> None:
    from app.mcp.server import mcp_server
    from app.mcp.tools import find_patients_by_lab, find_patients_by_medication

    for tool_name, arguments in (
        ("find_patients_by_lab", {"test_code": "K"}),
        ("find_patients_by_medication", {"medication_name": "metformin"}),
    ):
        result = await mcp_server.call_tool(tool_name, arguments, None)
        assert result.ok is False
        assert result.outcome == "denied"
        assert result.model_payload["error"]["code"] == "forbidden"
        rows = await audit_rows(action=f"mcp.{tool_name}", outcome="denied")
        assert rows
        assert rows[-1].actor_user_id is None

    for envelope in (
        await find_patients_by_lab("K", tool_context=None),
        await find_patients_by_medication("metformin", tool_context=None),
    ):
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "forbidden"


@pytest.mark.postgres
async def test_lookup_matched_text_is_truncated_with_marker(
    db_engine, clinical_data, make_user, make_tool_context
) -> None:
    from datetime import date, datetime, timezone
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.mcp.limits import LOOKUP_MATCHED_MAX_LENGTH, TRUNCATION_MARKER
    from app.mcp.server import mcp_server
    from app.models import LabResult, Prescription

    token = clinical_data.token
    long_med = f"LongMed {token} " + "x" * 300
    long_code = f"XQ{token}"
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                Prescription(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    medication_name=long_med,
                    dose_amount=Decimal("5.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="once daily",
                    status="active",
                    start_date=date(2026, 6, 1),
                ),
                LabResult(
                    id=uuid.uuid4(),
                    patient_id=clinical_data.patient_id,
                    test_code=long_code,
                    test_name="L" * 300,
                    value_num=Decimal("5.000"),
                    unit="mmol/L",
                    ref_low=Decimal("3.500"),
                    ref_high=Decimal("5.100"),
                    status="final",
                    collected_at=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
                ),
            ]
        )
        await session.commit()

    ctx = await make_tool_context(await make_user(roles=("doctor",)))
    by_med = await mcp_server.call_tool(
        "find_patients_by_medication",
        {"medication_name": f"LongMed {token}", "limit": 50},
        ctx,
    )
    matched = by_med.model_payload["data"]["patients"][0]["matched"]
    assert matched.endswith(TRUNCATION_MARKER)
    assert len(matched) <= LOOKUP_MATCHED_MAX_LENGTH

    by_lab = await mcp_server.call_tool(
        "find_patients_by_lab",
        {"test_code": long_code, "abnormal_only": False, "limit": 50},
        ctx,
    )
    matched = by_lab.model_payload["data"]["patients"][0]["matched"]
    assert matched.endswith(TRUNCATION_MARKER)
    assert len(matched) <= LOOKUP_MATCHED_MAX_LENGTH


# --- practitioner panel tool (get_my_recent_patients) ----------------------
#
# Deterministic fixture around a FROZEN clock (PANEL_NOW): window filtering,
# inclusive/exclusive boundaries, practitioner scoping, the assignment union,
# identity tampering, bounds, authorization, concealment, and row shape are all
# asserted against exact expected rows.


PANEL_NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

PANEL_ROW_KEYS = {
    "patient_id",
    "display_name",
    "mrn",
    "age_years",
    "sex",
    "blood_type",
    "last_encounter_at",
    "weight_kg",
    "symptoms",
    "diagnoses",
    "key_results",
    "proposed_plan",
    "prescribed_treatment",
}


@pytest.fixture
def freeze_panel_clock(monkeypatch):
    """Freeze THE clock (app.services.patients.utc_now) at PANEL_NOW."""
    from app.services import patients as patients_service

    monkeypatch.setattr(patients_service, "utc_now", lambda: PANEL_NOW)


async def _add_panel_data(db_engine, *, me_id, other_id, token):
    """Synthetic panel fixture (see the section comment) around PANEL_NOW.

    Patients (token-unique names/MRNs):
      alpha   - encounters with ME at -3d (vitals 70.50/165.00) and -20d
                (72.00/165.00); active + resolved diagnoses; one note; K high
                and GLU normal labs; one active Metformin prescription + item.
      beta    - encounter with ME at -20d only; note with 3000-char symptoms.
      gamma   - encounter with OTHER at -2d; active assignment to ME (union).
      delta   - encounter with OTHER at -1d; no assignment (never mine).
      epsilon - active assignment to ME; only a -40d encounter (out of window).
      zeta    - encounter with ME at exactly -7d (inclusive boundary).
      eta     - encounter with ME at exactly -30d (inclusive boundary).
      theta   - encounter with ME at +1d (future: outside every window).
      gone    - soft-deleted patient with a -1d encounter with ME (concealed).
    """
    from datetime import date as date_type
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import (
        ClinicalNote,
        Diagnosis,
        Encounter,
        LabResult,
        Patient,
        PatientAssignment,
        Prescription,
        PrescriptionItem,
    )

    def day(offset_days: int, hour: int = 12) -> datetime:
        return PANEL_NOW + timedelta(days=offset_days, hours=hour - 12)

    ids = {
        "alpha": uuid.uuid4(),
        "beta": uuid.uuid4(),
        "gamma": uuid.uuid4(),
        "delta": uuid.uuid4(),
        "epsilon": uuid.uuid4(),
        "zeta": uuid.uuid4(),
        "eta": uuid.uuid4(),
        "theta": uuid.uuid4(),
        "gone": uuid.uuid4(),
    }
    encounters = {
        "alpha_recent": uuid.uuid4(),
        "alpha_old": uuid.uuid4(),
        "beta": uuid.uuid4(),
        "gamma": uuid.uuid4(),
        "delta": uuid.uuid4(),
        "epsilon": uuid.uuid4(),
        "zeta": uuid.uuid4(),
        "eta": uuid.uuid4(),
        "theta": uuid.uuid4(),
        "gone": uuid.uuid4(),
    }
    extras = {
        "note_alpha": uuid.uuid4(),
        "note_beta": uuid.uuid4(),
        "dx_alpha_active": uuid.uuid4(),
        "dx_alpha_resolved": uuid.uuid4(),
        "lab_k": uuid.uuid4(),
        "lab_glu": uuid.uuid4(),
        "rx_alpha": uuid.uuid4(),
    }

    def patient(key, display, sex, *, dob=None, blood=None, deleted=False):
        return Patient(
            id=ids[key],
            mrn=f"DEMO-P{token}{key[:2].upper()}",
            display_name=display,
            given_name="Panel",
            family_name=display,
            date_of_birth=dob,
            sex=sex,
            blood_type=blood,
            provenance="synthetic",
            deleted_at=PANEL_NOW if deleted else None,
        )

    def encounter(key, patient_key, at, *, practitioner, weight=None, height=None):
        return Encounter(
            id=encounters[key],
            patient_id=ids[patient_key],
            occurred_at=at,
            encounter_type="progress note",
            summary=f"Synthetic panel encounter {token}.",
            practitioner_id=practitioner,
            weight_kg=Decimal(weight) if weight is not None else None,
            height_cm=Decimal(height) if height is not None else None,
        )

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                patient("alpha", f"Panel Alpha {token}", "female", dob=date_type(1980, 1, 15), blood="A+"),
                patient("beta", f"Panel Beta {token}", "male", dob=date_type(1992, 6, 30), blood="O-"),
                patient("gamma", f"Panel Gamma {token}", "other", dob=date_type(2001, 2, 2), blood="B+"),
                patient("delta", f"Panel Delta {token}", "male"),
                patient("epsilon", f"Panel Epsilon {token}", "female"),
                patient("zeta", f"Panel Zeta {token}", "male"),
                patient("eta", f"Panel Eta {token}", "female"),
                patient("theta", f"Panel Theta {token}", "male"),
                patient("gone", f"Panel Gone {token}", "unknown", deleted=True),
            ]
        )
        await session.flush()
        session.add_all(
            [
                encounter("alpha_recent", "alpha", day(-3), practitioner=me_id, weight="70.50", height="165.00"),
                encounter("alpha_old", "alpha", day(-20), practitioner=me_id, weight="72.00", height="165.00"),
                encounter("beta", "beta", day(-20), practitioner=me_id),
                encounter("gamma", "gamma", day(-2), practitioner=other_id, weight="75.25", height="170.00"),
                encounter("delta", "delta", day(-1), practitioner=other_id),
                encounter("epsilon", "epsilon", day(-40), practitioner=me_id),
                encounter("zeta", "zeta", day(-7), practitioner=me_id, weight="81.00"),
                encounter("eta", "eta", day(-30), practitioner=me_id),
                encounter("theta", "theta", day(1), practitioner=me_id),
                encounter("gone", "gone", day(-1), practitioner=me_id),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ClinicalNote(
                    id=extras["note_alpha"],
                    patient_id=ids["alpha"],
                    encounter_id=encounters["alpha_recent"],
                    author_id=me_id,
                    note_type="progress_note",
                    symptoms="fatigue and cold intolerance",
                    assessment="hypothyroidism",
                    proposed_plan="start levothyroxine 50 mcg daily; recheck TSH in 6 weeks",
                    body=f"Synthetic panel note {token}.",
                    created_at=day(-3),
                ),
                ClinicalNote(
                    id=extras["note_beta"],
                    patient_id=ids["beta"],
                    encounter_id=encounters["beta"],
                    author_id=me_id,
                    note_type="progress_note",
                    symptoms="x" * 3000,
                    assessment="stable",
                    proposed_plan="observe",
                    body=f"Synthetic panel note {token}.",
                    created_at=day(-20),
                ),
                Diagnosis(
                    id=extras["dx_alpha_active"],
                    patient_id=ids["alpha"],
                    encounter_id=encounters["alpha_recent"],
                    code="E11.9",
                    name="Type 2 diabetes mellitus",
                    status="active",
                    onset_date=date_type(2024, 5, 1),
                    recorded_at=day(-20),
                ),
                Diagnosis(
                    id=extras["dx_alpha_resolved"],
                    patient_id=ids["alpha"],
                    encounter_id=encounters["alpha_old"],
                    code="D50.9",
                    name="Iron deficiency anemia",
                    status="resolved",
                    onset_date=date_type(2023, 1, 1),
                    recorded_at=day(-25),
                ),
                LabResult(
                    id=extras["lab_k"],
                    patient_id=ids["alpha"],
                    encounter_id=encounters["alpha_recent"],
                    test_code="K",
                    test_name="Potassium",
                    value_num=Decimal("5.860"),
                    unit="mmol/L",
                    ref_low=Decimal("3.500"),
                    ref_high=Decimal("5.100"),
                    status="final",
                    collected_at=day(-3, 8),
                ),
                LabResult(
                    id=extras["lab_glu"],
                    patient_id=ids["alpha"],
                    encounter_id=encounters["alpha_recent"],
                    test_code="GLU",
                    test_name="Glucose, fasting",
                    value_num=Decimal("5.500"),
                    unit="mg/dL",
                    ref_low=Decimal("4.000"),
                    ref_high=Decimal("6.000"),
                    status="final",
                    collected_at=day(-3, 8),
                ),
                Prescription(
                    id=extras["rx_alpha"],
                    patient_id=ids["alpha"],
                    medication_name="Metformin",
                    dose_amount=Decimal("500.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="twice daily",
                    status="active",
                    start_date=date_type(2026, 1, 1),
                ),
                PatientAssignment(
                    patient_id=ids["gamma"],
                    staff_user_id=me_id,
                    care_role="attending",
                    assigned_at=day(-60),
                ),
                PatientAssignment(
                    patient_id=ids["epsilon"],
                    staff_user_id=me_id,
                    care_role="attending",
                    assigned_at=day(-60),
                ),
            ]
        )
        await session.flush()
        session.add(
            PrescriptionItem(
                prescription_id=extras["rx_alpha"],
                medication_name="Metformin",
                dose_amount=Decimal("500.000"),
                dose_unit="mg",
                sequence_no=1,
            )
        )
        await session.commit()
    return {"patients": ids, "encounters": encounters, **extras}


async def _panel_rows(result) -> list[dict]:
    assert result.ok is True, result.model_payload
    assert result.outcome == "success", result.model_payload
    return result.model_payload["data"]["patients"]


@pytest.mark.postgres
async def test_panel_roundup_window_filtering_and_boundaries(
    db_engine, make_user, make_tool_context, freeze_panel_clock
) -> None:
    """7 vs 30 day windows; both boundaries INCLUSIVE; future rows excluded."""
    from app.mcp.server import mcp_server

    me = await make_user(roles=("doctor",))
    other = await make_user(roles=("doctor",))
    data = await _add_panel_data(db_engine, me_id=me.id, other_id=other.id, token=uuid.uuid4().hex[:8])
    patients = data["patients"]
    ctx = await make_tool_context(me, request_id="test-request-panel-window")

    week = await mcp_server.call_tool("get_my_recent_patients", {"days": 7, "limit": 25}, ctx)
    rows = await _panel_rows(week)
    # gamma (-2d, assignment union), alpha (-3d), zeta (exactly -7d).
    assert [row["patient_id"] for row in rows] == [
        str(patients["gamma"]),
        str(patients["alpha"]),
        str(patients["zeta"]),
    ]
    assert week.model_payload["data"]["total"] == 3
    assert week.model_payload["data"]["window_days"] == 7
    assert week.model_payload["data"]["window_start"] == "2026-10-20T12:00:00Z"

    month = await mcp_server.call_tool("get_my_recent_patients", {"days": 30, "limit": 25}, ctx)
    rows = await _panel_rows(month)
    assert [row["patient_id"] for row in rows] == [
        str(patients["gamma"]),
        str(patients["alpha"]),
        str(patients["zeta"]),
        str(patients["beta"]),
        str(patients["eta"]),
    ]
    assert month.model_payload["data"]["total"] == 5

    # Boundary decision (docs/API.md): the window [now - days, now] is
    # INCLUSIVE at both ends - zeta (exactly -7d) and eta (exactly -30d) are
    # inside; theta's future encounter (+1d) is outside every window.
    listed = {row["patient_id"] for row in rows}
    assert str(patients["zeta"]) in listed and str(patients["eta"]) in listed
    assert str(patients["theta"]) not in listed
    week_ids = {row["patient_id"] for row in await _panel_rows(week)}
    assert str(patients["zeta"]) in week_ids  # exactly N days ago counts
    assert str(patients["eta"]) not in week_ids  # but only in the 30-day window
    assert str(patients["theta"]) not in week_ids


@pytest.mark.postgres
async def test_panel_practitioner_scoping_and_assignment_union(
    db_engine, make_user, make_tool_context, freeze_panel_clock
) -> None:
    """"Mine" only ever narrows: other practitioners' patients never appear."""
    from app.mcp.server import mcp_server

    me = await make_user(roles=("doctor",))
    other = await make_user(roles=("doctor",))
    data = await _add_panel_data(db_engine, me_id=me.id, other_id=other.id, token=uuid.uuid4().hex[:8])
    patients = data["patients"]
    ctx = await make_tool_context(me, request_id="test-request-panel-mine")
    other_ctx = await make_tool_context(other, request_id="test-request-panel-other")

    mine = {
        row["patient_id"]
        for row in await _panel_rows(
            await mcp_server.call_tool("get_my_recent_patients", {"days": 30, "limit": 50}, ctx)
        )
    }
    # delta: encounters belong to ANOTHER practitioner -> never mine.
    assert str(patients["delta"]) not in mine
    # epsilon: assigned to me but only OLD encounters -> outside the window.
    assert str(patients["epsilon"]) not in mine
    # gamma: encounter with another practitioner BUT active assignment to me
    # and an encounter in the window -> included via the union branch.
    assert str(patients["gamma"]) in mine

    # A second doctor's session can never obtain my rows.
    theirs = {
        row["patient_id"]
        for row in await _panel_rows(
            await mcp_server.call_tool("get_my_recent_patients", {"days": 30, "limit": 50}, other_ctx)
        )
    }
    assert theirs == {str(patients["gamma"]), str(patients["delta"])}
    for key in ("alpha", "beta", "zeta", "eta"):
        assert str(patients[key]) not in theirs


@pytest.mark.postgres
async def test_panel_identity_tampering_is_ignored_and_bounds_are_enforced(
    db_engine, make_user, make_tool_context, freeze_panel_clock
) -> None:
    """Identity comes only from ToolContext; out-of-range bounds are controlled."""
    from app.mcp.server import mcp_server

    me = await make_user(roles=("doctor",))
    other = await make_user(roles=("doctor",))
    data = await _add_panel_data(db_engine, me_id=me.id, other_id=other.id, token=uuid.uuid4().hex[:8])
    ctx = await make_tool_context(me, request_id="test-request-panel-tamper")

    clean = await mcp_server.call_tool("get_my_recent_patients", {"days": 30, "limit": 50}, ctx)
    tampered = await mcp_server.call_tool(
        "get_my_recent_patients",
        {
            "days": 30,
            "limit": 50,
            "user_id": str(other.id),
            "practitioner_id": str(other.id),
            "acting_as": "admin",
        },
        ctx,
    )
    assert tampered.model_payload["data"] == clean.model_payload["data"]
    listed = [row["patient_id"] for row in await _panel_rows(clean)]
    assert str(data["patients"]["alpha"]) in listed
    assert str(data["patients"]["delta"]) not in listed  # other's rows unreachable

    # Registry bounds: days 0/366 and limit 0/51 are controlled errors that
    # never echo the input.
    for arguments in ({"days": 0}, {"days": 366}, {"limit": 0}, {"limit": 51}):
        result = await mcp_server.call_tool("get_my_recent_patients", arguments, ctx)
        assert result.ok is False, arguments
        assert result.outcome == "error", arguments
        assert result.model_payload["error"]["code"] == "invalid_arguments", arguments
        assert "Traceback" not in json.dumps(result.model_payload)
    rows = await audit_rows(action="mcp.get_my_recent_patients", outcome="error")
    assert rows

    # Direct invocation clamps as defense in depth (1..365 / 1..50).
    from app.mcp.tools import get_my_recent_patients

    tiny = await get_my_recent_patients(days=0, limit=0, tool_context=ctx)
    assert tiny["ok"] is True
    assert tiny["data"]["window_days"] == 1
    assert len(tiny["data"]["patients"]) <= 1
    huge = await get_my_recent_patients(days=9999, limit=9999, tool_context=ctx)
    assert huge["ok"] is True
    assert huge["data"]["window_days"] == 365
    assert len(huge["data"]["patients"]) <= 50
    # Inside 365 days: alpha, beta, zeta, eta (with me), gamma + epsilon
    # (assignment union). delta is another practitioner's and theta is future.
    assert huge["data"]["total"] == 6


@pytest.mark.postgres
async def test_panel_authorization_denial_and_missing_context(
    db_engine, make_user, make_tool_context, freeze_panel_clock
) -> None:
    """No patient:read -> empty + note, outcome denied, audited (never data)."""
    from app.mcp.server import mcp_server

    me = await make_user(roles=("doctor",))
    other = await make_user(roles=("doctor",))
    data = await _add_panel_data(db_engine, me_id=me.id, other_id=other.id, token=uuid.uuid4().hex[:8])
    restricted = await make_tool_context(
        await make_user(roles=()), request_id="test-request-panel-denied"
    )

    denied = await mcp_server.call_tool("get_my_recent_patients", {"days": 30}, restricted)
    assert denied.ok is True  # the call completed ...
    assert denied.outcome == "denied"
    assert denied.model_payload["data"]["patients"] == []  # ... but sees nothing
    assert denied.model_payload["data"]["total"] == 0
    assert "visible" in denied.model_payload["data"]["note"]
    assert denied.citations == []
    serialized = json.dumps(denied.model_payload)
    assert f"Panel Alpha {data['patients']['alpha']}" not in serialized
    rows = await audit_rows(
        action="mcp.get_my_recent_patients", outcome="denied", request_id="test-request-panel-denied"
    )
    assert rows
    assert rows[-1].resource_type == "patient_panel"
    assert rows[-1].resource_id is None

    # Without a valid ToolContext the call is denied and audited (actor None).
    result = await mcp_server.call_tool("get_my_recent_patients", {"days": 30}, None)
    assert result.ok is False
    assert result.outcome == "denied"
    assert result.model_payload["error"]["code"] == "forbidden"
    rows = await audit_rows(action="mcp.get_my_recent_patients", outcome="denied")
    assert rows[-1].actor_user_id is None

    # Direct invocation (bypassing the registry) must deny too.
    from app.mcp.tools import get_my_recent_patients

    envelope = await get_my_recent_patients(tool_context=None)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "forbidden"


@pytest.mark.postgres
async def test_panel_row_shape_values_truncation_and_totals(
    db_engine, make_user, make_tool_context, freeze_panel_clock
) -> None:
    """Exact row shape; age/blood/weight/values correct; truncation markers."""
    from app.mcp.limits import PANEL_ROW_TEXT_MAX_LENGTH, TRUNCATION_MARKER
    from app.mcp.server import mcp_server

    me = await make_user(roles=("doctor",))
    other = await make_user(roles=("doctor",))
    data = await _add_panel_data(db_engine, me_id=me.id, other_id=other.id, token=uuid.uuid4().hex[:8])
    patients = data["patients"]
    ctx = await make_tool_context(me, request_id="test-request-panel-rows")

    result = await mcp_server.call_tool("get_my_recent_patients", {"days": 30, "limit": 50}, ctx)
    rows = {row["patient_id"]: row for row in await _panel_rows(result)}
    alpha = rows[str(patients["alpha"])]
    assert set(alpha) == PANEL_ROW_KEYS
    assert alpha["display_name"].startswith("Panel Alpha ")
    assert alpha["mrn"].startswith("DEMO-")
    assert alpha["age_years"] == 46  # dob 1980-01-15 at the frozen 2026-10-27
    assert alpha["sex"] == "female"
    assert alpha["blood_type"] == "A+"
    assert alpha["last_encounter_at"] == "2026-10-24T12:00:00Z"
    assert alpha["weight_kg"] == "70.50"  # latest recorded vitals snapshot
    assert alpha["symptoms"] == "fatigue and cold intolerance"
    assert alpha["diagnoses"] == ["Type 2 diabetes mellitus", "Iron deficiency anemia"]
    assert alpha["key_results"] == [
        "K 5.86 mmol/L high (2026-10-24)",
        "GLU 5.5 mg/dL (2026-10-24)",
    ]
    assert alpha["proposed_plan"] == "start levothyroxine 50 mcg daily; recheck TSH in 6 weeks"
    assert alpha["prescribed_treatment"] == ["Metformin 500 mg oral twice daily"]

    # beta: long note text is bounded with the explicit marker.
    beta = rows[str(patients["beta"])]
    assert beta["symptoms"].endswith(TRUNCATION_MARKER)
    assert len(beta["symptoms"]) <= PANEL_ROW_TEXT_MAX_LENGTH

    # limit bounds the rows; total counts every visible patient (so the model
    # can say "showing 2 of 5"); concealed rows never occupy slots.
    limited = await mcp_server.call_tool("get_my_recent_patients", {"days": 30, "limit": 2}, ctx)
    rows = await _panel_rows(limited)
    assert [row["patient_id"] for row in rows] == [str(patients["gamma"]), str(patients["alpha"])]
    assert limited.model_payload["data"]["total"] == 5
    assert limited.model_payload["data"]["note"] == "Showing 2 of 5 patients."

    single = await mcp_server.call_tool("get_my_recent_patients", {"days": 30, "limit": 1}, ctx)
    rows = await _panel_rows(single)
    # The concealed patient's -1d encounter is newer than gamma's, but it is
    # soft-deleted: it must never appear nor take the only slot.
    assert [row["patient_id"] for row in rows] == [str(patients["gamma"])]
    assert single.model_payload["data"]["total"] == 5
    assert f"Panel Gone {patients['gone']}" not in json.dumps(single.model_payload)

    # Citations: the patient + representing encounter + the note/lab/prescription
    # refs actually used in the rows.
    citation_ids = {(c["type"], c["id"]) for c in result.citations}
    assert ("patient", str(patients["alpha"])) in citation_ids
    assert ("encounter", str(data["encounters"]["alpha_recent"])) in citation_ids
    assert ("clinical_note", str(data["note_alpha"])) in citation_ids
    assert ("lab_result", str(data["lab_k"])) in citation_ids
    assert ("prescription", str(data["rx_alpha"])) in citation_ids

    # Audit: patient_panel / resource_id None; no names or payloads ever.
    rows_audit = await audit_rows(
        action="mcp.get_my_recent_patients", outcome="success", request_id="test-request-panel-rows"
    )
    assert rows_audit
    for row in rows_audit:
        assert row.resource_type == "patient_panel"
        assert row.resource_id is None
        text = repr(row)
        assert "Panel Alpha" not in text and "Metformin" not in text
        assert str(patients["alpha"]) not in text


@pytest.mark.postgres
async def test_summary_extension_fields_are_typed_and_additive(
    db_engine, make_user, make_tool_context, freeze_panel_clock
) -> None:
    """get_patient_summary: every pre-existing section kept, new fields typed."""
    from app.mcp.server import mcp_server

    me = await make_user(roles=("doctor",))
    other = await make_user(roles=("doctor",))
    data = await _add_panel_data(db_engine, me_id=me.id, other_id=other.id, token=uuid.uuid4().hex[:8])
    ctx = await make_tool_context(me, request_id="test-request-panel-summary")
    patient_id = str(data["patients"]["alpha"])

    result = await mcp_server.call_tool("get_patient_summary", {"patient_id": patient_id}, ctx)
    assert result.ok is True
    payload = result.model_payload["data"]

    # Existing sections are kept (additive-only extension).
    assert {"patient", "encounters", "active_problems", "latest_labs", "active_prescriptions"} <= set(payload)
    assert payload["patient"]["id"] == patient_id
    assert payload["patient"]["provenance"] == "synthetic"
    assert "created_at" not in payload["patient"]

    # New fields: typed and correct against the fixture.
    assert payload["patient"]["blood_type"] == "A+"
    assert payload["patient"]["age_years"] == 46
    assert payload["vitals"] == {
        "weight_kg": "70.50",
        "height_cm": "165.00",
        "measured_at": "2026-10-24T12:00:00Z",
        "previous_weight_kg": "72.00",
        "previous_measured_at": "2026-10-07T12:00:00Z",
        "provenance": "synthetic",
    }
    # Per-visit vitals also ride on the encounters (trend across visits).
    by_id = {item["id"]: item for item in payload["encounters"]}
    assert by_id[str(data["encounters"]["alpha_recent"])]["weight_kg"] == "70.50"
    assert by_id[str(data["encounters"]["alpha_old"])]["weight_kg"] == "72.00"

    assert [item["name"] for item in payload["diagnoses"]] == [
        "Type 2 diabetes mellitus",
        "Iron deficiency anemia",
    ]
    assert payload["diagnoses"][0] == {
        "code": "E11.9",
        "name": "Type 2 diabetes mellitus",
        "status": "active",
        "onset_date": "2024-05-01",
        "provenance": "synthetic",
    }
    assert [item["display_name"] for item in payload["active_problems"]] == ["Type 2 diabetes mellitus"]

    assert payload["symptoms"] == "fatigue and cold intolerance"
    assert payload["assessment"] == "hypothyroidism"
    assert payload["proposed_plan"] == "start levothyroxine 50 mcg daily; recheck TSH in 6 weeks"

    treatment = payload["prescribed_treatment"]
    assert len(treatment) == 1
    assert treatment[0]["medication"] == "Metformin"
    assert treatment[0]["dose_amount"] == "500.000"
    assert treatment[0]["dose_unit"] == "mg"
    assert treatment[0]["route"] == "oral"
    assert treatment[0]["frequency"] == "twice daily"
    assert treatment[0]["items"] == [
        {
            "sequence_no": 1,
            "medication": "Metformin",
            "dose_amount": "500.000",
            "dose_unit": "mg",
            "provenance": "synthetic",
        }
    ]
    # Raw prescriptions keep their own items (what was actually prescribed).
    assert payload["active_prescriptions"][0]["items"][0]["sequence_no"] == 1

    # Absence is real: this patient has no vitals, note, or diagnoses.
    empty = await mcp_server.call_tool(
        "get_patient_summary", {"patient_id": str(data["patients"]["delta"])}, ctx
    )
    empty_payload = empty.model_payload["data"]
    assert empty_payload["vitals"] is None
    assert empty_payload["diagnoses"] == []
    assert empty_payload["symptoms"] is None
    assert empty_payload["prescribed_treatment"] == []

    citation_ids = {(c["type"], c["id"]) for c in result.citations}
    assert ("clinical_note", str(data["note_alpha"])) in citation_ids
