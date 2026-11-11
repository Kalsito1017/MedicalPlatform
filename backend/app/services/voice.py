"""Self-hosted speech-to-text: bounded upload -> FFmpeg -> whisper.cpp.

Security properties enforced here (docs/SPEECH_RECOGNITION.md):

- the upload is streamed to a private temporary directory with a hard byte
  cap, never to a user-named path, and the directory is removed in `finally`;
- the media type is checked, then FFprobe/FFmpeg decide whether the bytes are
  usable audio; subprocesses use argument lists (never a shell) with timeouts;
- duration and the converted PCM size are bounded before the speech service is
  called, so a small-compressed but very long file cannot bypass the cap;
- transcripts and audio bytes are never logged, audited, or persisted;
- all dependency failures become controlled 503/429 application errors.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from fastapi import UploadFile

from app.config import Settings
from app.errors import DependencyUnavailable, RateLimited, ValidationFailed
from app.llm.limiter import SlidingWindowRateLimiter


class SpeechTimeout(DependencyUnavailable):
    """The speech service accepted the request but did not respond in time."""


logger = logging.getLogger(__name__)

# This endpoint is the only place allowed a larger-than-global request body.
# (The API router adds the /api/v1 prefix; the middleware needs the full path.)
VOICE_TRANSCRIPTION_PATH = "/api/v1/voice/transcriptions"
VOICE_MAX_REQUESTS_PER_MINUTE = 12

_ALLOWED_CONTENT_TYPE_PREFIXES = ("audio/",)
_ALLOWED_CONTENT_TYPES = frozenset(
    {"video/webm", "video/mp4", "application/ogg", "application/octet-stream"}
)
_READ_CHUNK_BYTES = 64 * 1024
_MIN_AUDIO_SECONDS = 0.15
_MAX_DURATION_TOLERANCE_SECONDS = 0.5
_WAV_SAMPLE_RATE = 16_000
_WAV_CHANNELS = 1
_WAV_SAMPLE_WIDTH = 2
# A 16 kHz mono 16-bit WAV is 32 kB/s; allow the header and encoder padding.
_MAX_CONVERTED_BYTES = _WAV_SAMPLE_RATE * _WAV_CHANNELS * _WAV_SAMPLE_WIDTH * 300 + 1_048_576

voice_rate_limiter = SlidingWindowRateLimiter(
    window_seconds=60, max_requests=VOICE_MAX_REQUESTS_PER_MINUTE
)


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: Literal["en", "bg"]
    model: str
    duration_seconds: float


class _ToolFailure(Exception):
    """Internal subprocess failure; details stay server-side."""


def _validate_content_type(content_type: str | None) -> None:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if not normalized or not (
        normalized.startswith(_ALLOWED_CONTENT_TYPE_PREFIXES)
        or normalized in _ALLOWED_CONTENT_TYPES
    ):
        raise ValidationFailed(
            "Unsupported audio format. Record audio in the browser or upload a WebM, OGG, MP4, or WAV file."
        )


async def _run_tool(binary: str, args: list[str], *, timeout: float) -> tuple[int, bytes]:
    """Run a fixed binary with an argument list (no shell) and a hard timeout."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("voice tool missing", extra={"tool": binary})
        raise DependencyUnavailable(
            "Audio processing is not available on this server."
        ) from None

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=max(1.0, timeout)
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        logger.warning("voice tool timed out", extra={"tool": binary})
        raise ValidationFailed("Audio processing took too long.") from None

    if process.returncode != 0:
        logger.warning(
            "voice tool failed",
            extra={"tool": binary, "returncode": process.returncode},
        )
    return process.returncode, stdout


async def _persist_upload(upload: UploadFile, target: Path, *, max_bytes: int) -> int:
    total = 0
    with target.open("wb") as handle:
        while True:
            chunk = await upload.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValidationFailed("The audio upload exceeds the configured limit.")
            handle.write(chunk)
    if total == 0:
        raise ValidationFailed("The uploaded audio file is empty.")
    target.chmod(0o600)
    return total


