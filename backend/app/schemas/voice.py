"""Voice transcription schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# A manual override the doctor can still choose; `auto` lets whisper.cpp detect
# the spoken language. The response reports the detected/selected code.
VoiceLanguage = Literal["en", "bg"]
VoiceLanguageRequest = Literal["en", "bg", "auto"]


class TranscriptionOut(BaseModel):
    """One bounded speech-to-text result. Audio is never persisted or returned."""

    text: str
    language: VoiceLanguage
    model: str
    duration_seconds: float = Field(ge=0)
