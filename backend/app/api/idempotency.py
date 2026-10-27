"""Idempotency-Key support for the two POSTs whose double-apply would hurt.

`POST /api/v1/staff` and `POST /api/v1/ai/ask` accept an optional
`Idempotency-Key` request header (docs/API.md "Idempotent mutations"):

* Scope: (user_id, endpoint, key). The key NEVER crosses users or endpoints -
  another user replaying the same key executes normally (no cross-user replay,
  no information leak).
* First successful (2xx) response is recorded (`admin.idempotency_record`) with
  its status code and JSON body and is replayed verbatim for a REPLAY of the
  same key + same payload within the retention window (24h), with
  `Idempotency-Replayed: true` set on the response. A replay does NOT
  re-execute: no second LLM call, no second account, no second history row, no
  second audit event.
* Same key + DIFFERENT payload -> 409 `conflict` (chosen over 422: the key was
  already consumed by a different request of the same user, which is a state
  conflict, not a malformed body). Detection uses a SHA-256 request-body
  fingerprint (`request_hash`) - the request payload itself is never stored.
* Failed attempts are NOT recorded: a retry after a failure re-executes.
* Missing header = normal behaviour (fully backwards compatible).
* Concurrency is decided by the (user_id, endpoint, key) unique constraint: the
  second insert loses, is rolled back, and is served as a replay-after-read of
  the winner's record.
* Secrets never land in the record: callers redact before recording (the staff
  one-time `generated_password` is stored and replayed as `null` - a generated
  password is shown exactly once).
* Expired rows (`expires_at` <= now) are treated as absent and pruned
  opportunistically on the same (user, endpoint) scope.

Non-GET requests are additionally CSRF-protected (app.auth.csrf); this module
never logs or audits header values.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, ValidationFailed
from app.models.admin import IdempotencyRecord

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"

# Retention window: a replay within this window returns the recorded response;
# afterwards the key is forgotten and the request executes normally.
RETENTION = timedelta(hours=24)

MAX_KEY_CHARS = 200
# Bounded, non-semantic key charset (client-generated random token). A key is
# never human/clinical text, so nothing user-authored can enter the record.
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{1,200}$")

# Endpoint scope identifiers (stable strings; never derived from request data).
ENDPOINT_STAFF_CREATE = "POST /api/v1/staff"
ENDPOINT_AI_ASK = "POST /api/v1/ai/ask"

PAYLOAD_CONFLICT_MESSAGE = (
    "This Idempotency-Key was already used with a different request payload."
)
RACE_MESSAGE = (
    "A concurrent request with this Idempotency-Key is being processed. "
    "Please retry with a new key."
)


def _fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON body. The body itself is never stored."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Replay:
    """A recorded response to return instead of re-executing the handler."""

    status_code: int
    body: Any

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=self.body,
            headers={REPLAYED_HEADER: "true"},
        )


def idempotency_key(request: Request) -> str | None:
    """FastAPI dependency: validate the optional `Idempotency-Key` header.

    Absent header -> None (the endpoint behaves exactly as before). An invalid
    key is a 400 `validation_error` (field location reported, never the value).
    """
    raw = request.headers.get(IDEMPOTENCY_HEADER)
    if raw is None:
        return None
    value = raw.strip()
    if len(value) > MAX_KEY_CHARS or not KEY_PATTERN.fullmatch(value):
        raise ValidationFailed(
            "Malformed input.",
            details={
                "fields": [
                    {
                        "loc": f"header.{IDEMPOTENCY_HEADER}",
                        "msg": "Idempotency-Key must be 1-200 characters from [A-Za-z0-9._~-].",
                        "type": "invalid_idempotency_key",
                    }
                ]
            },
        )
    return value


IdempotencyKey = Annotated[str | None, Depends(idempotency_key)]


@dataclass
class IdempotencyContext:
    """Per-request idempotency scope; created only when a key is present.

    Route usage (see app.api.v1.staff.create_staff, app.api.v1.ai.ask):

        context = IdempotencyContext.build(key=idem_key, user_id=..., endpoint=..., payload=...)
        if context is not None:
            replay = await context.lookup(db)      # 409 on payload mismatch
            if replay is not None:
                return replay.response()           # recorded response, no re-execution
        ... execute ...
        if context is not None:
            replay = await context.record(db, status_code=..., body=redacted_body)
            if replay is not None:
                return replay.response()           # lost the insert race: replay-after-read
    """

    key: str
    user_id: uuid.UUID
    endpoint: str
    request_hash: str

    @classmethod
    def build(
        cls,
        *,
        key: str | None,
        user_id: uuid.UUID | None,
        endpoint: str,
        payload: dict[str, Any],
    ) -> IdempotencyContext | None:
        """None when no key was sent (normal behaviour) or no user scopes it."""
        if key is None or user_id is None:
            return None
        return cls(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            request_hash=_fingerprint(payload),
        )

    def _scope_filter(self):
        return (
            IdempotencyRecord.user_id == self.user_id,
            IdempotencyRecord.endpoint == self.endpoint,
            IdempotencyRecord.key == self.key,
        )

    async def _prune_expired(self, db: AsyncSession) -> None:
        """Opportunistic cleanup of expired rows for this user (bounded scope)."""
        await db.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.user_id == self.user_id,
                IdempotencyRecord.expires_at <= _now(),
            )
        )

    async def lookup(self, db: AsyncSession) -> Replay | None:
        """Recorded response for this key, or None to execute normally.

        Raises Conflict (409) when the key was already used with a different
        payload. Expired records are deleted and treated as absent.
        """
        await self._prune_expired(db)
        row = await db.scalar(select(IdempotencyRecord).where(*self._scope_filter()))
        if row is None:
            return None
        if row.request_hash != self.request_hash:
            raise Conflict(PAYLOAD_CONFLICT_MESSAGE)
        if row.expires_at <= _now():
            await db.delete(row)
            await db.flush()
            return None
        return Replay(status_code=row.status_code, body=json.loads(row.response_body))

    async def record(
        self, db: AsyncSession, *, status_code: int, body: Any
    ) -> Replay | None:
        """Record the successful response (call BEFORE the final commit so the
        record commits in the same transaction as the work it describes).

        Returns a Replay when the unique constraint decided that a concurrent
        request with the same key won the insert: this request's transaction is
        rolled back and the winner's response is served instead (replay-after-
        read). Raises Conflict when the winner recorded a different payload.
        """
        row = IdempotencyRecord(
            user_id=self.user_id,
            endpoint=self.endpoint,
            key=self.key,
            request_hash=self.request_hash,
            status_code=status_code,
            response_body=json.dumps(body, sort_keys=True, default=str),
            expires_at=_now() + RETENTION,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            replay = await self.lookup(db)
            if replay is None:
                raise Conflict(RACE_MESSAGE) from None
            return replay
        return None
