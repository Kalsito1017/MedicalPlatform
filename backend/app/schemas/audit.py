"""Audit event payloads (docs/API.md audit endpoint).

No clinical payloads and no secrets ever appear in audit responses.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.common import Timestamp


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    outcome: Literal["success", "denied", "error"]
    request_id: str | None = None
    ip: str | None = None
    created_at: Timestamp
