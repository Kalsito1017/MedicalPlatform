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
EXPECTED_MATRIX: dict[str, dict[str, bool]] = {
    "patient:read": {"doctor": True, "nurse": True, "clinical_admin": False, "tech_admin": False},
    "lab:read": {"doctor": True, "nurse": True, "clinical_admin": False, "tech_admin": False},
    "prescription:read": {"doctor": True, "nurse": True, "clinical_admin": False, "tech_admin": False},
    "staff:read": {"doctor": False, "nurse": True, "clinical_admin": True, "tech_admin": True},
    "staff:manage": {"doctor": False, "nurse": False, "clinical_admin": True, "tech_admin": True},
    "role:assign": {"doctor": False, "nurse": False, "clinical_admin": True, "tech_admin": True},
    "audit:read": {"doctor": False, "nurse": False, "clinical_admin": True, "tech_admin": True},
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
    assert set(ALL_ROLES) == {"doctor", "nurse", "clinical_admin", "tech_admin"}
    assert set(ALL_PERMISSIONS) == set(EXPECTED_MATRIX)


@pytest.mark.parametrize("permission", sorted(EXPECTED_MATRIX))
@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
def test_role_permission_matrix(role: str, permission: str) -> None:
    expected = EXPECTED_MATRIX[permission][role]
    assert has_permission([role], permission) is expected
    assert (permission in ROLE_PERMISSIONS[role]) is expected


def test_matrix_exactly_matches_expected_sets() -> None:
    expected_sets = {
        "doctor": {"patient:read", "lab:read", "prescription:read"},
        "nurse": {"patient:read", "lab:read", "prescription:read", "staff:read"},
        "clinical_admin": {"staff:read", "staff:manage", "role:assign", "audit:read"},
        "tech_admin": {"staff:read", "staff:manage", "role:assign", "audit:read"},
    }
    assert {role: set(perms) for role, perms in ROLE_PERMISSIONS.items()} == expected_sets


def test_tech_admin_has_no_clinical_access() -> None:
    for permission in ("patient:read", "lab:read", "prescription:read"):
        assert has_permission(["tech_admin"], permission) is False
    for permission in ("staff:read", "staff:manage", "role:assign", "audit:read"):
        assert has_permission(["clinical_admin"], permission) is True


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
    dependency = require_permission("staff:read")
    assert await dependency(request) is principal


async def test_require_permission_accepts_permission_enum() -> None:
    policy.principal_resolver = _resolver(Principal(user_id="u1", roles=("clinical_admin",)))
    dependency = require_permission(policy.Permission.AUDIT_READ)
    assert (await dependency(_request())).roles == ("clinical_admin",)


def _resolver(principal: Principal | None):
    async def _get(_request: Request) -> Principal | None:
        return principal

    return _get
