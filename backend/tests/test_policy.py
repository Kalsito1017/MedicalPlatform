"""Permission policy tests: exact matrix from docs/API.md and deny-by-default."""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.authorization import policy
from app.authorization.policy import (
    ALL_PERMISSIONS,
    ALL_ROLES,
    ROLE_PERMISSIONS,
    Principal,
    has_permission,
    permissions_for_roles,
    require_permission,
)
from app.errors import Forbidden, Unauthenticated

# Transcribed from docs/API.md "Permission matrix" (frozen contract).
# Three roles only (owner decision, 2026-10-26: tech_admin removed entirely);
# `analytics:read` is declared now for the upcoming analytics page.
EXPECTED_MATRIX: dict[str, dict[str, bool]] = {
    "patient:read": {"doctor": True, "nurse": True, "clinical_admin": True},
    "patient:write": {"doctor": True, "nurse": False, "clinical_admin": True},
    "patient:delete": {"doctor": False, "nurse": False, "clinical_admin": True},
    "message:read": {"doctor": True, "nurse": True, "clinical_admin": True},
    "message:write": {"doctor": True, "nurse": True, "clinical_admin": True},
    "logs:read": {"doctor": False, "nurse": False, "clinical_admin": True},
    "lab:read": {"doctor": True, "nurse": True, "clinical_admin": True},
    "prescription:read": {"doctor": True, "nurse": True, "clinical_admin": True},
    "staff:read": {"doctor": False, "nurse": False, "clinical_admin": True},
    "staff:manage": {"doctor": False, "nurse": False, "clinical_admin": True},
    "role:assign": {"doctor": False, "nurse": False, "clinical_admin": True},
    "audit:read": {"doctor": False, "nurse": False, "clinical_admin": True},
    "analytics:read": {"doctor": False, "nurse": False, "clinical_admin": True},
    "appointment:read": {"doctor": True, "nurse": True, "clinical_admin": True},
    "appointment:write": {"doctor": True, "nurse": False, "clinical_admin": True},
    "appointment:manage_availability": {
        "doctor": True,
        "nurse": False,
        "clinical_admin": True,
    },
    "appointment:export": {"doctor": True, "nurse": False, "clinical_admin": True},
}


def _request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "state": {},
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _reset_principal_resolver():
    policy.principal_resolver = None
    yield
    policy.principal_resolver = None


def test_roles_and_permissions_match_contract() -> None:
    assert set(ALL_ROLES) == {"doctor", "nurse", "clinical_admin"}
    assert set(ALL_PERMISSIONS) == set(EXPECTED_MATRIX)


@pytest.mark.parametrize("permission", sorted(EXPECTED_MATRIX))
@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
def test_role_permission_matrix(role: str, permission: str) -> None:
    expected = EXPECTED_MATRIX[permission][role]
    assert has_permission([role], permission) is expected
    assert (permission in ROLE_PERMISSIONS[role]) is expected


def test_matrix_exactly_matches_expected_sets() -> None:
    expected_sets = {
        "doctor": {
            "patient:read",
            "patient:write",
            "message:read",
            "message:write",
            "lab:read",
            "prescription:read",
            "appointment:read",
            "appointment:write",
            "appointment:manage_availability",
            "appointment:export",
        },
        "nurse": {
            "patient:read",
            "message:read",
            "message:write",
            "lab:read",
            "prescription:read",
            "appointment:read",
        },
        "clinical_admin": set(EXPECTED_MATRIX),
    }
    assert {role: set(perms) for role, perms in ROLE_PERMISSIONS.items()} == expected_sets


def test_tech_admin_role_is_gone() -> None:
    # tech_admin was deleted from the system entirely (owner decision, 2026-10-26).
    assert "tech_admin" not in ALL_ROLES
    assert "tech_admin" not in ROLE_PERMISSIONS
    assert "tech_admin" not in policy.RoleLiteral.__args__
    for permission in sorted(EXPECTED_MATRIX):
        assert has_permission(["tech_admin"], permission) is False
    assert permissions_for_roles(["tech_admin"]) == frozenset()


def test_admin_role_has_every_permission() -> None:
    for permission in sorted(EXPECTED_MATRIX):
        assert has_permission(["clinical_admin"], permission) is True
    assert permissions_for_roles(["clinical_admin"]) == frozenset(ALL_PERMISSIONS)


def test_deny_by_default() -> None:
    # No roles, unknown roles, unknown permissions: everything denied.
    assert permissions_for_roles([]) == frozenset()
    assert has_permission([], "patient:read") is False
    assert has_permission(["superuser", "root"], "patient:read") is False
    assert has_permission(["doctor"], "patient:delete") is False
    assert has_permission(["doctor"], "") is False
    assert has_permission(["tech_admin"], "*") is False


def test_multiple_roles_union_of_grants() -> None:
    assert has_permission(["doctor", "clinical_admin"], "staff:manage") is True
    assert has_permission(["doctor", "clinical_admin"], "patient:read") is True
    assert has_permission(["nurse"], "role:assign") is False


async def test_require_permission_denies_without_principal() -> None:
    dependency = require_permission("patient:read")
    with pytest.raises(Unauthenticated):
        await dependency(_request())


async def test_require_permission_denies_inactive_principal() -> None:
    policy.principal_resolver = _resolver(Principal(user_id="u1", roles=("doctor",), is_active=False))
    dependency = require_permission("patient:read")
    with pytest.raises(Unauthenticated):
        await dependency(_request())


async def test_require_permission_allows_granted_role() -> None:
    principal = Principal(user_id="u1", roles=("doctor",))
    policy.principal_resolver = _resolver(principal)
    dependency = require_permission("patient:read")
    assert await dependency(_request()) is principal


async def test_require_permission_forbids_ungranted_role() -> None:
    policy.principal_resolver = _resolver(Principal(user_id="u1", roles=("doctor",)))
    dependency = require_permission("staff:manage")
    with pytest.raises(Forbidden):
        await dependency(_request())


async def test_require_permission_reads_request_state_first() -> None:
    async def _should_not_be_called(_request: Request) -> Principal:  # pragma: no cover
        raise AssertionError("resolver must not be called when request.state.principal exists")

    policy.principal_resolver = _should_not_be_called
    request = _request()
    principal = Principal(user_id="u2", roles=("nurse",))
    request.state.principal = principal
    dependency = require_permission("patient:read")
    assert await dependency(request) is principal


async def test_require_permission_accepts_permission_enum() -> None:
    policy.principal_resolver = _resolver(Principal(user_id="u1", roles=("clinical_admin",)))
    dependency = require_permission(policy.Permission.AUDIT_READ)
    assert (await dependency(_request())).roles == ("clinical_admin",)


def _resolver(principal: Principal | None):
    async def _get(_request: Request) -> Principal | None:
        return principal

    return _get
