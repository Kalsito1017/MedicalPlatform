"""Authenticated voice transcription for appointment scheduling.

`POST /api/v1/voice/transcriptions` accepts one bounded audio recording plus a
language (`en`, `bg`, or `auto`) and returns editable text. With `auto` the
whisper.cpp model detects Bulgarian or English from the audio; the response
reports the detected language. The browser records with MediaRecorder; the
server converts with FFmpeg and calls the private whisper.cpp service. Audio
and transcripts are never logged, audited, or persisted. The final appointment
is only created after an explicit doctor confirmation through the normal
appointment API.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.api.deps import RequestId
from app.auth.identity import client_ip
from app.authorization.policy import Principal, require_permission
from app.errors import DependencyUnavailable, RateLimited, ValidationFailed
from app.logs.service import record_operational_event
from app.schemas.voice import TranscriptionOut, VoiceLanguageRequest
from app.services.voice import SpeechTimeout, transcribe_upload, voice_rate_limiter

router = APIRouter(tags=["voice"])


@router.post("/voice/transcriptions", response_model=TranscriptionOut)
async def create_transcription(
    request: Request,
    request_id: RequestId,
    file: UploadFile = File(...),
    language: VoiceLanguageRequest = Form(...),
    principal: Principal = Depends(require_permission("appointment:write")),
) -> TranscriptionOut:
    settings = request.app.state.settings
    rate_key = principal.user_id or client_ip(request) or "unknown"
    retry_after = voice_rate_limiter.retry_after_seconds(rate_key)
    if retry_after is not None:
        await record_operational_event(
            event_type="voice.transcription.rate_limited",
            actor_id=principal.user_uuid,
            request_id=request_id,
            outcome="warning",
            metadata={"reason": "rate_limit", "language": language},
        )
        raise RateLimited(
            "Too many transcription requests. Please retry later.",
            retry_after=retry_after,
        )
    voice_rate_limiter.record(rate_key)
    started = time.perf_counter()
    await record_operational_event(
        event_type="voice.transcription.started",
        actor_id=principal.user_uuid,
        request_id=request_id,
        outcome="info",
        metadata={"language": language, "provider": "whisper"},
    )
    try:
        result = await transcribe_upload(
            upload=file,
            language=language,
            settings=settings,
        )
    except SpeechTimeout:
        await record_operational_event(
            event_type="voice.transcription.timeout",
            actor_id=principal.user_uuid,
            request_id=request_id,
            outcome="error",
            metadata={
                "language": language,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error_code": "timeout",
                "provider": "whisper",
            },
        )
        raise
    except DependencyUnavailable:
        await record_operational_event(
            event_type="voice.transcription.unavailable",
            actor_id=principal.user_uuid,
            request_id=request_id,
            outcome="error",
            metadata={
                "language": language,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error_code": "dependency_unavailable",
                "provider": "whisper",
            },
        )
        raise
    except ValidationFailed:
        await record_operational_event(
            event_type="voice.transcription.validation_failed",
            actor_id=principal.user_uuid,
            request_id=request_id,
            outcome="warning",
            metadata={
                "language": language,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error_code": "validation_error",
                "provider": "whisper",
            },
        )
        raise
    except Exception:
        await record_operational_event(
            event_type="voice.transcription.failed",
            actor_id=principal.user_uuid,
            request_id=request_id,
            outcome="error",
            metadata={
                "language": language,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error_code": "internal_error",
                "provider": "whisper",
            },
        )
        raise
    await record_operational_event(
        event_type="voice.transcription.completed",
        actor_id=principal.user_uuid,
        request_id=request_id,
        outcome="success",
        metadata={
            "language": result.language,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "audio_duration_seconds": result.duration_seconds,
            "model": result.model,
            "provider": "whisper",
        },
    )
    return TranscriptionOut(
        text=result.text,
        language=result.language,
        model=result.model,
        duration_seconds=result.duration_seconds,
    )
