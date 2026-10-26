"""Auth endpoints (docs/API.md): login, logout, current user.

Session/logout behavior is documented in app.auth.sessions. Login is rate
limited per (email, IP) and per IP (app.auth.ratelimit; in-process — documented
limitation), passwords are verified with argon2id in constant shape (unknown
email and wrong password produce byte-identical 401 bodies), and every outcome
is audited. Plaintext credentials are never logged, audited, or echoed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi import status as http_status

from app.audit.logger import write_audit_event
from app.auth.cookies import clear_session_cookie, set_session_cookie
from app.auth.identity import client_ip, client_user_agent, require_principal
from app.auth.passwords import verify_password
from app.auth.ratelimit import login_rate_limiter
from app.auth.sessions import create_session, revoke_session
from app.authorization.policy import Principal
from app.api.deps import DbSession, RequestId
from app.errors import RateLimited, Unauthenticated
from app.schemas.auth import LoginRequest, UserPayload
from app.services.staff import get_staff_user, get_staff_user_by_email, get_user_roles, to_user_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Uniform login failure: identical message for unknown email and wrong password
# (no account enumeration). Deliberately generic; never varies by account state.
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."
RATE_LIMIT_MESSAGE = "Too many failed login attempts. Please retry later."


@router.post("/login", response_model=UserPayload, status_code=http_status.HTTP_200_OK)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    request_id: RequestId,
) -> UserPayload:
    settings = request.app.state.settings
    ip = client_ip(request)
    user_agent = client_user_agent(request)

    retry_after = login_rate_limiter.retry_after_seconds(payload.email, ip)
    if retry_after is not None:
        logger.warning(
            "login rejected: rate limited",
            extra={"request_id": request_id, "ip": ip, "email": payload.email},
        )
        await write_audit_event(
            db,
            actor_user_id=None,
            action="auth.login.failure",
            resource_type="email",
            resource_id=payload.email,
            outcome="denied",
            request_id=request_id,
            ip=ip,
        )
        await db.commit()
        raise RateLimited(RATE_LIMIT_MESSAGE, retry_after=retry_after)

    user = await get_staff_user_by_email(db, payload.email)
    # Constant shape: verify against a dummy hash when the account is unknown.
    password_ok = verify_password(user.password_hash if user else None, payload.password)
    if user is None or not password_ok or not user.is_active:
        login_rate_limiter.record_failure(payload.email, ip)
        logger.warning(
            "login failed",
            extra={"request_id": request_id, "ip": ip, "email": payload.email},
        )
        await write_audit_event(
            db,
            actor_user_id=user.id if user is not None else None,
            action="auth.login.failure",
            resource_type="staff_user" if user is not None else "email",
            resource_id=str(user.id) if user is not None else payload.email,
            outcome="denied",
            request_id=request_id,
            ip=ip,
        )
        await db.commit()
        raise Unauthenticated(INVALID_CREDENTIALS_MESSAGE)

    # Session rotation (anti-fixation): a pre-login session can never become a
    # post-login session. The old token stops resolving immediately.
    old_token = request.cookies.get(settings.session_cookie_name)
    if old_token:
        await revoke_session(db, old_token)

    raw_token, _session = await create_session(
        db,
        user_id=user.id,
        ip=ip,
        user_agent=user_agent,
        ttl_hours=settings.session_ttl_hours,
    )
    roles = await get_user_roles(db, user.id)
    await write_audit_event(
        db,
        actor_user_id=user.id,
        action="auth.login.success",
        resource_type="staff_user",
        resource_id=str(user.id),
        outcome="success",
        request_id=request_id,
        ip=ip,
    )
    await db.commit()

    login_rate_limiter.record_success(payload.email, ip)
    set_session_cookie(
        response,
        request,
        settings,
        raw_token,
        max_age_seconds=settings.session_ttl_hours * 3600,
    )
    logger.info("login succeeded", extra={"request_id": request_id, "user_id": str(user.id)})
    return to_user_payload(user, roles)


@router.post("/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_principal),
) -> Response:
    """Revoke the current session server-side and clear the cookie.

    Idempotent-ish per docs/API.md: an invalid/absent session is `401`; a valid
    session is revoked and the cookie cleared. Repeating the call afterwards
    therefore yields 401 (the session is already gone) — the frontend treats
    that as logged out.
    """
    settings = request.app.state.settings
    ip = client_ip(request)
    raw_token = request.cookies.get(settings.session_cookie_name)
    await revoke_session(db, raw_token)
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action="auth.logout",
        resource_type="staff_user",
        resource_id=str(principal.user_id),
        outcome="success",
        request_id=request_id,
        ip=ip,
    )
    await db.commit()

    response = Response(status_code=http_status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response, request, settings)
    return response


@router.get("/me", response_model=UserPayload, status_code=http_status.HTTP_200_OK)
async def me(
    db: DbSession,
    principal: Principal = Depends(require_principal),
) -> UserPayload:
    """Current user payload; 401 when anonymous.

    This is the frontend's CSRF bootstrap call: the CSRF middleware issues the
    `csrf_token` cookie on this (and any other) GET that arrives without one.
    """
    user_id = principal.user_uuid
    user = await get_staff_user(db, user_id) if user_id else None
    if user is None:  # pragma: no cover - resolver only returns live users
        raise Unauthenticated()
    roles = await get_user_roles(db, user.id)
    return to_user_payload(user, roles)
