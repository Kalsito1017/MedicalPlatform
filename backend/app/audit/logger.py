"""Audit event writer (docs/SECURITY.md "Audit design").

`write_audit_event` persists one row to `admin.audit_event` on the caller's
transaction (the caller commits). Rows carry ONLY: actor, action, resource
reference (type + id), outcome (`success` | `denied` | `error`), request id,
optional client IP, and a server timestamp.

Never recorded: clinical payloads, note text, lab values, passwords, password
hashes, session tokens, CSRF tokens, API keys, request bodies, or query terms.

Actions used by Phases 5-6 (grep `action=` for the full live inventory):
  auth.login.success, auth.login.failure, auth.logout,
  authz.denied, patient.read, patient.summary.read, labs.read,
  prescriptions.read, staff.create, staff.update, staff.deactivate,
  role.grant, role.revoke, session.revoke_all

Resource references:
  - patient/encounter reads -> ("patient"|"encounter", uuid)
  - staff changes -> ("staff_user", user uuid)
  - role.grant / role.revoke -> ("user_role", "<target_user_id>:<role_key>")
    (the grant resource is the (user, role) pair)
  - login failure with an unknown account -> ("email", attempted email) — the
    "attempted email reference" allowed by docs/SECURITY.md; no password is
    ever recorded.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AuditEvent

logger = logging.getLogger(__name__)

VALID_OUTCOMES = ("success", "denied", "error")


def _publish_after_commit(session) -> None:  # noqa: ANN001
    pending = session.info.pop("_system_log_pending", [])
    if pending:
        from app.logs.service import publish_audit_payload

        for payload in pending:
            publish_audit_payload(payload)


def _discard_after_rollback(session) -> None:  # noqa: ANN001
    session.info.pop("_system_log_pending", None)


def _ensure_publish_hook(session: AsyncSession) -> None:
    sync = session.sync_session
    if sync.info.get("_system_log_hook"):
        return
    sqlalchemy_event.listen(sync, "after_commit", _publish_after_commit)
    sqlalchemy_event.listen(sync, "after_rollback", _discard_after_rollback)
    sync.info["_system_log_hook"] = True


def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def write_audit_event(
    db: AsyncSession,
    *,
    actor_user_id: uuid.UUID | str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str,
    request_id: str | None,
    ip: str | None = None,
) -> AuditEvent:
    """Append one audit row on `db` (caller commits). See module docstring."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid audit outcome: {outcome!r}")
    event = AuditEvent(
        actor_user_id=_as_uuid(actor_user_id),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        ip=ip,
    )
    db.add(event)
    await db.flush()
    # Normalize now (before commit/expiry) and publish only after the caller
    # commits. Rollbacks discard the pending payload, so the live stream never
    # shows an audit event that was not persisted.
    from app.logs.service import build_audit_event_payload

    _ensure_publish_hook(db)
    db.sync_session.info.setdefault("_system_log_pending", []).append(
        build_audit_event_payload(
            event_id=event.id,
            created_at=event.created_at or datetime.now(timezone.utc),
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            request_id=event.request_id,
            outcome=event.outcome,
            actor_user_id=event.actor_user_id,
        )
    )
    return event


async def record_audit_event(
    *,
    actor_user_id: uuid.UUID | str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str,
    request_id: str | None,
    ip: str | None = None,
) -> bool:
    """Best-effort audit write on its own committed transaction.

    Used where no request-scoped session exists or where the audit row must
    survive a failing request (authorization denials, CSRF rejections). Failures
    are logged and swallowed: auditing must never mask the original outcome.
    """
    from app.db.base import get_session_factory

    try:
        async with get_session_factory()() as session:
            await write_audit_event(
                session,
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                request_id=request_id,
                ip=ip,
            )
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - audit must never break the request
        logger.warning(
            "audit write failed",
            extra={"action": action, "error_type": type(exc).__name__},
        )
        return False
