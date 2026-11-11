"""Database-backed sessions (docs/SECURITY.md "Sessions").

Session/logout behavior (PLAN.md Phase 5 acceptance: documented):

- The `cap_session` cookie carries a raw random token (32 random bytes,
  URL-safe). The `admin.user_session` row stores only the SHA-256 hash of that
  token, so a database leak yields no usable sessions.
- A session resolves only when the row is not revoked (`revoked_at IS NULL`),
  not expired (`expires_at` in the future), and its user is active and not
  soft-deleted. Expiry is enforced here on every request — never client-side.
- TTL comes from `SESSION_TTL_HOURS` (default 8) and is fixed at creation
  (no sliding expiry). `last_seen_at` is refreshed at most once per minute.
- Login always creates a NEW token and, when the request presented an existing
  session, revokes that session first (anti-fixation: the pre-login cookie can
  never become a post-login session).
- Logout (`POST /api/v1/auth/logout`) revokes the presented session server-side
  and clears the cookie. Revocation is immediate: the token stops resolving on
  the very next request.
- Deactivating a user calls `revoke_all_sessions`, so every session that user
  holds is revoked immediately (the user also stops resolving because inactive
  users are rejected here — defense in depth).
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.admin import Role, StaffUser, UserRole, UserSession

# Minimum interval between `last_seen_at` writes for one session (write
# amplification guard; does not affect expiry enforcement).
_LAST_SEEN_WRITE_INTERVAL_SECONDS = 60

SESSION_TOKEN_BYTES = 32


def generate_session_token() -> str:
    """Raw session token for the cookie: 32 random bytes, URL-safe."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(raw_token: str) -> str:
    """SHA-256 hex digest stored in `user_session.token_hash`. Not reversible."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    ip: str | None = None,
    user_agent: str | None = None,
    ttl_hours: int | None = None,
) -> tuple[str, UserSession]:
    """Create a session row and return (raw_token, row). The raw token is
    returned exactly once (to be set as the cookie) and never stored."""
    settings = get_settings()
    hours = ttl_hours if ttl_hours is not None else settings.session_ttl_hours
    now = _now()
    raw_token = generate_session_token()
    session = UserSession(
        token_hash=hash_session_token(raw_token),
        user_id=user_id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=hours),
        ip=ip,
        user_agent=user_agent[:255] if user_agent else None,
    )
    db.add(session)
    await db.flush()
    return raw_token, session


async def load_session_with_roles(
    db: AsyncSession, raw_token: str
) -> tuple[UserSession, StaffUser, tuple[str, ...]] | None:
    """Resolve a raw cookie token to (session, user, role keys) in one query.

    The session/user/roles join is the hot path for every authenticated
    request. Keeping it to one round trip avoids the former session + user +
    roles trio while preserving the same deny-by-default checks: unknown,
    revoked, or expired sessions and inactive or soft-deleted users all return
    None. Refreshes `last_seen_at` (throttled) when the session resolves.
    """
    if not raw_token:
        return None
    token_hash = hash_session_token(raw_token)
    role_keys = func.array_agg(Role.key).label("role_keys")
    row = (
        await db.execute(
            select(UserSession, StaffUser, role_keys)
            .join(StaffUser, StaffUser.id == UserSession.user_id)
            .outerjoin(UserRole, UserRole.user_id == StaffUser.id)
            .outerjoin(Role, Role.id == UserRole.role_id)
            .where(
                UserSession.token_hash == token_hash,
                StaffUser.is_active.is_(True),
                StaffUser.not_deleted(),
            )
            .group_by(UserSession.id, StaffUser.id)
        )
    ).first()
    if row is None:
        return None
    session, user, roles = row
    now = _now()
    if session.revoked_at is not None or session.expires_at <= now:
        return None
    if (now - session.last_seen_at).total_seconds() >= _LAST_SEEN_WRITE_INTERVAL_SECONDS:
        session.last_seen_at = now
        await db.flush()
    clean_roles = tuple(sorted({str(role) for role in (roles or []) if role}))
    return session, user, clean_roles


async def load_session(db: AsyncSession, raw_token: str) -> tuple[UserSession, StaffUser] | None:
    """Resolve a raw cookie token to (session row, staff user) or None.

    Compatibility wrapper around :func:`load_session_with_roles`; callers that
    need the principal's roles should use the richer function directly.
    """
    resolved = await load_session_with_roles(db, raw_token)
    if resolved is None:
        return None
    session, user, _roles = resolved
    return session, user


async def revoke_session(db: AsyncSession, raw_token: str) -> bool:
    """Revoke the session for a raw cookie token. Returns True when revoked."""
    if not raw_token:
        return False
    row = await db.scalar(select(UserSession).where(UserSession.token_hash == hash_session_token(raw_token)))
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    await db.flush()
    return True


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every active session of a user (used by account deactivation).

    Returns the number of sessions revoked. Exposed as a service-layer seam so
    the admin API and future tooling share one revocation path.
    """
    now = _now()
    result = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.flush()
    return int(result.rowcount or 0)
