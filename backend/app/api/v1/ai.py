"""AI assistant endpoint (docs/API.md "AI assistant", ARCHITECTURE.md data flow (b)).

`POST /api/v1/ai/ask` - two modes over one endpoint:

* patient-scoped (`{"patient_id", "question"}`) - permission `patient:read`
  plus patient access (the concealment contract: an inaccessible patient is
  `404 not_found`, never 403),
* general lookup (`{"question"}`, no patient_id) - permission `patient:read`;
  the model may search and read records across the synthetic patients the
  caller is authorized for, and every tool call is authorization-checked again
  inside the tool layer.

`GET /api/v1/ai/conversations` and `DELETE /api/v1/ai/conversations[/{id}]`
serve the caller's own persisted history (permission `patient:read`, scoped to
the session user server-side - never another user's rows). Every successful ask
persists its exchange to that history in the same request lifecycle and commit
as the `ai.ask` audit event; the frozen ask response shape is unchanged.

Non-GET is CSRF-protected by app.auth.csrf middleware.

An optional `Idempotency-Key` header makes the ask safe to retry
(app.api.idempotency, docs/API.md "Idempotent mutations"): a replay of the same
key + payload returns the recorded answer without a second LLM call, history
row, or audit event.

Flow: authn (session) -> authz (policy + check_patient_access in patient mode,
re-check #1) -> per-user rate limit and request bounds (shared by both modes)
-> bounded LLM tool loop where every MCP tool re-authorizes (re-check #2) ->
exact frozen response shape. Every call is audited (`ai.ask` outcome, plus the
`mcp.*` tool events from the tool layer). No prompt, question text, or clinical
payload is ever audited or logged.

When the LLM is disabled or unreachable the endpoint answers 503
`dependency_unavailable`; the rest of the platform is unaffected.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.deps import DbSession, Pagination, RequestId, reject_unknown_query
from app.api.idempotency import ENDPOINT_AI_ASK, IdempotencyContext, IdempotencyKey
from app.audit.logger import write_audit_event
from app.auth.identity import client_ip
from app.authorization.policy import Principal, require_permission
from app.errors import DependencyUnavailable, NotFound, RateLimited, Unauthenticated
from app.llm.limiter import ai_rate_limiter
from app.llm.loop import ToolLoopAbort, run_assistant
from app.llm.provider import (
    LLMRateLimited,
    LLMRejected,
    LLMTimeout,
    LLMUnavailable,
    build_provider,
)
from app.mcp.context import ToolContext
from app.mcp.server import mcp_server
from app.schemas.ai import DISCLAIMER, AskRequest, AskResponse, Citation, ConversationOut
from app.schemas.common import Page
from app.services import ai_history
from app.services.access import check_patient_access

router = APIRouter(tags=["ai"])

# Audit resource reference for a general-lookup ask (no single patient subject).
GENERAL_LOOKUP_RESOURCE_TYPE = "ai_lookup"
# Audit resource reference for conversation-history mutations.
HISTORY_RESOURCE_TYPE = "ai_conversation"


async def _audit_ask(
    db,
    *,
    request: Request,
    request_id: str,
    principal: Principal,
    resource_type: str | None,
    resource_id: str | None,
    outcome: str,
) -> None:
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="ai.ask",
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()


@router.post("/ai/ask", response_model=AskResponse)
async def ask(
    request: Request,
    body: AskRequest,
    db: DbSession,
    request_id: RequestId,
    idem_key: IdempotencyKey,
    principal: Principal = Depends(require_permission("patient:read")),
) -> AskResponse | JSONResponse:
    """Answer one bounded question (AI-generated): one patient or a general lookup.

    Idempotency (docs/API.md "Idempotent mutations"): when the request carries
    an `Idempotency-Key`, a REPLAY of the same key + payload within the
    retention window returns the recorded answer with
    `Idempotency-Replayed: true` WITHOUT re-running the LLM tool loop and
    without writing a second history row or audit event; the same key with a
    different payload is 409 `conflict`. The replay check runs before the
    access check and rate limiter (the original request already passed both).
    """
    settings = request.app.state.settings

    idem = IdempotencyContext.build(
        key=idem_key,
        user_id=principal.user_uuid,
        endpoint=ENDPOINT_AI_ASK,
        payload=body.model_dump(mode="json"),
    )
    if idem is not None:
        replay = await idem.lookup(db)
        if replay is not None:
            return replay.response()

    patient_id = body.patient_id
    resource_type = "patient" if patient_id is not None else GENERAL_LOOKUP_RESOURCE_TYPE
    resource_id = str(patient_id) if patient_id is not None else None

    if patient_id is not None:
        allowed = await check_patient_access(principal, patient_id, db=db)
        if not allowed:
            await _audit_ask(
                db,
                request=request,
                request_id=request_id,
                principal=principal,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome="denied",
            )
            raise NotFound()

    rate_key = principal.user_id or client_ip(request) or "unknown"
    retry_after = ai_rate_limiter.retry_after_seconds(rate_key)
    if retry_after is not None:
        await _audit_ask(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="denied",
        )
        raise RateLimited(retry_after=retry_after)
    ai_rate_limiter.record(rate_key)

    provider = build_provider(settings)
    tool_context = ToolContext(
        principal=principal,
        db=db,
        request_id=request_id,
        ip=client_ip(request),
    )
    try:
        outcome = await run_assistant(
            provider=provider,
            mcp=mcp_server,
            question=body.question,
            patient_id=str(patient_id) if patient_id is not None else None,
            tool_context=tool_context,
            settings=settings,
        )
    except LLMRateLimited as exc:
        await _audit_ask(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="error",
        )
        raise RateLimited(exc.message, retry_after=exc.retry_after)
    except (LLMUnavailable, LLMTimeout, LLMRejected, ToolLoopAbort) as exc:
        await _audit_ask(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="error",
        )
        raise DependencyUnavailable(exc.message)

    citations: list[Citation] = []
    for reference in outcome.citations:
        try:
            citations.append(Citation.model_validate(reference))
        except ValidationError:
            continue  # never fail the answer on a malformed reference

    # Persist the exchange to the caller's private history before the single
    # commit below: one transaction for history row + ai.ask audit event, so a
    # request lifecycle can never double-write (app.services.ai_history).
    if principal.user_uuid is not None:
        await ai_history.save_conversation(
            db,
            user_id=principal.user_uuid,
            patient_id=patient_id,
            question=body.question,
            answer=outcome.answer,
            used_tools=outcome.used_tools,
            citations=[citation.model_dump() for citation in citations],
            model=outcome.model,
        )

    response = AskResponse(
        answer=outcome.answer,
        used_tools=outcome.used_tools,
        citations=citations,
        model=outcome.model,
        ai_generated=True,
        disclaimer=DISCLAIMER,
    )
    if idem is not None:
        # Recorded before the single commit below so the idempotency row commits
        # in the same transaction as the history row and the ai.ask audit event.
        # The answer contains no secrets (it is stored in history anyway).
        replay = await idem.record(db, status_code=200, body=response.model_dump(mode="json"))
        if replay is not None:
            return replay.response()

    await _audit_ask(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="success",
    )
    return response


# --- conversation history (per-user private, docs/API.md "AI assistant") ----


def _history_user(principal: Principal) -> uuid.UUID:
    """The caller's own user id; sessions without one are unauthenticated."""
    user_id = principal.user_uuid
    if user_id is None:
        raise Unauthenticated()
    return user_id


