"""AI assistant endpoint (docs/API.md "AI assistant", ARCHITECTURE.md data flow (b)).

`POST /api/v1/ai/ask` - permission `patient:read` plus patient access (the
concealment contract: an inaccessible patient is `404 not_found`, never 403).
Non-GET is CSRF-protected by app.auth.csrf middleware.

Flow: authn (session) -> authz (policy + check_patient_access, re-check #1) ->
per-user rate limit and request bounds -> bounded LLM tool loop where every MCP
tool re-authorizes (re-check #2) -> exact frozen response shape. Every call is
audited (`ai.ask` outcome, plus the `mcp.*` tool events from the tool layer).
No prompt, question text, or clinical payload is ever audited or logged.

When the LLM is disabled or unreachable the endpoint answers 503
`dependency_unavailable`; the rest of the platform is unaffected.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.api.deps import DbSession, RequestId
from app.audit.logger import write_audit_event
from app.auth.identity import client_ip
from app.authorization.policy import Principal, require_permission
from app.errors import DependencyUnavailable, NotFound, RateLimited
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
from app.schemas.ai import DISCLAIMER, AskRequest, AskResponse, Citation
from app.services.access import check_patient_access

router = APIRouter(tags=["ai"])


async def _audit_ask(
    db,
    *,
    request: Request,
    request_id: str,
    principal: Principal,
    patient_id,
    outcome: str,
) -> None:
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="ai.ask",
        resource_type="patient",
        resource_id=str(patient_id),
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
    principal: Principal = Depends(require_permission("patient:read")),
) -> AskResponse:
    """Answer one bounded question about one synthetic patient (AI-generated)."""
    settings = request.app.state.settings

    allowed = await check_patient_access(principal, body.patient_id, db=db)
    if not allowed:
        await _audit_ask(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            patient_id=body.patient_id,
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
            patient_id=body.patient_id,
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
            patient_id=str(body.patient_id),
            tool_context=tool_context,
            settings=settings,
        )
    except LLMRateLimited as exc:
        await _audit_ask(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            patient_id=body.patient_id,
            outcome="error",
        )
        raise RateLimited(exc.message, retry_after=exc.retry_after)
    except (LLMUnavailable, LLMTimeout, LLMRejected, ToolLoopAbort) as exc:
        await _audit_ask(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            patient_id=body.patient_id,
            outcome="error",
        )
        raise DependencyUnavailable(exc.message)

    citations: list[Citation] = []
    for reference in outcome.citations:
        try:
            citations.append(Citation.model_validate(reference))
        except ValidationError:
            continue  # never fail the answer on a malformed reference

    await _audit_ask(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        patient_id=body.patient_id,
        outcome="success",
    )
    return AskResponse(
        answer=outcome.answer,
        used_tools=outcome.used_tools,
        citations=citations,
        model=outcome.model,
        ai_generated=True,
        disclaimer=DISCLAIMER,
    )
