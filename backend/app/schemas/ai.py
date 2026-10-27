"""AI assistant payloads (docs/API.md "AI assistant").

`POST /api/v1/ai/ask` accepts two modes over ONE endpoint:

* patient-scoped: body `{"patient_id", "question"}` - one bounded question
  about one synthetic patient the caller may read,
* general lookup: body `{"question"}` - a bounded question across the
  synthetic patients the caller is authorized to read.

Both modes respond with exactly `{"answer", "used_tools", "citations", "model",
"ai_generated": true, "disclaimer"}`. `used_tools` lists the MCP tools actually
called; `citations` reference the synthetic records used
(patient/encounter/lab/prescription ids) and may span multiple patients in
general mode; `disclaimer` is the non-clinical-use notice. `question` is always
1..QUESTION_MAX_CHARS characters and never blank.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

QUESTION_MAX_CHARS = 2000

DISCLAIMER = (
    "AI-generated answer based on synthetic demo data. Not a diagnosis, treatment "
    "recommendation, or validated clinical decision - and not for clinical use."
)

CitationType = Literal["patient", "encounter", "clinical_note", "lab_result", "prescription"]


class Citation(BaseModel):
    """Reference to one synthetic record used to build the answer."""

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
    """Exact frozen response shape (docs/API.md). Six keys, nothing else."""

    answer: str
    used_tools: list[str]
    citations: list[Citation]
    model: str
    ai_generated: Literal[True] = True
    disclaimer: str
