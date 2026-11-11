"""Internal staff message payloads (docs/API.md "Messages")."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Timestamp

MessageBox = Literal["inbox", "sent"]
MESSAGE_SUBJECT_MAX = 200
MESSAGE_BODY_MAX = 5000


class MessageCreate(BaseModel):
    """Send one staff-to-staff message; no attachments or patient payloads."""

    model_config = ConfigDict(extra="forbid")

    recipient_id: UUID
    subject: str | None = Field(default=None, max_length=MESSAGE_SUBJECT_MAX)
    body: str = Field(min_length=1, max_length=MESSAGE_BODY_MAX)

    @field_validator("subject")
    @classmethod
    def _strip_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("body")
    @classmethod
    def _strip_body(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message body must not be blank")
        return stripped


class MessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    sender_id: UUID
    sender_display_name: str
    recipient_id: UUID
    recipient_display_name: str
    subject: str | None = None
    body: str
    read_at: Timestamp | None = None
    created_at: Timestamp
    updated_at: Timestamp


class MessageRecipientOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    display_name: str
    roles: list[str] = Field(default_factory=list)


class MessageRecipientListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MessageRecipientOut] = Field(max_length=100)
