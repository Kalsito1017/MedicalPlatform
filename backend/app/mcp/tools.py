"""The six read-only MCP tools (docs/API.md "MCP tool contract").

Exactly six tools exist; there is no `execute_sql`, shell, filesystem, or
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
`matched` reason string — never a full clinical payload.

Tool results are envelopes consumed by app/mcp/server.py: `ok`/`data`/`error`
go back to the model, `citations` feed the API response, and `audit` names the
resource reference. Concealment semantics match the REST API: a patient the
caller may not read is indistinguishable from a missing one (`not_found`), and
lookup rows for such patients are silently dropped (never listed).
"""
from __future__ import annotations

import uuid
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
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    SEARCH_LIMIT_MIN,
    SEARCH_QUERY_MAX_LENGTH,
    SUMMARY_LIMIT_DEFAULT,
    SUMMARY_LIMIT_MAX,
    SUMMARY_LIMIT_MIN,
    TEST_CODE_MAX_LENGTH,
    TEXT_FIELD_MAX_LENGTH,
    TRUNCATION_MARKER,
)
from app.models.lab import LabResult
from app.models.medication import Medication, Prescription
from app.models.patient import Patient
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
    """Find the SYNTHETIC patients with lab results for one known test code.

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
    """Find the SYNTHETIC patients with prescriptions matching one medication.

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
