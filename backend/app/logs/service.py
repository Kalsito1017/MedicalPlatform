"""Unified system-log read model and operational-event writer.

Audit rows stay in `admin.audit_event`; operational events live in
`admin.system_event` with bounded retention. The Admin read model merges both
without copying audit rows. History uses bounded candidate queries per source
and a deterministic `(timestamp, event_id)` order; SSE replay uses the same
ordering and a cursor.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logs.bus import bus
from app.logs.registry import (
    AUDIT_EVENT_SPECS,
    SAFE_METADATA_KEYS,
    SEVERITIES,
    audit_event_spec,
    event_matches,
    operational_event_spec,
    sanitize_metadata,
)
from app.models import AuditEvent, SystemEvent

logger = logging.getLogger(__name__)

OPERATIONAL_RETENTION_DAYS = 7
MAX_LOG_RANGE_DAYS = 7
MAX_LOG_LIMIT = 100
MAX_LOG_OFFSET = 500
REPLAY_LIMIT = 200


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_resource_ref(resource_type: str | None, resource_id: str | None) -> str | None:
    if not resource_id:
        return None
    if resource_type == "email":
        return None
    return str(resource_id)[:128]


def build_audit_event_payload(
    *,
    event_id: uuid.UUID,
    created_at: datetime,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    request_id: str | None,
    outcome: str,
    actor_user_id: uuid.UUID | None,
) -> dict[str, Any]:
    spec, event_type, metadata = audit_event_spec(action, outcome, resource_type)
    return {
        "id": f"audit:{event_id}",
        "timestamp": _iso_utc(created_at),
        "severity": spec.severity,
        "category": spec.category,
        "event_type": event_type,
        "description": spec.description,
        "actor_id": str(actor_user_id) if actor_user_id else None,
        "resource_type": resource_type,
        "resource_reference": _safe_resource_ref(resource_type, resource_id),
        "request_id": request_id,
        "outcome": outcome,
        "metadata": sanitize_metadata(metadata),
    }


def serialize_audit_event(row: AuditEvent) -> dict[str, Any]:
    return build_audit_event_payload(
        event_id=row.id,
        created_at=row.created_at,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        request_id=row.request_id,
        outcome=row.outcome,
        actor_user_id=row.actor_user_id,
    )


def serialize_operational_event(row: SystemEvent) -> dict[str, Any]:
    spec = operational_event_spec(row.event_type)
    return {
        "id": f"op:{row.id}",
        "timestamp": _iso_utc(row.created_at),
        "severity": spec.severity,
        "category": spec.category,
        "event_type": row.event_type,
        "description": spec.description,
        "actor_id": str(row.actor_id) if row.actor_id else None,
        "resource_type": row.resource_type,
        "resource_reference": _safe_resource_ref(row.resource_type, row.resource_id),
        "request_id": row.request_id,
        "outcome": row.outcome,
        "metadata": sanitize_metadata(row.safe_metadata or {}),
    }


def publish_audit_payload(payload: dict[str, Any]) -> None:
    bus.publish(payload)


async def record_operational_event(
    *,
    event_type: str,
    actor_id: uuid.UUID | str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    outcome: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Best-effort operational event write + live publish.

    Operational logging must never break a clinical request. Invalid event
    types are programming errors and raise before any DB access.
    """
    operational_event_spec(event_type)
    actor_uuid: uuid.UUID | None = None
    if actor_id is not None:
        try:
            actor_uuid = actor_id if isinstance(actor_id, uuid.UUID) else uuid.UUID(str(actor_id))
        except ValueError:
            actor_uuid = None
    from app.db.base import get_session_factory

    try:
        async with get_session_factory()() as session:
            row = SystemEvent(
                id=uuid.uuid4(),
                severity=operational_event_spec(event_type).severity,
                category=operational_event_spec(event_type).category,
                event_type=event_type,
                actor_id=actor_uuid,
                resource_type=(resource_type or None),
                resource_id=(str(resource_id)[:128] if resource_id is not None else None),
                request_id=(request_id[:64] if request_id else None),
                outcome=(outcome[:16] if outcome else None),
                safe_metadata=sanitize_metadata(metadata),
            )
            session.add(row)
            await session.flush()
            await session.execute(
                delete(SystemEvent).where(
                    SystemEvent.created_at
                    < datetime.now(timezone.utc) - timedelta(days=OPERATIONAL_RETENTION_DAYS)
                )
            )
            await session.commit()
            publish_audit_payload(serialize_operational_event(row))
        return True
    except Exception as exc:  # noqa: BLE001 - logging is best-effort
        logger.warning(
            "operational event write failed",
            extra={"event_type": event_type, "error_type": type(exc).__name__},
        )
        return False