async def _probe_duration(path: Path, *, timeout: float) -> float:
    code, stdout = await _run_tool(
        "ffprobe",
        [
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=timeout,
    )
    if code != 0:
        raise ValidationFailed("The uploaded file could not be read as audio.")
    try:
        duration = float(stdout.decode("ascii", errors="strict").strip().splitlines()[0])
    except (ValueError, IndexError, UnicodeDecodeError):
        raise ValidationFailed("The uploaded file could not be read as audio.") from None
    if not math.isfinite(duration) or duration <= _MIN_AUDIO_SECONDS:
        raise ValidationFailed("The uploaded audio is too short or contains no audio track.")
    return duration


async def _convert_to_pcm_wav(source: Path, target: Path, *, timeout: float) -> float:
    code, _ = await _run_tool(
        "ffmpeg",
        [
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(_WAV_CHANNELS),
            "-ar",
            str(_WAV_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(target),
        ],
        timeout=timeout,
    )
    if code != 0 or not target.is_file():
        raise ValidationFailed("The uploaded audio could not be converted.")
    if target.stat().st_size > _MAX_CONVERTED_BYTES:
        raise ValidationFailed("The converted audio exceeds the size limit.")

    try:
        with wave.open(str(target), "rb") as converted:
            if (
                converted.getnchannels() != _WAV_CHANNELS
                or converted.getframerate() != _WAV_SAMPLE_RATE
                or converted.getsampwidth() != _WAV_SAMPLE_WIDTH
            ):
                raise ValidationFailed("Audio conversion produced an unexpected format.")
            duration = converted.getnframes() / float(_WAV_SAMPLE_RATE)
    except (wave.Error, EOFError):
        raise ValidationFailed("Audio conversion produced an unreadable file.") from None
    if not math.isfinite(duration) or duration <= _MIN_AUDIO_SECONDS:
        raise ValidationFailed("The uploaded audio is too short.")
    return duration


def _retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("retry-after")
    try:
        value = int(raw) if raw is not None else 15
    except ValueError:
        value = 15
    return max(1, min(value, 120))


_identity_token_cache: dict[str, tuple[str, float]] = {}


def _token_ttl_seconds(token: str) -> int:
    """Best-effort expiry read from an unverified JWT payload (cache only)."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        expires = float(payload["exp"])
        return max(30, min(int(expires - time.time() - 60), 900))
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 300


async def _google_identity_token(audience: str) -> str:
    """Fetch a Cloud Run ID token from the metadata server (gcp_iam mode).

    The token is cached in-process. Only the audience (configuration, never
    user input) is sent. Failure is fail-closed as `dependency_unavailable`.
    """
    now = time.monotonic()
    cached = _identity_token_cache.get(audience)
    if cached is not None and cached[1] > now:
        return cached[0]

    url = (
        "http://metadata.google.internal/computeMetadata/v1/"
        "instance/service-accounts/default/identity"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                url,
                params={"audience": audience, "format": "full"},
                headers={"Metadata-Flavor": "Google"},
            )
        response.raise_for_status()
        token = response.text.strip()
    except httpx.HTTPError:
        logger.error("speech identity token unavailable")
        raise DependencyUnavailable(
            "Speech service authentication is unavailable."
        ) from None
    if not token:
        raise DependencyUnavailable("Speech service authentication is unavailable.")
    _identity_token_cache[audience] = (token, now + _token_ttl_seconds(token))
    return token


async def _speech_headers(settings: Settings) -> dict[str, str]:
    if settings.whisper_auth_mode == "bearer":
        token = settings.whisper_bearer_token
        if token is None:  # validated at startup; fail closed anyway
            raise DependencyUnavailable("Speech service authentication is unavailable.")
        return {"Authorization": f"Bearer {token.get_secret_value()}"}
    if settings.whisper_auth_mode == "gcp_iam":
        if not settings.whisper_audience:
            raise DependencyUnavailable("Speech service authentication is unavailable.")
        token = await _google_identity_token(settings.whisper_audience)
        return {"Authorization": f"Bearer {token}"}
    return {}


async def _call_whisper(
    wav_path: Path,
    *,
    language: Literal["en", "bg"],
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    base_url = settings.whisper_base_url
    if not base_url:
        raise DependencyUnavailable(
            "Speech recognition is not configured on this server. Type the appointment details instead."
        )
    headers = await _speech_headers(settings)
    try:
        async with httpx.AsyncClient(
            timeout=max(1.0, float(settings.whisper_timeout_seconds)),
            transport=transport,
        ) as client:
            with wav_path.open("rb") as audio:
                response = await client.post(
                    f"{base_url}/inference",
                    headers=headers,
                    data={
                        "language": language,
                        "response_format": "json",
                        "temperature": "0",
                    },
                    files={"file": ("audio.wav", audio, "audio/wav")},
                )
    except httpx.TimeoutException:
        logger.warning("speech service timed out")
        raise SpeechTimeout(
            "Speech recognition did not respond in time. Please try again."
        ) from None
    except httpx.HTTPError:
        logger.warning("speech service unreachable")
        raise DependencyUnavailable(
            "Speech recognition is temporarily unavailable. Type the appointment details instead."
        ) from None

    if response.status_code == 429:
        raise RateLimited(
            "Speech recognition is at capacity. Please retry later.",
            retry_after=_retry_after(response),
        )
    if response.status_code >= 400:
        logger.warning(
            "speech service rejected the request",
            extra={"status_code": response.status_code},
        )
        raise DependencyUnavailable(
            "Speech recognition could not process this audio. Please try again or type the details."
        )
    try:
        payload = response.json()
    except ValueError:
        raise DependencyUnavailable(
            "Speech recognition returned an unusable response."
        ) from None
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise DependencyUnavailable("Speech recognition returned an unusable response.")
    return text.strip()


async def transcribe_upload(
    *,
    upload: UploadFile,
    language: Literal["en", "bg"],
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TranscriptionResult:
    """Convert one uploaded recording and return its transcript."""
    _validate_content_type(upload.content_type)
    max_bytes = min(settings.voice_max_upload_bytes, 8 * 1024 * 1024)

    with tempfile.TemporaryDirectory(prefix="voice-") as temp_dir:
        directory = Path(temp_dir)
        directory.chmod(0o700)
        source = directory / "input.bin"
        converted = directory / "audio.wav"
        await _persist_upload(upload, source, max_bytes=max_bytes)

        timeout = float(settings.voice_ffmpeg_timeout_seconds)
        probed_duration = await _probe_duration(source, timeout=timeout)
        if probed_duration > settings.voice_max_duration_seconds + _MAX_DURATION_TOLERANCE_SECONDS:
            raise ValidationFailed(
                f"Audio must be no longer than {settings.voice_max_duration_seconds} seconds."
            )

        duration = await _convert_to_pcm_wav(source, converted, timeout=timeout)
        if duration > settings.voice_max_duration_seconds + _MAX_DURATION_TOLERANCE_SECONDS:
            raise ValidationFailed(
                f"Audio must be no longer than {settings.voice_max_duration_seconds} seconds."
            )

        text = (
            await _call_whisper(
                converted, language=language, settings=settings, transport=transport
            )
        ).strip()

    return TranscriptionResult(
        text=text,
        language=language,
        model=settings.whisper_model_name,
        duration_seconds=round(duration, 2),
    )
