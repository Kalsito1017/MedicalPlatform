"""Admin-only System Logs history and SSE stream (docs/API.md "System Logs")."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, reject_unknown_query
from app.auth.identity import resolve_principal
from app.authorization.policy import Principal, has_permission, require_permission
from app.db.base import get_session_factory
from app.errors import ValidationFailed
from app.logs.bus import bus
from app.logs.registry import ALL_EVENT_TYPES
from app.logs.service import (
    MAX_LOG_RANGE_DAYS,
    LogFilters,
    query_events,
    replay_events,
)
from app.schemas.system_log import SystemLogPageOut

router = APIRouter(prefix="/admin/logs", tags=["system-logs"])

DEFAULT_WINDOW_MINUTES = 15
HEARTBEAT_SECONDS = 15

SeverityQuery = Literal["info", "success", "warning", "error", "security"]
CategoryQuery = Literal[
    "auth",
    "security",
    "patients",
    "encounters",
    "labs",
    "prescriptions",
    "appointments",
    "ai",
    "mcp",
    "voice",
    "administration",
    "system",
]
def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailed("Log timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


def _filters(
    *,
    start: datetime | None,
    end: datetime | None,
    severity: SeverityQuery | None,
    category: CategoryQuery | None,
    event_type: str | None,
    actor_id: uuid.UUID | None,
    request_id: str | None,
    outcome: str | None,
    q: str | None,
) -> LogFilters:
    if (start is None) != (end is None):
        raise ValidationFailed("Both start and end are required for a custom log range.")
    now = datetime.now(timezone.utc)
    if start is None or end is None:
        end = now
        start = end - timedelta(minutes=DEFAULT_WINDOW_MINUTES)
    start = _as_aware(start)
    end = _as_aware(end)
    if end <= start:
        raise ValidationFailed("The log range end must be after start.")
    if end - start > timedelta(days=MAX_LOG_RANGE_DAYS):
        raise ValidationFailed(
            f"The log range may not exceed {MAX_LOG_RANGE_DAYS} days."
        )
    if event_type is not None and event_type not in ALL_EVENT_TYPES:
        raise ValidationFailed("Unknown log event type.")
    if outcome is not None and outcome not in {"success", "denied", "error", "info", "warning"}:
        raise ValidationFailed("Unknown log outcome.")
    return LogFilters(
        start=start,
        end=end,
        severity=severity,
        category=category,
        event_type=event_type,
        actor_id=actor_id,
        request_id=request_id,
        outcome=outcome,
        query=q,
    )


@router.get("", response_model=SystemLogPageOut)
async def list_system_logs(
    request: Request,
    db: DbSession,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    severity: SeverityQuery | None = Query(default=None),
    category: CategoryQuery | None = Query(default=None),
    event_type: str | None = Query(default=None, max_length=100),
    actor_id: uuid.UUID | None = Query(default=None),
    request_id: str | None = Query(default=None, max_length=64),
    outcome: str | None = Query(default=None, max_length=16),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=500),
    _unknown: None = Depends(
        reject_unknown_query(
            "start",
            "end",
            "severity",
            "category",
            "event_type",
            "actor_id",
            "request_id",
            "outcome",
            "q",
            "limit",
            "offset",
        )
    ),
    _principal: Principal = Depends(require_permission("logs:read")),
) -> SystemLogPageOut:
    filters = _filters(
        start=start,
        end=end,
        severity=severity,
        category=category,
        event_type=event_type,
        actor_id=actor_id,
        request_id=request_id,
        outcome=outcome,
        q=q,
    )
    payload = await query_events(db, filters, limit=limit, offset=offset)
    return SystemLogPageOut(
        **payload,
        start=filters.start,
        end=filters.end,
        timezone=request.app.state.settings.clinic_timezone,
    )


@router.get("/stream")
async def stream_system_logs(
    request: Request,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    severity: SeverityQuery | None = Query(default=None),
    category: CategoryQuery | None = Query(default=None),
    event_type: str | None = Query(default=None, max_length=100),
    actor_id: uuid.UUID | None = Query(default=None),
    request_id: str | None = Query(default=None, max_length=64),
    outcome: str | None = Query(default=None, max_length=16),
    q: str | None = Query(default=None, max_length=100),
    last_event_id: str | None = Query(default=None, max_length=80),
    _unknown: None = Depends(
        reject_unknown_query(
            "start",
            "end",
            "severity",
            "category",
            "event_type",
            "actor_id",
            "request_id",
            "outcome",
            "q",
            "last_event_id",
        )
    ),
    principal: Principal = Depends(require_permission("logs:read")),
) -> StreamingResponse:
    filters = _filters(
        start=start,
        end=end,
        severity=severity,
        category=category,
        event_type=event_type,
        actor_id=actor_id,
        request_id=request_id,
        outcome=outcome,
        q=q,
    )
    cursor_id = last_event_id or request.headers.get("last-event-id")

    async def still_authorized() -> bool:
        refreshed = await resolve_principal(request)
        return bool(
            refreshed is not None
            and refreshed.is_active
            and has_permission(refreshed.roles, "logs:read")
        )

    async def generate():
        queue = bus.subscribe()
        delivered_ids: set[str] = set()
        last_auth_check = time.monotonic()
        try:
            async with get_session_factory()() as db:
                replay = await replay_events(db, filters, cursor_id)
            if cursor_id:
                delivered_ids.add(cursor_id)
            for event in replay:
                delivered_ids.add(event["id"])
                yield f"id: {event['id']}\nevent: system\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            # Relative/current windows stay open for live events; a custom
            # historical range keeps its fixed upper bound so out-of-period
            # events do not appear in a past-only view.
            now = datetime.now(timezone.utc)
            live_end = (
                filters.end
                if filters.end < now - timedelta(minutes=1)
                else now + timedelta(days=MAX_LOG_RANGE_DAYS)
            )
            live_filters = replace(filters, end=live_end)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_auth_check >= HEARTBEAT_SECONDS:
                        if not await still_authorized():
                            yield 'event: auth_expired\ndata: {"reason":"session_expired"}\n\n'
                            return
                        last_auth_check = time.monotonic()
                    yield ": heartbeat\n\n"
                    continue
                # Revalidate periodically even on a busy stream so a revoked
                # session or deactivated account does not keep receiving events.
                if time.monotonic() - last_auth_check >= HEARTBEAT_SECONDS:
                    if not await still_authorized():
                        yield 'event: auth_expired\ndata: {"reason":"session_expired"}\n\n'
                        return
                    last_auth_check = time.monotonic()
                if not live_filters.matches(event):
                    continue
                # Exact-id dedup across the replay/live boundary. New events
                # that share a timestamp with an already delivered one are
                # still delivered; the client orders by (timestamp, event id).
                if event["id"] in delivered_ids:
                    continue
                delivered_ids.add(event["id"])
                yield f"id: {event['id']}\nevent: system\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
