"""Staff and role payloads (docs/API.md staff endpoints).

Staff payloads never include password hashes or tokens. Request bodies reject
unknown fields (`extra="forbid"`), so a PATCH can never smuggle a role change
and a role change can never smuggle a password.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Timestamp

RoleKey = Literal["doctor", "nurse", "clinical_admin"]


class StaffUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    roles: list[str] = Field(default_factory=list)
    is_active: bool
    created_at: Timestamp
    updated_at: Timestamp


class StaffCreate(BaseModel):
    """Create a staff account.

    Password source (flagged per design decision): an optional request field
    `password` (8..128 chars). When it is omitted, the server generates a strong
    random password and returns it ONCE in `StaffCreateOut.generated_password`.
    The plaintext is never stored, logged, or audited — only its argon2id hash.
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True
    password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class StaffCreateOut(BaseModel):
    """201 body for POST /staff: the account plus a one-time generated password."""

    user: StaffUserOut
    generated_password: str | None = None


class StaffUpdate(BaseModel):
    """PATCH /staff/{user_id}: display name and/or activation only.

    Roles cannot be changed here by design (extra="forbid"): role changes go
    through the dedicated role endpoints so self-escalation guards cannot be
    bypassed.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class RoleAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: RoleKey