@router.get("/ai/conversations", response_model=Page[ConversationOut])
async def list_conversations(
    db: DbSession,
    pagination: Pagination,
    patient_id: uuid.UUID | None = Query(default=None),
    _unknown: None = Depends(reject_unknown_query("limit", "offset", "patient_id")),
    principal: Principal = Depends(require_permission("patient:read")),
) -> Page[ConversationOut]:
    """The caller's own history, newest first. Never another user's rows.

    `patient_id` optionally narrows to one patient's questions (patient-scoped
    exchanges only). Standard pagination envelope; `question` is bounded at
    write time (AskRequest, 1..2000 characters).
    """
    user_id = _history_user(principal)
    rows, total = await ai_history.list_conversations(
        db,
        user_id=user_id,
        limit=pagination.limit,
        offset=pagination.offset,
        patient_id=patient_id,
    )
    items = [
        ConversationOut(
            id=row.id,
            patient_id=row.patient_id,
            question=row.question,
            answer=row.answer,
            used_tools=ai_history.decode_used_tools(row),
            citations=[
                citation
                for entry in ai_history.decode_citations(row)
                if (citation := _valid_citation(entry)) is not None
            ],
            model=row.model,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return Page[ConversationOut](
        items=items,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


def _valid_citation(entry: dict[str, str]) -> Citation | None:
    try:
        return Citation.model_validate(entry)
    except ValidationError:
        return None  # never fail a history read on a malformed stored reference


@router.delete("/ai/conversations", status_code=204)
async def clear_conversations(
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:read")),
) -> Response:
    """Clear the caller's ENTIRE own history (204). Audited `ai.history.clear`."""
    user_id = _history_user(principal)
    await ai_history.clear_conversations(db, user_id=user_id)
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="ai.history.clear",
        resource_type=HISTORY_RESOURCE_TYPE,
        resource_id=None,
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    return Response(status_code=204)


@router.delete("/ai/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("patient:read")),
) -> Response:
    """Delete one OWNED exchange (204). A foreign/unknown id is 404 (concealment).

    Audited `ai.history.delete` (metadata only - never the question or answer).
    """
    user_id = _history_user(principal)
    deleted = await ai_history.delete_conversation(
        db, user_id=user_id, conversation_id=conversation_id
    )
    if not deleted:
        await write_audit_event(
            db,
            actor_user_id=principal.user_uuid,
            action="ai.history.delete",
            resource_type=HISTORY_RESOURCE_TYPE,
            resource_id=str(conversation_id),
            outcome="denied",
            request_id=request_id,
            ip=client_ip(request),
        )
        await db.commit()
        raise NotFound()
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="ai.history.delete",
        resource_type=HISTORY_RESOURCE_TYPE,
        resource_id=str(conversation_id),
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    return Response(status_code=204)
