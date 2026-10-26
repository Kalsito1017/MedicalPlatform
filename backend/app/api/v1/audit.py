"""Audit read API (docs/API.md): paginated, newest-first, bounded filters.

Read-only view of `admin.audit_event` (audit:read). Rows carry actor, action,
resource reference, outcome, request id, and timestamp — never payloads,
passwords, tokens, or request bodies (see app.audit.logger).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, Pagination, reject_unknown_query
from app.authorization.policy import Principal, require_permission
from app.schemas.audit import AuditEventOut
from app.schemas.common import Page
from app.services.audit import AUDIT_ACTION_MAX_LENGTH, list_audit_events

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditEventOut])
async def list_audit(
    db: DbSession,
    pagination: Pagination,
    action: str | None = Query(default=None, max_length=AUDIT_ACTION_MAX_LENGTH),
    actor_id: uuid.UUID | None = Query(default=None),
    outcome: str | None = Query(default=None, pattern="^(success|denied|error)$"),
    _unknown: None = Depends(reject_unknown_query("action", "actor_id", "outcome", "limit", "offset")),
    _principal: Principal = Depends(require_permission("audit:read")),
) -> Page[AuditEventOut]:
    rows, total = await list_audit_events(
        db,
        limit=pagination.limit,
        offset=pagination.offset,
        action=action.strip() if action and action.strip() else None,
        actor_id=actor_id,
        outcome=outcome,
    )
    items = [
        AuditEventOut(
            id=event.id,
            actor_user_id=event.actor_user_id,
            actor_email=actor_email,
            actor_display_name=actor_display_name,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            request_id=event.request_id,
            ip=event.ip,
            created_at=event.created_at,
        )
        for event, actor_email, actor_display_name in rows
    ]
    return Page[AuditEventOut](
        items=items,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )
