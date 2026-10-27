"""Analytics read API (docs/API.md "Analytics"): one bounded operational overview.

`GET /api/v1/analytics/summary` (analytics:read — Admin only) returns REAL
aggregates over the synthetic demo dataset computed by
`app.services.analytics` (grouped queries, no N+1, no fabricated numbers). It
accepts NO query parameters (one call; unknown query parameters are a
`400 validation_error` like everywhere else) and writes exactly one
`analytics.read` audit event per request (metadata only — no clinical
payloads, no secrets).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import DbSession, RequestId, reject_unknown_query
from app.audit.logger import write_audit_event
from app.auth.identity import client_ip
from app.authorization.policy import Principal, require_permission
from app.schemas.analytics import AnalyticsSummaryOut
from app.services.analytics import build_summary

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummaryOut)
async def get_analytics_summary(
    request: Request,
    db: DbSession,
    request_id: RequestId,
    _unknown: None = Depends(reject_unknown_query()),
    principal: Principal = Depends(require_permission("analytics:read")),
) -> AnalyticsSummaryOut:
    """Aggregated counts, 30-day activity, lab/prescription/staff mixes, AI usage."""
    payload = await build_summary(db)
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="analytics.read",
        resource_type="analytics",
        resource_id=None,
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    return AnalyticsSummaryOut.model_validate(payload)
