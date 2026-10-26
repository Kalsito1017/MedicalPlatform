"""The four read-only MCP tools (docs/API.md "MCP tool contract").

Exactly four tools exist; there is no `execute_sql`, shell, filesystem, or
general-purpose database tool and none may be added. Every tool:

  * takes its arguments as typed, bounded values and a server-injected
    `ToolContext` (never an identity tool argument),
  * denies and audits when called without a valid ToolContext,
  * re-executes authorization on every call — the same
    `app.authorization.policy` permission checks and the same
    `app.services.access.check_patient_access` seam as the REST API,
  * reads data only through `app.services` (parameterized ORM; this module
    writes no SQL),
  * returns the minimum data needed (shared `app.schemas.patient` shapes,
    timestamps excluded), with large text fields truncated and an explicit
    `[truncated]` marker,
  * writes exactly one `mcp.<tool_name>` audit event per call (outcome
    success | denied | error) with a resource reference and request id —
    never a clinical payload, prompt, or search term.

Tool results are envelopes consumed by app/mcp/server.py: `ok`/`data`/`error`
go back to the model, `citations` feed the API response, and `audit` names the
resource reference. Concealment semantics match the REST API: a patient the
caller may not read is indistinguishable from a missing one (`not_found`).
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import Field

from app.audit.logger import record_audit_event
from app.authorization.policy import has_permission
from app.mcp.context import ToolContext, is_valid_context
from app.mcp.limits import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    LIST_LIMIT_MIN,
    PATIENT_ID_MAX_LENGTH,
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    SEARCH_LIMIT_MIN,
    SEARCH_QUERY_MAX_LENGTH,
    SUMMARY_LIMIT_DEFAULT,
    SUMMARY_LIMIT_MAX,
    SUMMARY_LIMIT_MIN,
    TEXT_FIELD_MAX_LENGTH,
    TRUNCATION_MARKER,
)
from app.schemas.patient import (
    EncounterOut,
    LabResultOut,
    PatientOut,
    PrescriptionOut,
)
from app.services import labs as labs_service
from app.services import patients as patients_service
from app.services import prescriptions as prescriptions_service
from app.services.access import check_patient_access

PATIENT_FIELDS = {"id", "mrn", "display_name", "date_of_birth", "sex", "provenance"}
ENCOUNTER_FIELDS = {"id", "patient_id", "occurred_at", "encounter_type", "summary", "provenance"}
LAB_FIELDS = {
    "id",
    "patient_id",
    "encounter_id",
    "test_code",
    "test_name",
    "value_num",
    "value_text",
    "unit",
    "ref_low",
    "ref_high",
    "status",
    "collected_at",
    "provenance",
}
PRESCRIPTION_FIELDS = {
    "id",
    "patient_id",
    "encounter_id",
    "medication_name",
    "dose_amount",
    "dose_unit",
    "route",
    "frequency",
    "status",
    "start_date",
    "end_date",
    "provenance",
}


def truncate_text(value: str, max_length: int = TEXT_FIELD_MAX_LENGTH) -> str:
    """Bound a text field; over-long values end with an explicit marker."""
    if len(value) <= max_length:
        return value
    return value[: max_length - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _dump(schema: type[Any], row: Any, fields: set[str]) -> dict[str, Any]:
    data = schema.model_validate(row).model_dump(mode="json", include=fields)
    if "summary" in data and isinstance(data["summary"], str):
        data["summary"] = truncate_text(data["summary"])
    return data


def _citation(resource_type: str, resource_id: Any) -> dict[str, str]:
    return {"type": resource_type, "id": str(resource_id)}


def _envelope(
    *,
    ok: bool,
    data: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
    citations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {"ok": ok, "data": data, "error": error, "citations": citations or []}


async def _finish(
    tool_name: str,
    tool_context: ToolContext | None,
    *,
    outcome: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Audit one `mcp.<tool_name>` event (self-committing) and build the result."""
    valid = is_valid_context(tool_context)
    await record_audit_event(
        actor_user_id=tool_context.principal.user_uuid if valid else None,
        action=f"mcp.{tool_name}",
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=tool_context.request_id if valid else None,
        ip=tool_context.ip if valid else None,
    )
    envelope = _envelope(**kwargs)
    envelope["outcome"] = outcome  # the audited outcome (not model-visible)
    return envelope


