"""System-log API payloads (docs/API.md "System Logs")."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Timestamp


class SystemEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^(audit|op):[0-9a-f-]{36}$")
    timestamp: Timestamp
    severity: Literal["info", "success", "warning", "error", "security"]
    category: Literal[
        "auth",
        "security",
        "patients",
        "encounters",
        "labs",
        "prescriptions",
        "appointments",
        "ai",
        "mcp",
        "voice",
        "administration",
        "system",
    ]
    event_type: str
    description: str
    actor_id: UUID | None = None
    resource_type: str | None = None
    resource_reference: str | None = None
    request_id: str | None = None
    outcome: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SystemLogCountsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    info: int = Field(ge=0)
    success: int = Field(ge=0)
    warning: int = Field(ge=0)
    error: int = Field(ge=0)
    security: int = Field(ge=0)


class SystemLogPageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SystemEventOut]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    counts: SystemLogCountsOut
    start: Timestamp
    end: Timestamp
    timezone: str = Field(min_length=1, max_length=64)