@dataclass(frozen=True)
class LogFilters:
    start: datetime
    end: datetime
    severity: str | None = None
    category: str | None = None
    event_type: str | None = None
    actor_id: uuid.UUID | None = None
    request_id: str | None = None
    outcome: str | None = None
    query: str | None = None

    def matches(self, event: dict[str, Any]) -> bool:
        timestamp = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
        if timestamp < self.start or timestamp >= self.end:
            return False
        return event_matches(
            event,
            severities={self.severity} if self.severity else None,
            categories={self.category} if self.category else None,
            event_type=self.event_type,
            actor_id=str(self.actor_id) if self.actor_id else None,
            request_id=self.request_id,
            outcome=self.outcome,
            query=self.query,
        )


def _q_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _audit_base_severity_expr():
    clauses = [(AuditEvent.action == "ai.ask", "success")]
    clauses.extend((AuditEvent.action == action, spec.severity) for action, spec in AUDIT_EVENT_SPECS.items())
    base = case(*clauses, else_="info")
    mcp = case(
        (
            and_(
                AuditEvent.action.like("mcp.%"),
                AuditEvent.action != "mcp.unknown_tool",
            ),
            "success",
        ),
        else_=base,
    )
    return case(
        (AuditEvent.outcome == "denied", "security"),
        (AuditEvent.outcome == "error", "error"),
        else_=mcp,
    )


def _audit_category_expr():
    clauses = [
        (
            and_(
                AuditEvent.action == "patient.read",
                AuditEvent.resource_type == "encounter",
            ),
            "encounters",
        ),
        (AuditEvent.action == "ai.ask", "ai"),
    ]
    clauses.extend(
        (AuditEvent.action == action, spec.category)
        for action, spec in AUDIT_EVENT_SPECS.items()
    )
    return case(
        *clauses,
        (
            and_(
                AuditEvent.action.like("mcp.%"),
                AuditEvent.action != "mcp.unknown_tool",
            ),
            "mcp",
        ),
        else_="system",
    )


def _audit_event_type_expr():
    clauses = [
        (AuditEvent.action == "ai.ask", "ai.ask"),
        (AuditEvent.action == "mcp.unknown_tool", "mcp.unknown_tool"),
    ]
    clauses.extend((AuditEvent.action == action, action) for action in AUDIT_EVENT_SPECS)
    return case(
        *clauses,
        (
            and_(
                AuditEvent.action.like("mcp.%"),
                AuditEvent.action != "mcp.unknown_tool",
            ),
            "mcp.tool.call",
        ),
        else_="system.audit_event",
    )


SEVERITY_EXPR = _audit_base_severity_expr()
CATEGORY_EXPR = _audit_category_expr()
EVENT_TYPE_EXPR = _audit_event_type_expr()


def _audit_filters(filters: LogFilters) -> list[Any]:
    result = [
        AuditEvent.created_at >= filters.start,
        AuditEvent.created_at < filters.end,
    ]
    if filters.severity:
        result.append(SEVERITY_EXPR == filters.severity)
    if filters.category:
        result.append(CATEGORY_EXPR == filters.category)
    if filters.event_type:
        result.append(EVENT_TYPE_EXPR == filters.event_type)
    if filters.actor_id:
        result.append(AuditEvent.actor_user_id == filters.actor_id)
    if filters.request_id:
        result.append(AuditEvent.request_id == filters.request_id)
    if filters.outcome:
        result.append(AuditEvent.outcome == filters.outcome)
    if filters.query:
        pattern = _q_pattern(filters.query)
        result.append(
            or_(
                AuditEvent.action.ilike(pattern, escape="\\"),
                AuditEvent.resource_type.ilike(pattern, escape="\\"),
                # Login-failure email references are redacted from the System
                # Logs read model; never expose them through the search oracle.
                and_(
                    func.coalesce(AuditEvent.resource_type, "") != "email",
                    AuditEvent.resource_id.ilike(pattern, escape="\\"),
                ),
                AuditEvent.request_id.ilike(pattern, escape="\\"),
            )
        )
    return result


