"""Voice transcription schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VoiceLanguage = Literal["en", "bg"]


class TranscriptionOut(BaseModel):
    """One bounded speech-to-text result. Audio is never persisted or returned."""

    text: str
    language: VoiceLanguage
    model: str
    duration_seconds: float = Field(ge=0)
