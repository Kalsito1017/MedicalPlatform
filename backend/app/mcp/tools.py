"""The seven read-only MCP tools (docs/API.md "MCP tool contract").

Exactly seven tools exist; there is no `execute_sql`, shell, filesystem, or
general-purpose database tool and none may be added. Every tool:

  * takes its arguments as typed, bounded values and a server-injected
    `ToolContext` (never an identity tool argument),
  * denies and audits when called without a valid ToolContext,
  * re-executes authorization on every call — the same
    `app.authorization.policy` permission checks and the same
    `app.services.access.check_patient_access` seam as the REST API,
  * reads data only through parameterized ORM queries (per-patient reads via
    `app.services`; the two cross-patient lookup tools run their own bounded
    ORM queries on the ToolContext session because the services expose no
    cross-patient queries — this module still writes no SQL strings),
  * returns the minimum data needed (shared `app.schemas.patient` shapes,
    timestamps excluded), with large text fields truncated and an explicit
    `[truncated]` marker,
  * writes exactly one `mcp.<tool_name>` audit event per call (outcome
    success | denied | error) with a resource reference and request id —
    never a clinical payload, prompt, or search term.

The two lookup tools (`find_patients_by_lab`, `find_patients_by_medication`)
return ONLY patient references (patient id, display name, MRN) plus a short
`matched` reason string - never a full clinical payload. The panel tool
(`get_my_recent_patients`) returns one bounded compact row per patient for the
caller's own recent patients ("went through me"; identity from the ToolContext,
never an argument).

Tool results are envelopes consumed by app/mcp/server.py: `ok`/`data`/`error`
go back to the model, `citations` feed the API response, and `audit` names the
resource reference. Concealment semantics match the REST API: a patient the
caller may not read is indistinguishable from a missing one (`not_found`), and
lookup rows for such patients are silently dropped (never listed).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import and_, func, or_, select

from app.audit.logger import record_audit_event
from app.authorization.policy import has_permission
from app.mcp.context import ToolContext, is_valid_context
from app.mcp.limits import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    LIST_LIMIT_MIN,
    LOOKUP_CATALOG_MAX_CODES,
    LOOKUP_LIMIT_DEFAULT,
    LOOKUP_LIMIT_MAX,
    LOOKUP_LIMIT_MIN,
    LOOKUP_MATCHED_MAX_LENGTH,
    LOOKUP_MAX_SCAN,
    MEDICATION_NAME_MAX_LENGTH,
    PATIENT_ID_MAX_LENGTH,
    PANEL_CANDIDATE_SCAN_LIMIT,
    PANEL_DAYS_DEFAULT,
    PANEL_DAYS_MAX,
    PANEL_DAYS_MIN,
    PANEL_DIAGNOSIS_MAX,
    PANEL_ITEM_MAX_LENGTH,
    PANEL_KEY_RESULTS_MAX,
    PANEL_LIMIT_DEFAULT,
    PANEL_LIMIT_MAX,
    PANEL_LIMIT_MIN,
    PANEL_ROW_TEXT_MAX_LENGTH,
    PANEL_TREATMENT_MAX,
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    SEARCH_LIMIT_MIN,
    SEARCH_QUERY_MAX_LENGTH,
    SUMMARY_DIAGNOSIS_MAX,
    SUMMARY_LIMIT_DEFAULT,
    SUMMARY_LIMIT_MAX,
    SUMMARY_LIMIT_MIN,
    SUMMARY_PRESCRIPTION_ITEMS_MAX,
    TEST_CODE_MAX_LENGTH,
    TEXT_FIELD_MAX_LENGTH,
    TRUNCATION_MARKER,
)
from app.models.lab import LabResult
from app.models.medication import Medication, Prescription
from app.models.patient import Diagnosis, Encounter, Patient
from app.schemas.patient import (
    DiagnosisOut,
    EncounterOut,
    LabResultOut,
    PatientOut,
    PrescriptionOut,
    ProblemOut,
)
from app.services import labs as labs_service
from app.services import patients as patients_service
from app.services import prescriptions as prescriptions_service
from app.services.access import check_patient_access

PATIENT_FIELDS = {
    "id",
    "mrn",
    "display_name",
    "date_of_birth",
    "sex",
    "blood_type",
    "age_years",
}
ENCOUNTER_FIELDS = {
    "id",
    "patient_id",
    "occurred_at",
    "encounter_type",
    "summary",
    "weight_kg",
    "height_cm",
}
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
    "items",
}
DIAGNOSIS_FIELDS = {"code", "name", "status", "onset_date"}
PROBLEM_FIELDS = {"code", "display_name", "status", "onset_date"}


def truncate_text(value: str, max_length: int = TEXT_FIELD_MAX_LENGTH) -> str:
    """Bound a text field; over-long values end with an explicit marker."""
    if len(value) <= max_length:
        return value
    return value[: max_length - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _truncate_strings(value: Any, max_length: int = TEXT_FIELD_MAX_LENGTH) -> Any:
    """Bound every string in a serialized payload (recursively)."""
    if isinstance(value, str):
        return truncate_text(value, max_length)
    if isinstance(value, dict):
        return {key: _truncate_strings(item, max_length) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_strings(item, max_length) for item in value]
    return value


def _dump(schema: type[Any], row: Any, fields: set[str]) -> dict[str, Any]:
    """Serialize one row to the bounded tool shape (all strings truncated)."""
    data = schema.model_validate(row).model_dump(mode="json", include=fields)
    return _truncate_strings(data)


def _iso_utc(value: datetime) -> str:
    """Contract timestamp: ISO-8601 UTC with a 'Z' suffix (docs/API.md)."""
    return value.isoformat().replace("+00:00", "Z")


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


# --- summary/panel row formatters -----------------------------------------
#
# Every string these produce is bounded (truncate_text) with an explicit
# `[truncated]` marker; numeric values serialize as Decimal strings.


def _problem_from_diagnosis(diagnosis: Diagnosis) -> ProblemOut:
    """Map a diagnosis row onto the summary's active-problem shape."""
    return ProblemOut(
        code=diagnosis.code,
        display_name=diagnosis.name,
        status=diagnosis.status,
        onset_date=diagnosis.onset_date,
    )


