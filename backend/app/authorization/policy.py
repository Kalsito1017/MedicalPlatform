"""Role/permission policy — source of truth for the docs/API.md permission matrix.

| Permission        | doctor | nurse | clinical_admin | tech_admin |
|-------------------|--------|-------|----------------|------------|
| patient:read      | yes    | yes   | no             | no         |
| lab:read          | yes    | yes   | no             | no         |
| prescription:read | yes    | yes   | no             | no         |
| staff:read        | no     | yes   | yes            | yes        |
| staff:manage      | no     | no    | yes            | yes        |
| role:assign       | no     | no    | yes            | yes        |
| audit:read        | no     | no    | yes            | yes        |

Deny by default: unknown roles have no permissions, unknown permissions are
never granted, and the absence of an authenticated principal is a denial.

`require_permission(...)` is the shared FastAPI dependency. The Phase 5 auth
agent wires `principal_resolver` (below) to the verified server-side session
lookup; until then requests are denied (401) — never allowed by default.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from fastapi import Request

from app.errors import Forbidden, Unauthenticated

PermissionLiteral = Literal[
    "patient:read",
    "lab:read",
    "prescription:read",
    "staff:read",
    "staff:manage",
    "role:assign",
    "audit:read",
]

RoleLiteral = Literal["doctor", "nurse", "clinical_admin", "tech_admin"]


class Permission(str, Enum):
    PATIENT_READ = "patient:read"
    LAB_READ = "lab:read"
    PRESCRIPTION_READ = "prescription:read"
    STAFF_READ = "staff:read"
    STAFF_MANAGE = "staff:manage"
    ROLE_ASSIGN = "role:assign"
    AUDIT_READ = "audit:read"


class Role(str, Enum):
    DOCTOR = "doctor"
    NURSE = "nurse"
    CLINICAL_ADMIN = "clinical_admin"
    TECH_ADMIN = "tech_admin"


ALL_PERMISSIONS: tuple[str, ...] = tuple(permission.value for permission in Permission)
ALL_ROLES: tuple[str, ...] = tuple(role.value for role in Role)

# Exact matrix from docs/API.md. Keys are role keys; values are permission sets.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.DOCTOR.value: frozenset(
        {
            Permission.PATIENT_READ.value,
            Permission.LAB_READ.value,
            Permission.PRESCRIPTION_READ.value,
        }
    ),
    Role.NURSE.value: frozenset(
        {
            Permission.PATIENT_READ.value,
            Permission.LAB_READ.value,
            Permission.PRESCRIPTION_READ.value,
            Permission.STAFF_READ.value,
        }
    ),
    Role.CLINICAL_ADMIN.value: frozenset(
        {
            Permission.STAFF_READ.value,
            Permission.STAFF_MANAGE.value,
            Permission.ROLE_ASSIGN.value,
            Permission.AUDIT_READ.value,
        }
    ),
    Role.TECH_ADMIN.value: frozenset(
        {
            Permission.STAFF_READ.value,
            Permission.STAFF_MANAGE.value,
            Permission.ROLE_ASSIGN.value,
            Permission.AUDIT_READ.value,
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    """Verified acting identity. Built server-side only, never from request input."""

    user_id: str | None
    roles: tuple[str, ...] = field(default_factory=tuple)
    is_active: bool = True


# Extension point for the Phase 5 auth agent: set this to a callable that
# resolves the verified session identity for a request. None => nobody is ever
# authenticated (deny by default).
principal_resolver: Callable[[Request], Awaitable[Principal | None]] | None = None


def permissions_for_roles(roles: Iterable[str]) -> frozenset[str]:
    """Effective permissions for a set of role keys. Unknown roles contribute nothing."""
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(permissions)


def has_permission(roles: Iterable[str], permission: PermissionLiteral | Permission | str) -> bool:
    """Deny by default: only an explicit policy grant allows the action."""
    permission_key = permission.value if isinstance(permission, Permission) else str(permission)
    if permission_key not in ALL_PERMISSIONS:
        return False
    return permission_key in permissions_for_roles(roles)


def require_permission(permission: PermissionLiteral | Permission) -> Callable[[Request], Awaitable[Principal]]:
    """FastAPI dependency factory enforcing a single permission.

    Raises 401 (unauthenticated) when no verified principal exists and 403
    (forbidden) when the principal's roles do not grant the permission. The
    resolved Principal is returned so handlers can use it for auditing.

    Identity resolution order:
      1. request.state.principal, if a middleware/dependency already set it;
      2. the module-level principal_resolver hook (Phase 5 auth agent wires it).
    """

    async def _dependency(request: Request) -> Principal:
        principal: Principal | None = getattr(request.state, "principal", None)
        if principal is None and principal_resolver is not None:
            principal = await principal_resolver(request)
        if principal is None or not principal.is_active:
            raise Unauthenticated()
        if not has_permission(principal.roles, permission):
            raise Forbidden()
        return principal

    return _dependency


def role_keys() -> Sequence[str]:
    """All known role keys (used by seeding and validation)."""
    return ALL_ROLES
