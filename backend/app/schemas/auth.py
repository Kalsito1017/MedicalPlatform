"""Auth request/response payloads (docs/API.md auth endpoints).

The user payload is exactly
{"id", "email", "display_name", "roles", "permissions", "is_active"}.
Submitted credentials are never echoed back (validation errors carry field
locations only) and never logged or audited.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    """POST /api/v1/auth/login body. Unknown fields are rejected (extra=forbid)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class UserPayload(BaseModel):
    """Current user, roles, and effective permissions (docs/API.md)."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    email: str
    display_name: str
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    is_active: bool