def _operational_filters(filters: LogFilters) -> list[Any]:
    result = [
        SystemEvent.created_at >= filters.start,
        SystemEvent.created_at < filters.end,
    ]
    if filters.severity:
        result.append(SystemEvent.severity == filters.severity)
    if filters.category:
        result.append(SystemEvent.category == filters.category)
    if filters.event_type:
        result.append(SystemEvent.event_type == filters.event_type)
    if filters.actor_id:
        result.append(SystemEvent.actor_id == filters.actor_id)
    if filters.request_id:
        result.append(SystemEvent.request_id == filters.request_id)
    if filters.outcome:
        result.append(SystemEvent.outcome == filters.outcome)
    if filters.query:
        pattern = _q_pattern(filters.query)
        result.append(
            or_(
                SystemEvent.event_type.ilike(pattern, escape="\\"),
                SystemEvent.resource_type.ilike(pattern, escape="\\"),
                SystemEvent.resource_id.ilike(pattern, escape="\\"),
                SystemEvent.request_id.ilike(pattern, escape="\\"),
                # Approved flat metadata only; no free-form payload is stored.
                *(
                    SystemEvent.safe_metadata[key].astext.ilike(pattern, escape="\\")
                    for key in SAFE_METADATA_KEYS
                ),
            )
        )
    return result


def _empty_counts() -> dict[str, int]:
    return {severity: 0 for severity in SEVERITIES} | {"total": 0}


async def query_events(
    db: AsyncSession, filters: LogFilters, *, limit: int, offset: int
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_LOG_LIMIT))
    offset = max(0, min(int(offset), MAX_LOG_OFFSET))
    candidate_limit = min(limit + offset, MAX_LOG_LIMIT + MAX_LOG_OFFSET)
    audit_filters = _audit_filters(filters)
    op_filters = _operational_filters(filters)

    audit_rows = (
        await db.execute(
            select(
                AuditEvent.id,
                AuditEvent.created_at,
                AuditEvent.action,
                AuditEvent.resource_type,
                AuditEvent.resource_id,
                AuditEvent.request_id,
                AuditEvent.outcome,
                AuditEvent.actor_user_id,
            )
            .where(*audit_filters)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(candidate_limit)
        )
    ).all()
    op_rows = (
        await db.execute(
            select(SystemEvent)
            .where(*op_filters)
            .order_by(SystemEvent.created_at.desc(), SystemEvent.id.desc())
            .limit(candidate_limit)
        )
    ).scalars().all()

    audit_events = []
    for row in audit_rows:
        audit_events.append(
            build_audit_event_payload(
                event_id=row.id,
                created_at=row.created_at,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                request_id=row.request_id,
                outcome=row.outcome,
                actor_user_id=row.actor_user_id,
            )
        )
    events = audit_events + [serialize_operational_event(row) for row in op_rows]
    events.sort(key=lambda event: (event["timestamp"], event["id"]), reverse=True)
    page = events[offset : offset + limit]

    counts = _empty_counts()
    audit_count_rows = (
        await db.execute(
            select(SEVERITY_EXPR, func.count()).where(*audit_filters).group_by(SEVERITY_EXPR)
        )
    ).all()
    op_count_rows = (
        await db.execute(
            select(SystemEvent.severity, func.count()).where(*op_filters).group_by(SystemEvent.severity)
        )
    ).all()
    for severity, count in [*audit_count_rows, *op_count_rows]:
        key = str(severity)
        counts[key] = counts.get(key, 0) + int(count)
        counts["total"] += int(count)
    return {
        "items": page,
        "total": counts["total"],
        "limit": limit,
        "offset": offset,
        "counts": counts,
    }


async def cursor_key(db: AsyncSession, event_id: str | None) -> tuple[str, str] | None:
    if not event_id or ":" not in event_id:
        return None
    source, raw = event_id.split(":", 1)
    try:
        uid = uuid.UUID(raw)
    except ValueError:
        return None
    created_at = None
    if source == "audit":
        created_at = await db.scalar(select(AuditEvent.created_at).where(AuditEvent.id == uid))
    elif source == "op":
        created_at = await db.scalar(select(SystemEvent.created_at).where(SystemEvent.id == uid))
    return (_iso_utc(created_at), event_id) if created_at is not None else None


async def replay_events(
    db: AsyncSession, filters: LogFilters, last_event_id: str | None
) -> list[dict[str, Any]]:
    """Bounded replay after the cursor, oldest first, then live subscription."""
    cursor = await cursor_key(db, last_event_id)
    effective = filters
    if cursor is not None:
        cursor_time = datetime.fromisoformat(cursor[0].replace("Z", "+00:00"))
        if cursor_time > effective.start:
            effective = replace(effective, start=cursor_time)
    page = await query_events(db, effective, limit=REPLAY_LIMIT, offset=0)
    events = page["items"]
    if cursor is not None:
        events = [event for event in events if (event["timestamp"], event["id"]) > cursor]
    events.reverse()
    return events
