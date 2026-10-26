"""Staff account and role business logic (docs/API.md staff & roles).

Handlers stay thin: validation/authorization at the API layer, persistence and
invariants here. Staff payloads built from these rows never include
`password_hash`, session tokens, or any secret.

Role-change semantics (docs/ARCHITECTURE.md (c)): permissions are recomputed
from the database on every request, so grants and revocations take effect for
existing sessions on their NEXT request — no session invalidation needed and no
stale grants are cached in the cookie.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import revoke_all_sessions
from app.authorization.policy import permissions_for_roles
from app.errors import Conflict, ValidationFailed
from app.models.admin import Role, StaffUser, UserRole
from app.schemas.auth import UserPayload
from app.schemas.staff import StaffUserOut

# Roles that confer administrative authority; at least one active, non-deleted
# user holding one of these must always remain (last-admin protection).
ADMIN_ROLE_KEYS: frozenset[str] = frozenset({"clinical_admin", "tech_admin"})


async def get_user_roles(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Role keys granted to a user (alphabetical)."""
    rows = await db.scalars(
        select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.key)
    )
    return list(rows)


async def get_roles_for_users(
    db: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Role keys per user id (for staff list payloads). Empty mapping when none."""
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(UserRole.user_id, Role.key)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids))
            .order_by(UserRole.user_id, Role.key)
        )
    ).all()
    mapping: dict[uuid.UUID, list[str]] = {}
    for user_id, key in rows:
        mapping.setdefault(user_id, []).append(key)
    return mapping


async def get_staff_user(db: AsyncSession, user_id: uuid.UUID) -> StaffUser | None:
    """Staff account by id, excluding soft-deleted rows."""
    return await db.scalar(select(StaffUser).where(StaffUser.id == user_id, StaffUser.not_deleted()))


async def get_staff_user_by_email(db: AsyncSession, email: str) -> StaffUser | None:
    """Staff account by normalized email, excluding soft-deleted rows."""
    return await db.scalar(
        select(StaffUser).where(StaffUser.email == email.strip().lower(), StaffUser.not_deleted())
    )


async def list_staff_users(
    db: AsyncSession, *, limit: int, offset: int
) -> tuple[list[StaffUser], int]:
    """Paginated staff list (soft-deleted excluded), ordered by email."""
    base = select(StaffUser).where(StaffUser.not_deleted())
    total = (await db.scalar(select(func.count()).select_from(base.subquery()))) or 0
    rows = list((await db.scalars(base.order_by(StaffUser.email).limit(limit).offset(offset))).all())
    return rows, int(total)


async def create_staff_user(
    db: AsyncSession,
    *,
    email: str,
    display_name: str,
    password_hash: str,
    is_active: bool = True,
) -> StaffUser:
    """Create an account. Raises Conflict when the email is taken."""
    existing = await db.scalar(select(StaffUser.id).where(StaffUser.email == email.strip().lower()))
    if existing is not None:
        raise Conflict("An account with this email already exists.")
    user = StaffUser(
        email=email,
        display_name=display_name,
        password_hash=password_hash,
        is_active=is_active,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("An account with this email already exists.") from None
    return user


async def update_staff_user(
    db: AsyncSession,
    user: StaffUser,
    *,
    display_name: str | None = None,
    is_active: bool | None = None,
) -> StaffUser:
    """Update display name and/or activation. Never touches roles or secrets."""
    if display_name is not None:
        user.display_name = display_name
    if is_active is not None:
        user.is_active = is_active
    await db.flush()
    # Reload so the server-side `updated_at` is populated without lazy IO later.
    await db.refresh(user)
    return user


async def _role_by_key(db: AsyncSession, role_key: str, *, loc: str = "body.role") -> Role:
    role = await db.scalar(select(Role).where(Role.key == role_key))
    if role is None:
        raise ValidationFailed(
            "Malformed input.",
            details={"fields": [{"loc": loc, "msg": "Unknown role.", "type": "unknown_role"}]},
        )
    return role


async def grant_role(
    db: AsyncSession, user: StaffUser, role_key: str, *, granted_by: uuid.UUID | None
) -> bool:
    """Grant a role. Idempotent: an existing grant is left untouched (200).

    Returns True when a new grant was created, False when it already existed.
    """
    role = await _role_by_key(db, role_key)
    existing = await db.get(UserRole, {"user_id": user.id, "role_id": role.id})
    if existing is not None:
        return False
    db.add(UserRole(user_id=user.id, role_id=role.id, granted_by=granted_by))
    await db.flush()
    return True


async def revoke_role(db: AsyncSession, user: StaffUser, role_key: str, *, loc: str = "body.role") -> bool:
    """Revoke a role, enforcing last-admin protection.

    Returns True when a grant was removed, False when the user did not hold it.
    Raises Conflict when the removal would leave no active administrator
    (any active, non-deleted user holding clinical_admin or tech_admin).
    """
    role = await _role_by_key(db, role_key, loc=loc)
    grant = await db.get(UserRole, {"user_id": user.id, "role_id": role.id})
    if grant is None:
        return False
    if role.key in ADMIN_ROLE_KEYS:
        remaining_holders = await db.scalar(
            select(func.count(func.distinct(UserRole.user_id)))
            .join(StaffUser, StaffUser.id == UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                Role.key.in_(sorted(ADMIN_ROLE_KEYS)),
                StaffUser.is_active.is_(True),
                StaffUser.not_deleted(),
                UserRole.user_id != user.id,
            )
        )
        target_keeps_admin = await db.scalar(
            select(func.count())
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == user.id,
                Role.key.in_(sorted(ADMIN_ROLE_KEYS)),
                Role.key != role.key,
            )
        )
        if not remaining_holders and not target_keeps_admin:
            raise Conflict(
                "At least one active administrator must remain. "
                "Grant another administrator before removing this role."
            )
    await db.delete(grant)
    await db.flush()
    return True


async def deactivate_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every session of a user (service-layer seam over auth.sessions)."""
    return await revoke_all_sessions(db, user_id)


def to_staff_user_out(user: StaffUser, roles: list[str]) -> StaffUserOut:
    """API view of a staff account. Never includes password hashes or tokens."""
    return StaffUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=sorted(roles),
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def to_user_payload(user: StaffUser, roles: list[str]) -> UserPayload:
    """The docs/API.md user payload: id, email, display_name, roles, permissions, is_active."""
    return UserPayload(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=sorted(roles),
        permissions=sorted(permissions_for_roles(roles)),
        is_active=user.is_active,
    )
