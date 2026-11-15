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
import re
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
class WhisperResult:
    """The transcribed text plus the language whisper detected or was given."""

    text: str
    language: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: Literal["en", "bg"]
    model: str
    duration_seconds: float


_WHISPER_LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "bg": "bg",
    "bul": "bg",
    "bulgarian": "bg",
}
_LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,8}$")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def _detected_language(payload: dict[str, Any], *, requested: str, text: str) -> str:
    """Return the language whisper detected, or the doctor's manual choice.

    `verbose_json` exposes `language_probabilities` and `detected_language`.
    A fixed request language always wins (the doctor explicitly overrode
    detection); `auto` trusts the model and falls back to the transcript's
    script only when the response carries no usable language field.
    """
    if requested in {"en", "bg"}:
        return requested

    probabilities = payload.get("language_probabilities")
    if isinstance(probabilities, dict) and probabilities:
        candidates = [
            (str(code).strip().lower(), value)
            for code, value in probabilities.items()
            if isinstance(code, str) and isinstance(value, (int, float))
        ]
        if candidates:
            code = max(candidates, key=lambda item: item[1])[0]
            if _LANGUAGE_CODE_RE.fullmatch(code):
                return code

    for key in ("detected_language", "language"):
        raw = payload.get(key)
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            alias = _WHISPER_LANGUAGE_ALIASES.get(normalized)
            if alias:
                return alias
            if _LANGUAGE_CODE_RE.fullmatch(normalized):
                return normalized

    return "bg" if _CYRILLIC_RE.search(text) else "en"


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


def _parse_duration(stdout: bytes) -> float | None:
    """Parse an ffprobe duration value; `N/A` and non-positive values are unknown."""
    try:
        text = stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        return None
    if not text or text.upper() in {"N/A", "NAN"}:
        return None
    first = text.splitlines()[0].strip()
    try:
        duration = float(first)
    except ValueError:
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


def _packet_end_seconds(stdout: bytes) -> float | None:
    """Compute the last packet end time for containers without a duration tag."""
    try:
        text = stdout.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None
    end = 0.0
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            pts = float(parts[0])
            duration = float(parts[1]) if parts[1].strip() else 0.0
        except ValueError:
            continue
        if math.isfinite(pts) and math.isfinite(duration) and duration >= 0:
            end = max(end, pts + duration)
    return end if end > 0 else None


async def _probe_duration(path: Path, *, timeout: float) -> float | None:
    """Best-effort duration before conversion.

    Browser `MediaRecorder` WebM output is streamed and frequently omits the
    container/stream duration (`N/A`). That is still valid audio: fall back to
    the last packet timestamp. If no metadata is available, return None and let
    the bounded conversion below determine the duration from decoded frames.
    """
    for entries, extra in (
        ("format=duration", []),
        ("stream=duration", ["-select_streams", "a:0"]),
    ):
        code, stdout = await _run_tool(
            "ffprobe",
            [
                "-v",
                "error",
                *extra,
                "-show_entries",
                entries,
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout=timeout,
        )
        if code != 0:
            raise ValidationFailed("The uploaded file could not be read as audio.")
        duration = _parse_duration(stdout)
        if duration is not None:
            return duration

    code, stdout = await _run_tool(
        "ffprobe",
        [
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=pts_time,duration_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        timeout=timeout,
    )
    if code != 0:
        raise ValidationFailed("The uploaded file could not be read as audio.")
    return _packet_end_seconds(stdout)


async def _convert_to_pcm_wav(
    source: Path,
    target: Path,
    *,
    timeout: float,
    max_duration: float | None = None,
) -> float:
    args = [
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
    ]
    if max_duration is not None:
        # Bound decoding even when the container does not carry a duration.
        # The caller deliberately sets this slightly above the accepted limit
        # so a too-long file is truncated to a detectable over-limit duration.
        args.extend(["-t", f"{max_duration:.3f}"])
    args.extend(
        [
            "-ac",
            str(_WAV_CHANNELS),
            "-ar",
            str(_WAV_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(target),
        ]
    )
    code, _ = await _run_tool("ffmpeg", args, timeout=timeout)
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
    language: Literal["en", "bg", "auto"],
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WhisperResult:
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
                        # verbose_json carries the detected language and its
                        # probabilities; plain json returns text only.
                        "response_format": "verbose_json",
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
    if not isinstance(payload, dict):
        raise DependencyUnavailable("Speech recognition returned an unusable response.")
    text = payload.get("text")
    if not isinstance(text, str):
        raise DependencyUnavailable("Speech recognition returned an unusable response.")
    cleaned = text.strip()
    return WhisperResult(
        text=cleaned,
        language=_detected_language(payload, requested=language, text=cleaned),
    )


async def transcribe_upload(
    *,
    upload: UploadFile,
    language: Literal["en", "bg", "auto"],
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
        allowed_duration = (
            settings.voice_max_duration_seconds + _MAX_DURATION_TOLERANCE_SECONDS
        )
        probed_duration = await _probe_duration(source, timeout=timeout)
        if probed_duration is not None and probed_duration > allowed_duration:
            raise ValidationFailed(
                f"Audio must be no longer than {settings.voice_max_duration_seconds} seconds."
            )

        duration = await _convert_to_pcm_wav(
            source,
            converted,
            timeout=timeout,
            # Leave a margin above the accepted limit so a no-metadata source
            # longer than the limit decodes to a duration we can reject rather
            # than being silently truncated to an apparently valid length.
            max_duration=allowed_duration + 1.0,
        )
        if duration > allowed_duration:
            raise ValidationFailed(
                f"Audio must be no longer than {settings.voice_max_duration_seconds} seconds."
            )

        whisper_result = await _call_whisper(
            converted, language=language, settings=settings, transport=transport
        )
        if whisper_result.language not in {"en", "bg"}:
            raise ValidationFailed(
                "Speech recognition supports Bulgarian and English only. "
                "Please speak in Bulgarian or English, or choose a language manually."
            )

    return TranscriptionResult(
        text=whisper_result.text.strip(),
        language=whisper_result.language,
        model=settings.whisper_model_name,
        duration_seconds=round(duration, 2),
    )
