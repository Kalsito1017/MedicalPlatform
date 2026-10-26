"""Audit event queries (read side of docs/SECURITY.md "Audit design").

Read-only: the audit API can list events and never mutate them. Rows carry no
clinical payloads and no secrets (see app.audit.logger for the write side).
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AuditEvent, StaffUser

AUDIT_ACTION_MAX_LENGTH = 64


async def list_audit_events(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
    outcome: str | None = None,
) -> tuple[list[tuple[AuditEvent, str | None, str | None]], int]:
    """Newest-first audit events with the actor's email/display name (outer join).

    Bounded filters: exact action, exact actor id, exact outcome. Unknown rows
    are returned with `None` actor fields (system or deleted-account events).
    """
    filters = []
    if action is not None:
        filters.append(AuditEvent.action == action)
    if actor_id is not None:
        filters.append(AuditEvent.actor_user_id == actor_id)
    if outcome is not None:
        filters.append(AuditEvent.outcome == outcome)

    base = select(AuditEvent, StaffUser.email, StaffUser.display_name).outerjoin(
        StaffUser, StaffUser.id == AuditEvent.actor_user_id
    )
    if filters:
        base = base.where(*filters)

    count_base = select(AuditEvent.id)
    if filters:
        count_base = count_base.where(*filters)
    total = (await db.scalar(select(func.count()).select_from(count_base.subquery()))) or 0

    rows = (
        await db.execute(
            base.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [(row[0], row[1], row[2]) for row in rows], int(total)
