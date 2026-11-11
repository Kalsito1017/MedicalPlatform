"""Verified request identity (the ONLY place identity comes from).

`resolve_principal` reads the `cap_session` cookie, loads the session row and
its staff user (active, not soft-deleted, not expired/revoked), and returns a
`Principal` with user id, email, roles, and effective permissions. It is wired
to `app.authorization.policy.principal_resolver` at application startup.

Identity is never taken from request bodies, query parameters, cookies other
than the session cookie, or (later) LLM tool arguments. The resolved principal
is also stored on `request.state.principal` for auditing.
"""
from __future__ import annotations

from fastapi import Request

from app.auth.sessions import load_session_with_roles
from app.authorization.policy import Principal, permissions_for_roles
from app.db.base import get_session_factory
from app.errors import Unauthenticated


def client_ip(request: Request) -> str | None:
    """Direct client address for security auditing (never used for identity)."""
    return request.client.host if request.client else None


def client_user_agent(request: Request) -> str | None:
    """Bounded user-agent string for session records (no full request payloads)."""
    value = request.headers.get("user-agent")
    return value[:255] if value else None


async def resolve_principal(request: Request) -> Principal | None:
    """Session-cookie -> (session row, user) -> Principal, or None when anonymous.

    Deny by default: unknown, expired, or revoked sessions and inactive or
    soft-deleted users all resolve to None (401 at the policy layer).
    """
    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        return None
    async with get_session_factory()() as db:
        resolved = await load_session_with_roles(db, raw_token)
        if resolved is None:
            return None
        _session_row, user, roles = resolved
    principal = Principal(
        user_id=str(user.id),
        roles=roles,
        is_active=user.is_active,
        email=user.email,
        permissions=permissions_for_roles(roles),
    )
    request.state.principal = principal
    return principal


async def require_principal(request: Request) -> Principal:
    """Dependency: verified principal or 401 (no permission check).

    Used by endpoints any authenticated user may call (e.g. logout, /auth/me).
    """
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None:
        principal = await resolve_principal(request)
    if principal is None or not principal.is_active:
        raise Unauthenticated()
    return principal