async def _denied_context(tool_name: str) -> dict[str, Any]:
    """No valid server-injected context -> deny by default, and audit the attempt."""
    return await _finish(
        tool_name,
        None,
        outcome="denied",
        ok=False,
        error={
            "code": "forbidden",
            "message": "This tool requires a verified caller context; the call was denied.",
        },
    )


def _parse_patient_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


async def _authorize_patient(
    tool_name: str,
    ctx: ToolContext,
    patient_id: str,
    permission: str,
) -> tuple[uuid.UUID | None, dict[str, Any] | None]:
    """Shared authorization path. Returns (patient_uuid, None) or (None, deny envelope)."""
    pid = _parse_patient_id(patient_id)
    resource_id = str(pid) if pid is not None else None
    if not has_permission(ctx.principal.roles, permission):
        return None, await _finish(
            tool_name,
            ctx,
            outcome="denied",
            resource_type="patient",
            resource_id=resource_id,
            ok=False,
            error={"code": "forbidden", "message": "You are not permitted to read this data."},
        )
    if pid is None or not await check_patient_access(ctx.principal, pid, db=ctx.db):
        return None, await _finish(
            tool_name,
            ctx,
            outcome="denied",
            resource_type="patient",
            resource_id=resource_id,
            ok=False,
            error={
                "code": "not_found",
                "message": "No accessible patient record matches that id.",
            },
        )
    return pid, None


