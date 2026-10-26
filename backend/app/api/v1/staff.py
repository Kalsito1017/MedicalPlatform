"""Staff and role administration endpoints (docs/API.md).

Authorization: `staff:read` / `staff:manage` / `role:assign` via
`require_permission` (401 anonymous, 403 denied). Mutation endpoints are also
CSRF-protected (see app.auth.csrf).

Guards (audited):
- Self role escalation is refused with 403 "Self role assignment is not
  permitted" whenever the grant would add permissions the actor does not hold.
- Removing the last active administrator is refused with 409 (at least one
  active clinical_admin/tech_admin must remain).
- Deactivating an account revokes all of that account's sessions immediately.
- PATCH cannot change roles (the schema rejects unknown fields).

Role changes take effect for existing sessions on their NEXT request:
permissions are recomputed from the database per request (app.auth.identity),
nothing is cached in the cookie (docs/ARCHITECTURE.md (c)).

Password source for POST /staff (flagged in app.schemas.staff.StaffCreate):
optional request field `password`; when omitted the server generates a strong
random password returned ONCE in the 201 body. Plaintext is never logged or
audited — only the argon2id hash is stored.
"""
from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi import status as http_status

from app.api.deps import DbSession, Pagination, RequestId, reject_unknown_query
from app.audit.logger import write_audit_event
from app.auth.identity import client_ip
from app.auth.passwords import hash_password
from app.authorization.policy import (
    Principal,
    permissions_for_roles,
    require_permission,
)
from app.errors import Conflict, Forbidden, NotFound
from app.schemas.common import Page
from app.schemas.staff import (
    RoleAssignment,
    RoleKey,
    StaffCreate,
    StaffCreateOut,
    StaffUpdate,
    StaffUserOut,
)
from app.services import staff as staff_service

router = APIRouter(prefix="/staff", tags=["staff"])

SELF_ASSIGNMENT_MESSAGE = "Self role assignment is not permitted"


async def _audit(
    db,
    *,
    request: Request,
    request_id: str,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
) -> None:
    await write_audit_event(
        db,
        actor_user_id=principal.user_uuid,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()


async def _load_user_or_404(db, user_id: uuid.UUID):
    user = await staff_service.get_staff_user(db, user_id)
    if user is None:
        raise NotFound()
    return user


@router.get("", response_model=Page[StaffUserOut])
async def list_staff(
    db: DbSession,
    pagination: Pagination,
    _unknown: None = Depends(reject_unknown_query("limit", "offset")),
    _principal: Principal = Depends(require_permission("staff:read")),
) -> Page[StaffUserOut]:
    """Paginated staff accounts. Never returns password hashes or tokens."""
    users, total = await staff_service.list_staff_users(
        db, limit=pagination.limit, offset=pagination.offset
    )
    roles_by_user = await staff_service.get_roles_for_users(db, [user.id for user in users])
    return Page[StaffUserOut](
        items=[staff_service.to_staff_user_out(user, roles_by_user.get(user.id, [])) for user in users],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("", response_model=StaffCreateOut, status_code=http_status.HTTP_201_CREATED)
async def create_staff(
    payload: StaffCreate,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("staff:manage")),
) -> StaffCreateOut:
    generated_password: str | None = None
    plaintext_password = payload.password
    if plaintext_password is None:
        generated_password = secrets.token_urlsafe(12)
        plaintext_password = generated_password

    user = await staff_service.create_staff_user(
        db,
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(plaintext_password),
        is_active=payload.is_active,
    )
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="staff.create",
        resource_type="staff_user",
        resource_id=str(user.id),
        outcome="success",
    )
    return StaffCreateOut(
        user=staff_service.to_staff_user_out(user, []),
        generated_password=generated_password,
    )


@router.patch("/{user_id}", response_model=StaffUserOut)
async def update_staff(
    user_id: uuid.UUID,
    payload: StaffUpdate,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("staff:manage")),
) -> StaffUserOut:
    user = await _load_user_or_404(db, user_id)
    was_active = user.is_active
    await staff_service.update_staff_user(
        db, user, display_name=payload.display_name, is_active=payload.is_active
    )

    if payload.is_active is False and was_active:
        # Deactivation is effective immediately: every session is revoked and
        # the user also stops resolving because inactive users are rejected.
        await staff_service.deactivate_user_sessions(db, user.id)
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="staff.deactivate",
            resource_type="staff_user",
            resource_id=str(user.id),
            outcome="success",
        )
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="session.revoke_all",
            resource_type="staff_user",
            resource_id=str(user.id),
            outcome="success",
        )
    else:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="staff.update",
            resource_type="staff_user",
            resource_id=str(user.id),
            outcome="success",
        )

    roles = await staff_service.get_user_roles(db, user.id)
    return staff_service.to_staff_user_out(user, roles)


@router.post("/{user_id}/roles", response_model=StaffUserOut)
async def assign_role(
    user_id: uuid.UUID,
    payload: RoleAssignment,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("role:assign")),
) -> StaffUserOut:
    user = await _load_user_or_404(db, user_id)
    current_roles = await staff_service.get_user_roles(db, user.id)

    if user.id == principal.user_uuid:
        # Self-escalation guard: refuse when the grant adds permissions the
        # actor does not already hold (docs/SECURITY.md threat model).
        would_add = permissions_for_roles([payload.role]) - permissions_for_roles(current_roles)
        if would_add:
            await _audit(
                db,
                request=request,
                request_id=request_id,
                principal=principal,
                action="authz.denied",
                resource_type="user_role",
                resource_id=f"{user.id}:{payload.role}",
                outcome="denied",
            )
            raise Forbidden(SELF_ASSIGNMENT_MESSAGE)

    created = await staff_service.grant_role(
        db, user, payload.role, granted_by=principal.user_uuid
    )
    # Duplicate grants are idempotent: 200 with the unchanged account (no
    # second grant row is created).
    if created:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="role.grant",
            resource_type="user_role",
            resource_id=f"{user.id}:{payload.role}",
            outcome="success",
        )
    roles = await staff_service.get_user_roles(db, user.id)
    return staff_service.to_staff_user_out(user, roles)


@router.delete("/{user_id}/roles/{role}", status_code=http_status.HTTP_204_NO_CONTENT)
async def remove_role(
    user_id: uuid.UUID,
    role: RoleKey,
    request: Request,
    db: DbSession,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("role:assign")),
) -> Response:
    user = await _load_user_or_404(db, user_id)
    try:
        removed = await staff_service.revoke_role(db, user, role, loc="path.role")
    except Conflict:
        await _audit(
            db,
            request=request,
            request_id=request_id,
            principal=principal,
            action="role.revoke",
            resource_type="user_role",
            resource_id=f"{user.id}:{role}",
            outcome="denied",
        )
        raise
    if not removed:
        # The user did not hold the role: nothing to revoke (concealed as 404).
        raise NotFound()
    await _audit(
        db,
        request=request,
        request_id=request_id,
        principal=principal,
        action="role.revoke",
        resource_type="user_role",
        resource_id=f"{user.id}:{role}",
        outcome="success",
    )
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
