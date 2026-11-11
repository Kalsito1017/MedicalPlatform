"""AI assistant payloads (docs/API.md "AI assistant").

`POST /api/v1/ai/ask` accepts two modes over ONE endpoint:

* patient-scoped: body `{"patient_id", "question"}` - one bounded question
  about one patient the caller may read,
* general lookup: body `{"question"}` - a bounded question across the
  patients the caller is authorized to read.

Both modes respond with exactly `{"answer", "used_tools", "citations", "model",
"ai_generated": true}`. `used_tools` lists the MCP tools actually
called; `citations` reference the records used
(patient/encounter/lab/prescription ids) and may span multiple patients in
general mode. `question` is always 1..QUESTION_MAX_CHARS characters and never
blank.

History (`GET/DELETE /api/v1/ai/conversations`) returns the caller's own
persisted exchanges in the standard pagination envelope
(`{"items", "total", "limit", "offset"}`); each item is `ConversationOut`.
History is per-user private workflow data (see app.models.ai for the privacy
position) - never audit data, never another user's rows.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Timestamp

QUESTION_MAX_CHARS = 2000

CitationType = Literal["patient", "encounter", "clinical_note", "lab_result", "prescription"]


class Citation(BaseModel):
    """Reference to one record used to build the answer."""

    type: CitationType
    id: str


class AskRequest(BaseModel):
    """One bounded question: patient-scoped (patient_id) or general lookup."""

    patient_id: UUID | None = None
    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class AskResponse(BaseModel):
    """Exact frozen response shape (docs/API.md). Five keys, nothing else."""

    answer: str
    used_tools: list[str]
    citations: list[Citation]
    model: str
    ai_generated: Literal[True] = True


class ConversationOut(BaseModel):
    """One persisted exchange from the caller's own AI history.

    `patient_id` is NULL for general-lookup questions. `used_tools` and
    `citations` mirror the provenance returned with the original answer.
    """

    id: UUID
    patient_id: UUID | None = None
    question: str
    answer: str
    used_tools: list[str]
    citations: list[Citation]
    model: str
    created_at: Timestamp
