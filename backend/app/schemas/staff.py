"""Staff and role payloads (docs/API.md staff endpoints).

Staff payloads never include password hashes or tokens.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Timestamp

RoleKey = Literal["doctor", "nurse", "clinical_admin", "tech_admin"]


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
    """Create a staff account. Password is set via seeded env or invite flow."""

    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class StaffUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class RoleAssignment(BaseModel):
    role: RoleKey