async def search_authorized_patients(
    query: Annotated[str, Field(max_length=SEARCH_QUERY_MAX_LENGTH, description="Case-insensitive substring match on the synthetic display name or MRN. Empty string returns any patients.")],
    limit: Annotated[int, Field(ge=SEARCH_LIMIT_MIN, le=SEARCH_LIMIT_MAX, description="Maximum patients to return (1-25).")] = SEARCH_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search the SYNTHETIC demo patients the current user is authorized to read.

    Returns only patients the caller may see (id, MRN, display name, date of
    birth, sex). Callers without `patient:read` see an empty result. Never
    returns other record types.
    """
    name = "search_authorized_patients"
    if not is_valid_context(tool_context):
        return await _denied_context(name)
    ctx = tool_context
    if not has_permission(ctx.principal.roles, "patient:read"):
        return await _finish(
            name,
            ctx,
            outcome="denied",
            resource_type="patient",
            ok=True,
            data={
                "patients": [],
                "total": 0,
                "note": "No patients are visible to your account.",
            },
        )
    term = query.strip() or None
    rows, total = await patients_service.list_patients(ctx.db, q=term, limit=limit, offset=0)
    return await _finish(
        name,
        ctx,
        outcome="success",
        resource_type="patient",
        ok=True,
        data={
            "patients": [_dump(PatientOut, row, PATIENT_FIELDS) for row in rows],
            "total": total,
        },
        citations=[_citation("patient", row.id) for row in rows],
    )


async def get_patient_summary(
    patient_id: Annotated[str, Field(max_length=PATIENT_ID_MAX_LENGTH, description="Opaque patient UUID as returned by search_authorized_patients.")],
    limit: Annotated[int, Field(ge=SUMMARY_LIMIT_MIN, le=SUMMARY_LIMIT_MAX, description="Maximum recent encounters to include (1-25).")] = SUMMARY_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Get the SYNTHETIC summary for one patient the current user may read.

    Includes demographics, recent encounters (summaries may be truncated),
    latest lab results per test, and active prescriptions. Missing sections are
    returned as empty lists - absence of data is real and must be reported as
    absent, never invented.
    """
    name = "get_patient_summary"
    if not is_valid_context(tool_context):
        return await _denied_context(name)
    ctx = tool_context
    pid, deny = await _authorize_patient(name, ctx, patient_id, "patient:read")
    if deny is not None:
        return deny
    patient = await patients_service.get_patient(ctx.db, pid)
    if patient is None:
        return await _finish(
            name,
            ctx,
            outcome="denied",
            resource_type="patient",
            resource_id=str(pid),
            ok=False,
            error={"code": "not_found", "message": "No accessible patient record matches that id."},
        )
    encounters = await patients_service.list_encounters(ctx.db, pid, limit=limit)
    latest_labs = await patients_service.list_latest_labs(ctx.db, pid)
    active_prescriptions = await patients_service.list_active_prescriptions(ctx.db, pid)
    citations = [_citation("patient", patient.id)]
    citations += [_citation("encounter", row.id) for row in encounters]
    citations += [_citation("lab_result", row.id) for row in latest_labs]
    citations += [_citation("prescription", row.id) for row in active_prescriptions]
    return await _finish(
        name,
        ctx,
        outcome="success",
        resource_type="patient",
        resource_id=str(pid),
        ok=True,
        data={
            "patient": _dump(PatientOut, patient, PATIENT_FIELDS),
            "encounters": [_dump(EncounterOut, row, ENCOUNTER_FIELDS) for row in encounters],
            "active_problems": [],
            "latest_labs": [_dump(LabResultOut, row, LAB_FIELDS) for row in latest_labs],
            "active_prescriptions": [
                _dump(PrescriptionOut, row, PRESCRIPTION_FIELDS) for row in active_prescriptions
            ],
        },
        citations=citations,
    )


async def get_patient_lab_results(
    patient_id: Annotated[str, Field(max_length=PATIENT_ID_MAX_LENGTH, description="Opaque patient UUID as returned by search_authorized_patients.")],
    limit: Annotated[int, Field(ge=LIST_LIMIT_MIN, le=LIST_LIMIT_MAX, description="Maximum lab results to return (1-50).")] = LIST_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """List recent SYNTHETIC lab results for one patient the current user may read.

    Most recently collected first (test code, name, value, unit, reference
    range, status, collected time). Requires `lab:read`. Missing results are
    real absences and must be reported as absent, never invented.
    """
    name = "get_patient_lab_results"
    if not is_valid_context(tool_context):
        return await _denied_context(name)
    ctx = tool_context
    pid, deny = await _authorize_patient(name, ctx, patient_id, "lab:read")
    if deny is not None:
        return deny
    rows, total = await labs_service.list_lab_results(ctx.db, pid, limit=limit, offset=0)
    return await _finish(
        name,
        ctx,
        outcome="success",
        resource_type="patient",
        resource_id=str(pid),
        ok=True,
        data={
            "lab_results": [_dump(LabResultOut, row, LAB_FIELDS) for row in rows],
            "total": total,
        },
        citations=[_citation("lab_result", row.id) for row in rows],
    )


async def get_recent_prescriptions(
    patient_id: Annotated[str, Field(max_length=PATIENT_ID_MAX_LENGTH, description="Opaque patient UUID as returned by search_authorized_patients.")],
    limit: Annotated[int, Field(ge=LIST_LIMIT_MIN, le=LIST_LIMIT_MAX, description="Maximum prescriptions to return (1-50).")] = LIST_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """List recent SYNTHETIC prescriptions for one patient the current user may read.

    Newest start date first (medication, dose, route, frequency, status, start
    and end dates). Requires `prescription:read`. Missing prescriptions are
    real absences and must be reported as absent, never invented.
    """
    name = "get_recent_prescriptions"
    if not is_valid_context(tool_context):
        return await _denied_context(name)
    ctx = tool_context
    pid, deny = await _authorize_patient(name, ctx, patient_id, "prescription:read")
    if deny is not None:
        return deny
    rows, total = await prescriptions_service.list_prescriptions(ctx.db, pid, limit=limit, offset=0)
    return await _finish(
        name,
        ctx,
        outcome="success",
        resource_type="patient",
        resource_id=str(pid),
        ok=True,
        data={
            "prescriptions": [_dump(PrescriptionOut, row, PRESCRIPTION_FIELDS) for row in rows],
            "total": total,
        },
        citations=[_citation("prescription", row.id) for row in rows],
    )