def _vitals_dict(encounters: list[Encounter]) -> dict[str, Any] | None:
    """Latest per-visit vitals + previous weight from newest-first encounters."""
    measured = [row for row in encounters if row.weight_kg is not None]
    if not measured:
        return None
    latest = measured[0]
    previous = measured[1] if len(measured) > 1 else None
    return {
        "weight_kg": str(latest.weight_kg),
        "height_cm": str(latest.height_cm) if latest.height_cm is not None else None,
        "measured_at": _iso_utc(latest.occurred_at),
        "previous_weight_kg": str(previous.weight_kg) if previous is not None else None,
        "previous_measured_at": _iso_utc(previous.occurred_at) if previous is not None else None,
    }


def _treatment_dict(rx: Prescription) -> dict[str, Any]:
    """One active prescription rendered as prescribed treatment (bounded)."""
    items = [
        {
            "sequence_no": item.sequence_no,
            "medication": truncate_text(str(item.medication_name)),
            "dose_amount": str(item.dose_amount),
            "dose_unit": truncate_text(str(item.dose_unit)),
        }
        for item in (getattr(rx, "items", None) or [])[:SUMMARY_PRESCRIPTION_ITEMS_MAX]
    ]
    return {
        "prescription_id": str(rx.id),
        "medication": truncate_text(str(rx.medication_name)),
        "dose_amount": str(rx.dose_amount) if rx.dose_amount is not None else None,
        "dose_unit": truncate_text(str(rx.dose_unit)),
        "route": truncate_text(str(rx.route)),
        "frequency": truncate_text(str(rx.frequency)),
        "items": items,
    }


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
    query: Annotated[str, Field(max_length=SEARCH_QUERY_MAX_LENGTH, description="Case-insensitive substring match on the display name or MRN. Empty string returns any patients.")],
    limit: Annotated[int, Field(ge=SEARCH_LIMIT_MIN, le=SEARCH_LIMIT_MAX, description="Maximum patients to return (1-25).")] = SEARCH_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search the patients the current user is authorized to read.

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
    """Get the clinical summary for one patient the current user may read.

    Includes demographics (blood type, derived age), recent encounters with the
    per-visit vitals snapshot (weight/height, previous weight for trend),
    problem-list diagnoses (active first), the latest clinical note's symptoms /
    assessment / proposed plan, prescribed treatment (active prescriptions with
    their items), latest lab results per test, and active prescriptions.
    Missing sections are returned as empty lists or null - absence of data is
    real and must be reported as absent, never invented. Proposed treatment is
    what the RECORD says was proposed - never advice.
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
    diagnoses: list[Diagnosis] = list(getattr(patient, "diagnoses", []) or [])
    latest_note = getattr(patient, "latest_note", None)
    citations = [_citation("patient", patient.id)]
    citations += [_citation("encounter", row.id) for row in encounters]
    if latest_note is not None:
        citations.append(_citation("clinical_note", latest_note.id))
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
            "vitals": _vitals_dict(encounters),
            "diagnoses": [
                _dump(DiagnosisOut, row, DIAGNOSIS_FIELDS) for row in diagnoses[:SUMMARY_DIAGNOSIS_MAX]
            ],
            "active_problems": [
                _dump(ProblemOut, _problem_from_diagnosis(row), PROBLEM_FIELDS)
                for row in diagnoses
                if row.status == "active"
            ],
            "symptoms": truncate_text(latest_note.symptoms) if latest_note is not None else None,
            "assessment": truncate_text(latest_note.assessment) if latest_note is not None else None,
            "proposed_plan": truncate_text(latest_note.proposed_plan) if latest_note is not None else None,
            "prescribed_treatment": [
                _treatment_dict(row) for row in active_prescriptions
            ],
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
    """List recent lab results for one patient the current user may read.

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
    """List recent prescriptions for one patient the current user may read.

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


# --- practitioner panel tool (get_my_recent_patients) ----------------------
#
# The panel roundup ("which patients went through me in the last N days").
# Decisions (docs/API.md "MCP tool contract"):
#   * "me" is the verified principal in the injected ToolContext
#     (`principal.user_uuid`) - NEVER a tool argument; the LLM-facing schema
#     exposes only `days` and `limit`,
#   * "went through me" = within the UTC window [now - days, now] (inclusive
#     both ends, one freezeable clock: `app.services.patients.utc_now`) a
#     patient had (a) PRIMARY - an encounter whose `practitioner_id` is the
#     caller, or (b) UNION - an active `patient_assignment` to the caller
#     (ended_at NULL) AND any encounter in the window. When both match, (a)
#     wins: the patient is represented by their most recent encounter WITH the
#     caller (that encounter is cited and stamped as `last_encounter_at`);
#     assignment-only patients are represented by their most recent in-window
#     encounter. Rows are ordered by that representing encounter, newest first.
#   * row content answers "their results, symptoms, weight, blood category,
#     age, proposed treatment, prescribed treatment": weight is the most
#     recent recorded vitals snapshot, symptoms/proposed plan come from the
#     most recent clinical note, key results are the most recent result per
#     test code with abnormal ones first, prescribed treatment is the active
#     prescriptions (what the RECORD says - never advice),
#   * `limit` clamps 1..50 and `days` clamps 1..365 (schema-bounded; out of
#     range is a controlled `invalid_arguments` at the registry, and direct
#     invocation additionally clamps as defense in depth),
#   * visibility: `patient:read` is checked once per call (deny -> empty result
#     with a note, outcome `denied`), then the SAME `check_patient_access` seam
#     as the REST API runs per candidate row; non-visible rows are dropped and
#     paging continues past them within a bounded candidate scan, so concealed
#     rows never crowd out visible ones. The "mine" filter only ever NARROWS
#     visibility. `total` counts only rows the caller may see,
#   * every string in a row is bounded with `[truncated]`; audit action
#     `mcp.get_my_recent_patients`, resource_type `patient_panel`,
#     resource_id None (never search terms or clinical payloads).


def _key_result_line(lab: LabResult) -> str:
    """One key-result string: "K 5.86 mmol/L high (2026-09-24)" (bounded)."""
    if lab.value_num is not None:
        value = _fmt_decimal(lab.value_num)
    else:
        value = truncate_text(str(lab.value_text or ""), PANEL_ITEM_MAX_LENGTH // 2)
    unit = f" {lab.unit}" if lab.unit else ""
    direction = ""
    if lab.value_num is not None and lab.ref_high is not None and lab.value_num > lab.ref_high:
        direction = " high"
    elif lab.value_num is not None and lab.ref_low is not None and lab.value_num < lab.ref_low:
        direction = " low"
    when = lab.collected_at.date().isoformat() if lab.collected_at else "unknown date"
    return truncate_text(f"{lab.test_code} {value}{unit}{direction} ({when})", PANEL_ITEM_MAX_LENGTH)


def _treatment_line(rx: Prescription) -> str:
    """One prescribed-treatment string: "Metformin 500 mg oral twice daily"."""
    if rx.dose_amount is not None:
        dose = f"{_fmt_decimal(rx.dose_amount)} {rx.dose_unit}"
    else:
        dose = str(rx.dose_unit or "")
    return truncate_text(
        f"{rx.medication_name} {dose} {rx.route} {rx.frequency}".strip(), PANEL_ITEM_MAX_LENGTH
    )


def _panel_row(patient: Patient, encounter: Encounter, details: dict[str, Any]) -> dict[str, Any]:
    """One compact, fully bounded panel row (see the section comment)."""
    note = details.get("latest_note")
    vitals = details.get("last_vitals")
    weight_kg = None
    if vitals is not None and vitals.weight_kg is not None:
        weight_kg = str(vitals.weight_kg)
    return {
        "patient_id": str(patient.id),
        "display_name": truncate_text(str(patient.display_name)),
        "mrn": truncate_text(str(patient.mrn)),
        "age_years": patients_service.age_in_years(patient.date_of_birth),
        "sex": str(patient.sex),
        "blood_type": patient.blood_type,
        "last_encounter_at": _iso_utc(encounter.occurred_at),
        "weight_kg": weight_kg,
        "symptoms": (
            truncate_text(str(note.symptoms), PANEL_ROW_TEXT_MAX_LENGTH) if note is not None else None
        ),
        "diagnoses": [
            truncate_text(str(row.name), PANEL_ITEM_MAX_LENGTH)
            for row in details.get("diagnoses", [])[:PANEL_DIAGNOSIS_MAX]
        ],
        "key_results": [
            _key_result_line(row) for row in details.get("key_labs", [])[:PANEL_KEY_RESULTS_MAX]
        ],
        "proposed_plan": (
            truncate_text(str(note.proposed_plan), PANEL_ROW_TEXT_MAX_LENGTH)
            if note is not None
            else None
        ),
        "prescribed_treatment": [
            _treatment_line(row)
            for row in details.get("active_prescriptions", [])[:PANEL_TREATMENT_MAX]
        ],
    }


async def get_my_recent_patients(
    days: Annotated[int, Field(ge=PANEL_DAYS_MIN, le=PANEL_DAYS_MAX, description="Look-back window in days counted back from now, UTC, inclusive (1-365).")] = PANEL_DAYS_DEFAULT,
    limit: Annotated[int, Field(ge=PANEL_LIMIT_MIN, le=PANEL_LIMIT_MAX, description="Maximum patient rows to return (1-50); `total` always reports the full visible count.")] = PANEL_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """List the patients that went through YOU in the last N days.

    "You" is the verified caller (never an argument). A patient is listed when,
    within the window, they had an encounter with you (practitioner match - the
    primary meaning) or you hold an active care assignment for them and they
    had any encounter in the window. Returns one compact row per patient, most
    recent first: demographics, age (derived), sex, blood type, weight, last
    encounter time, symptoms, diagnosis names, key lab results (abnormal
    first), proposed plan, and prescribed treatment - every string bounded.
    Requires `patient:read`; patients you may not read are never listed. Absent
    values are real absences and must be reported as absent, never invented.
    """
    name = "get_my_recent_patients"
    if not is_valid_context(tool_context):
        return await _finish(
            name,
            None,
            outcome="denied",
            resource_type="patient_panel",
            ok=False,
            error={
                "code": "forbidden",
                "message": "This tool requires a verified caller context; the call was denied.",
            },
        )
    ctx = tool_context
    if not has_permission(ctx.principal.roles, "patient:read"):
        return await _finish(
            name,
            ctx,
            outcome="denied",
            resource_type="patient_panel",
            ok=True,
            data={
                "patients": [],
                "total": 0,
                "note": "No patients are visible to your account.",
            },
        )
    me = ctx.principal.user_uuid
    if me is None:
        return await _finish(
            name,
            ctx,
            outcome="denied",
            resource_type="patient_panel",
            ok=False,
            error={
                "code": "forbidden",
                "message": "This tool requires a verified caller context; the call was denied.",
            },
        )
    bound_days = max(PANEL_DAYS_MIN, min(days, PANEL_DAYS_MAX))
    bound = max(PANEL_LIMIT_MIN, min(limit, PANEL_LIMIT_MAX))
    now = patients_service.utc_now()
    since = now - timedelta(days=bound_days)

    async def fetch_page(page_limit: int, offset: int) -> list[tuple[Encounter, Patient]]:
        return await patients_service.list_panel_candidates(
            ctx.db,
            practitioner_id=me,
            since=since,
            until=now,
            limit=page_limit,
            offset=offset,
        )

    visible = await _visible_pairs(ctx, fetch_page, PANEL_CANDIDATE_SCAN_LIMIT)
    total = len(visible)
    page = visible[:bound]
    details = await patients_service.load_panel_details(
        ctx.db, [patient.id for _encounter, patient in page]
    )
    rows = [_panel_row(patient, encounter, details[patient.id]) for encounter, patient in page]
    citations: list[dict[str, str]] = []
    for encounter, patient in page:
        bucket = details[patient.id]
        citations.append(_citation("patient", patient.id))
        citations.append(_citation("encounter", encounter.id))
        note = bucket.get("latest_note")
        if note is not None:
            citations.append(_citation("clinical_note", note.id))
        citations += [
            _citation("lab_result", row.id) for row in bucket.get("key_labs", [])[:PANEL_KEY_RESULTS_MAX]
        ]
        citations += [
            _citation("prescription", row.id)
            for row in bucket.get("active_prescriptions", [])[:PANEL_TREATMENT_MAX]
        ]
    data: dict[str, Any] = {
        "window_days": bound_days,
        "window_start": _iso_utc(since),
        "patients": rows,
        "total": total,
    }
    if total == 0:
        data["note"] = f"No patients went through you in the last {bound_days} days."
    elif total > len(rows):
        data["note"] = f"Showing {len(rows)} of {total} patients."
    return await _finish(
        name,
        ctx,
        outcome="success",
        resource_type="patient_panel",
        ok=True,
        data=data,
        citations=citations,
    )


# --- cross-patient lookup tools (general mode) -----------------------------
#
# Both tools return ONLY patient references plus a short `matched` reason.
# Decisions (docs/API.md "MCP tool contract"):
#   * abnormality is computed from value_num against ref_low/ref_high; the
#     `status` column is the result's document state (e.g. "final") and is NOT
#     an abnormality flag, so it is never used for filtering,
#   * one row per patient: the most recent matching result is shown and rows
#     are ordered by that result (most recent first),
#   * medication matching is a case-insensitive substring match on the
#     prescription's medication name (LIKE wildcards escaped to literal) or on
#     the linked catalog medication code,
#   * visibility: `patient:read` (plus `lab:read` / `prescription:read`) is
#     checked once per call, then the SAME `check_patient_access` seam as the
#     REST API runs per returned patient; non-visible rows are dropped and
#     paging continues past them (bounded candidate scan, one cheap indexed
#     seam check per candidate) so concealed rows never crowd out visible ones,
#   * `limit` is schema-bounded 1..50 (out-of-range is a controlled
#     `invalid_arguments` error via the registry) and additionally clamped
#     1..50 inside the tool as defense in depth for direct invocation,
#   * an unknown `test_code` is a controlled error that lists the known codes
#     (catalog data, never an echo of the submitted value) so the model can
#     correct itself; submitted values never appear in any error.


def _fmt_decimal(value: Decimal) -> str:
    """Compact decimal rendering ("6.100" -> "6.1", no exponent form)."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _lab_matched(lab: LabResult) -> str:
    """Short reason string for one lab result (bounded, marker on overflow)."""
    if lab.value_num is not None:
        value = _fmt_decimal(lab.value_num)
    else:
        value = truncate_text(str(lab.value_text or ""), LOOKUP_MATCHED_MAX_LENGTH // 2)
    unit = f" {lab.unit}" if lab.unit else ""
    direction = ""
    if lab.value_num is not None and lab.ref_high is not None and lab.value_num > lab.ref_high:
        direction = " high"
    elif lab.value_num is not None and lab.ref_low is not None and lab.value_num < lab.ref_low:
        direction = " low"
    when = lab.collected_at.date().isoformat() if lab.collected_at else "unknown date"
    return truncate_text(f"{lab.test_name} {value}{unit}{direction}, {when}", LOOKUP_MATCHED_MAX_LENGTH)


def _rx_matched(rx: Prescription) -> str:
    """Short reason string for one prescription (bounded, marker on overflow)."""
    if rx.dose_amount is not None:
        dose = f"{_fmt_decimal(rx.dose_amount)} {rx.dose_unit}"
    else:
        dose = str(rx.dose_unit or "")
    head = f"{rx.medication_name} {dose}".strip()
    return truncate_text(f"{head}, {rx.status}", LOOKUP_MATCHED_MAX_LENGTH)


async def _visible_pairs(
    ctx: ToolContext,
    fetch_page: Any,
    bound: int,
) -> list[tuple[Any, Any]]:
    """Candidate (row, patient) pairs the caller may see, bounded and paged.

    The visibility decision stays entirely with the `check_patient_access`
    seam (one cheap indexed check per candidate): non-visible rows are dropped
    and paging continues past them up to `LOOKUP_MAX_SCAN` candidates, so
    rows for concealed patients can never crowd out visible ones. At most
    `bound` rows are returned.
    """
    visible: list[tuple[Any, Any]] = []
    scanned = 0
    offset = 0
    while len(visible) < bound and scanned < LOOKUP_MAX_SCAN:
        page = await fetch_page(bound, offset)
        if not page:
            break
        for pair in page:
            scanned += 1
            _row, patient = pair
            if await check_patient_access(ctx.principal, patient.id, db=ctx.db):
                visible.append(pair)
                if len(visible) >= bound:
                    break
        if len(page) < bound:
            break
        offset += len(page)
    return visible


async def _test_code_is_known(db: Any, code: str) -> bool:
    """Exact-match allowlist check against the seeded lab catalog.

    There is no lab-test catalog table; the distinct codes of the seeded lab
    results ARE the catalog (scripts/seed_demo_data.py), so "known" means an
    exact, parameterized match on live results. No free text, no interpolation.
    """
    found = await db.scalar(
        select(LabResult.test_code)
        .where(LabResult.test_code == code, LabResult.not_deleted())
        .limit(1)
    )
    return found is not None


async def _known_test_code_hint(db: Any) -> list[tuple[str, str]]:
    """Best-effort bounded (code, name) hints for error messages, most used first.

    Only a hint (the catalog can exceed the bound): it helps the model map a
    name like "potassium" to a code like "K" after an unknown-code error.
    """
    rows = (
        await db.execute(
            select(LabResult.test_code, LabResult.test_name, func.count().label("uses"))
            .where(LabResult.not_deleted())
            .group_by(LabResult.test_code, LabResult.test_name)
            .order_by(func.count().desc(), LabResult.test_code)
            .limit(LOOKUP_CATALOG_MAX_CODES)
        )
    ).all()
    seen: dict[str, str] = {}
    for code, name, _uses in rows:
        seen.setdefault(str(code), str(name))
    return list(seen.items())


async def _lookup_permission_gate(
    tool_name: str, ctx: ToolContext, extra_permission: str
) -> dict[str, Any] | None:
    """Permission check for lookup tools; None means proceed.

    Requires `patient:read` AND the record-type permission (`lab:read` /
    `prescription:read`) because the `matched` reason carries record values.
    Mirrors search_authorized_patients: a caller without the permissions gets
    an empty result (outcome `denied`, audited) and learns nothing.
    """
    if has_permission(ctx.principal.roles, "patient:read") and has_permission(
        ctx.principal.roles, extra_permission
    ):
        return None
    return await _finish(
        tool_name,
        ctx,
        outcome="denied",
        resource_type="patient",
        ok=True,
        data={
            "patients": [],
            "note": "No patients are visible to your account.",
        },
    )


async def find_patients_by_lab(
    test_code: Annotated[str, Field(min_length=1, max_length=TEST_CODE_MAX_LENGTH, description="Known lab test code exactly as stored (e.g. K, GLU, HGB, TSH); unknown codes return a controlled error listing the known codes.")],
    abnormal_only: Annotated[bool, Field(description="Only results outside their reference range (value_num vs ref_low/ref_high). Default true.")] = True,
    limit: Annotated[int, Field(ge=LOOKUP_LIMIT_MIN, le=LOOKUP_LIMIT_MAX, description="Maximum patient references to return (1-50).")] = LOOKUP_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Find the patients with lab results for one known test code.

    Returns only patient references (patient id, display name, MRN) plus a
    short `matched` reason (most recent matching result first, one row per
    patient). With `abnormal_only` true only results outside their reference
    range match (numeric value against ref_low/ref_high; the `status` field is
    the result's document state and is never used). Requires `patient:read`
    and `lab:read`. Patients the caller may not read are never listed.
    """
    name = "find_patients_by_lab"
    if not is_valid_context(tool_context):
        return await _denied_context(name)
    ctx = tool_context
    deny = await _lookup_permission_gate(name, ctx, "lab:read")
    if deny is not None:
        return deny
    bound = max(LOOKUP_LIMIT_MIN, min(limit, LOOKUP_LIMIT_MAX))
    code = test_code.strip()

    known_hint = await _known_test_code_hint(ctx.db)
    if not await _test_code_is_known(ctx.db, code):
        hint = ", ".join(
            f"{c} ({truncate_text(n, 60)})"
            for c, n in known_hint[:LOOKUP_CATALOG_MAX_CODES]
        )
        message = (
            f"Unknown test code. Known lab test codes: {hint}."
            if hint
            else "Unknown test code; no lab test codes are known."
        )
        return await _finish(
            name,
            ctx,
            outcome="error",
            resource_type="patient",
            ok=False,
            error={"code": "unknown_test_code", "message": truncate_text(message)},
        )

    filters = [LabResult.test_code == code, LabResult.not_deleted()]
    if abnormal_only:
        filters.append(
            or_(
                and_(
                    LabResult.ref_high.is_not(None),
                    LabResult.value_num.is_not(None),
                    LabResult.value_num > LabResult.ref_high,
                ),
                and_(
                    LabResult.ref_low.is_not(None),
                    LabResult.value_num.is_not(None),
                    LabResult.value_num < LabResult.ref_low,
                ),
            )
        )
    ranked = (
        select(
            LabResult.id.label("row_id"),
            func.row_number()
            .over(
                partition_by=LabResult.patient_id,
                order_by=(LabResult.collected_at.desc(), LabResult.id),
            )
            .label("rank"),
        )
        .where(*filters)
        .subquery()
    )
    async def fetch_page(page_limit: int, offset: int) -> list[tuple[LabResult, Patient]]:
        result = await ctx.db.execute(
            select(LabResult, Patient)
            .join(ranked, LabResult.id == ranked.c.row_id)
            .join(Patient, Patient.id == LabResult.patient_id)
            .where(ranked.c.rank == 1)
            .order_by(LabResult.collected_at.desc(), LabResult.id)
            .limit(page_limit)
            .offset(offset)
        )
        return list(result.all())

    patients: list[dict[str, Any]] = []
    citations: list[dict[str, str]] = []
    for lab, patient in await _visible_pairs(ctx, fetch_page, bound):
        patients.append(
            {
                "patient_id": str(patient.id),
                "display_name": truncate_text(patient.display_name),
                "mrn": truncate_text(patient.mrn),
                "matched": _lab_matched(lab),
            }
        )
        citations.append(_citation("patient", patient.id))
        citations.append(_citation("lab_result", lab.id))
    return await _finish(
        name,
        ctx,
        outcome="success",
        resource_type="patient",
        ok=True,
        data={"patients": patients},
        citations=citations,
    )


async def find_patients_by_medication(
    medication_name: Annotated[str, Field(min_length=1, max_length=MEDICATION_NAME_MAX_LENGTH, description="Medication name or catalog code: case-insensitive substring match on the prescription medication name or catalog code (wildcards are literal).")],
    active_only: Annotated[bool, Field(description="Only active prescriptions. Default true.")] = True,
    limit: Annotated[int, Field(ge=LOOKUP_LIMIT_MIN, le=LOOKUP_LIMIT_MAX, description="Maximum patient references to return (1-50).")] = LOOKUP_LIMIT_DEFAULT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Find the patients with prescriptions matching one medication.

    Returns only patient references (patient id, display name, MRN) plus a
    short `matched` reason (most recent matching prescription first, one row
    per patient). Matching is a case-insensitive substring match on the
    prescription's medication name or the linked catalog medication code; LIKE
    wildcards in the input are treated literally. With `active_only` true only
    active prescriptions match. Requires `patient:read` and `prescription:read`.
    Patients the caller may not read are never listed.
    """
    name = "find_patients_by_medication"
    if not is_valid_context(tool_context):
        return await _denied_context(name)
    ctx = tool_context
    deny = await _lookup_permission_gate(name, ctx, "prescription:read")
    if deny is not None:
        return deny
    bound = max(LOOKUP_LIMIT_MIN, min(limit, LOOKUP_LIMIT_MAX))
    term = medication_name.strip()
    if not term:
        return await _finish(
            name,
            ctx,
            outcome="error",
            resource_type="patient",
            ok=False,
            error={
                "code": "invalid_arguments",
                "message": "medication_name must contain a non-whitespace character.",
            },
        )
    pattern = patients_service.like_pattern(term)
    catalog_ids = select(Medication.id).where(Medication.code.ilike(pattern, escape="\\"))
    filters = [
        Prescription.not_deleted(),
        or_(
            Prescription.medication_name.ilike(pattern, escape="\\"),
            Prescription.medication_id.in_(catalog_ids),
        ),
    ]
    if active_only:
        filters.append(Prescription.status == "active")
    ranked = (
        select(
            Prescription.id.label("row_id"),
            func.row_number()
            .over(
                partition_by=Prescription.patient_id,
                order_by=(Prescription.start_date.desc(), Prescription.id),
            )
            .label("rank"),
        )
        .where(*filters)
        .subquery()
    )
    async def fetch_page(page_limit: int, offset: int) -> list[tuple[Prescription, Patient]]:
        result = await ctx.db.execute(
            select(Prescription, Patient)
            .join(ranked, Prescription.id == ranked.c.row_id)
            .join(Patient, Patient.id == Prescription.patient_id)
            .where(ranked.c.rank == 1)
            .order_by(Prescription.start_date.desc(), Prescription.id)
            .limit(page_limit)
            .offset(offset)
        )
        return list(result.all())

    patients: list[dict[str, Any]] = []
    citations: list[dict[str, str]] = []
    for rx, patient in await _visible_pairs(ctx, fetch_page, bound):
        patients.append(
            {
                "patient_id": str(patient.id),
                "display_name": truncate_text(patient.display_name),
                "mrn": truncate_text(patient.mrn),
                "matched": _rx_matched(rx),
            }
        )
        citations.append(_citation("patient", patient.id))
        citations.append(_citation("prescription", rx.id))
    return await _finish(
        name,
        ctx,
        outcome="success",
        resource_type="patient",
        ok=True,
        data={"patients": patients},
        citations=citations,
    )
