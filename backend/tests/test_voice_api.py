"""Voice transcription API, FFmpeg conversion, and whisper.cpp client tests.

Real FFmpeg/FFprobe process the generated WAV; the network boundary is mocked
with `httpx.MockTransport` (or a monkeypatched transcriber) so no speech server
or model is required. Audio bytes and transcripts are never asserted in logs.
"""
from __future__ import annotations

import io
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import httpx
import pytest
from starlette.datastructures import Headers, UploadFile

from app.config import load_settings
from app.errors import DependencyUnavailable, RateLimited, ValidationFailed
from app.services import voice as voice_service
from app.services.voice import transcribe_upload, voice_rate_limiter
from conftest import csrf_headers

pytestmark = pytest.mark.postgres

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/FFprobe are required for voice conversion tests.",
)


@pytest.fixture(autouse=True)
def _reset_voice_limiter():
    voice_rate_limiter.reset()
    yield
    voice_rate_limiter.reset()


def _wav_bytes(*, duration: float = 1.0, sample_rate: int = 16_000) -> bytes:
    frames = b"".join(
        struct.pack("<h", int(12_000 * math.sin(2 * math.pi * 440 * index / sample_rate)))
        for index in range(int(sample_rate * duration))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(sample_rate)
        recording.writeframes(frames)
    return buffer.getvalue()


def _upload(data: bytes, content_type: str = "audio/wav") -> UploadFile:
    return UploadFile(
        io.BytesIO(data),
        filename="recording.wav",
        headers=Headers({"content-type": content_type}),
    )


@requires_ffmpeg
async def test_transcription_returns_editable_text_and_cleans_up(
    make_user, auth_client, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    observed: dict[str, object] = {}
    created_dirs: list[str] = []
    real_temporary_directory = voice_service.tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        directory = real_temporary_directory(*args, **kwargs)
        created_dirs.append(directory.name)
        return directory

    async def fake_transcriber(wav_path, *, language, settings, transport=None):
        assert language == "auto"
        with wave.open(str(wav_path), "rb") as converted:
            assert converted.getnchannels() == 1
            assert converted.getframerate() == 16_000
            assert converted.getsampwidth() == 2
        observed["path"] = wav_path
        return voice_service.WhisperResult(
            text="  Утре в десет часа  ", language="bg"
        )

    monkeypatch.setattr(voice_service, "_call_whisper", fake_transcriber)
    monkeypatch.setattr(
        voice_service.tempfile, "TemporaryDirectory", tracked_temporary_directory
    )

    response = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "auto"},
        files={"file": ("recording.wav", _wav_bytes(), "audio/wav")},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["text"] == "Утре в десет часа"
    assert payload["language"] == "bg"
    assert payload["model"] == "base"
    assert 0.9 <= payload["duration_seconds"] <= 1.1
    assert observed
    assert created_dirs and not Path(created_dirs[0]).exists()



def _webm_without_duration(duration: float = 2.0) -> bytes:
    """Create a streamed WebM like MediaRecorder (no container duration tag)."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            "pipe:1",
        ],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg lacks libopus/WebM support on this host")
    assert result.stdout
    return result.stdout


@requires_ffmpeg
async def test_transcription_accepts_webm_without_duration_metadata(
    make_user, auth_client, app, monkeypatch
) -> None:
    """MediaRecorder WebM commonly reports N/A for duration; it is still valid."""
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    payload = _webm_without_duration(2.0)

    async def fake_transcriber(wav_path, *, language, settings, transport=None):
        with wave.open(str(wav_path), "rb") as converted:
            assert converted.getframerate() == 16_000
        return voice_service.WhisperResult(text="Book a follow-up visit", language="en")

    monkeypatch.setattr(voice_service, "_call_whisper", fake_transcriber)

    response = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("appointment-recording.webm", payload, "audio/webm")},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["text"] == "Book a follow-up visit"
    assert 1.8 <= response.json()["duration_seconds"] <= 2.3

    # If no container/stream/packet metadata is usable, the bounded conversion
    # must still determine the duration from decoded frames.
    async def unknown_duration(_path, *, timeout):
        return None

    monkeypatch.setattr(voice_service, "_probe_duration", unknown_duration)
    fallback = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("appointment-recording.webm", payload, "audio/webm")},
        headers=csrf_headers(client),
    )
    assert fallback.status_code == 200, fallback.text
    assert 1.8 <= fallback.json()["duration_seconds"] <= 2.3

    # A no-metadata file longer than the configured limit is rejected, not
    # silently truncated to an apparently valid length.
    app.state.settings.voice_max_duration_seconds = 1
    too_long = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("appointment-recording.webm", payload, "audio/webm")},
        headers=csrf_headers(client),
    )
    assert too_long.status_code == 400
    assert "no longer than" in too_long.json()["error"]["message"]


@requires_ffmpeg
async def test_transcription_rejects_unsupported_empty_and_long_audio(
    make_user, auth_client, app
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)

    unsupported = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=csrf_headers(client),
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["code"] == "validation_error"

    empty = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("empty.webm", b"", "audio/webm")},
        headers=csrf_headers(client),
    )
    assert empty.status_code == 400

    # A syntactically valid, bounded WAV longer than the configured duration.
    app.state.settings.voice_max_duration_seconds = 1
    long_audio = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("long.wav", _wav_bytes(duration=2.0), "audio/wav")},
        headers=csrf_headers(client),
    )
    assert long_audio.status_code == 400
    assert "no longer than" in long_audio.json()["error"]["message"]


@requires_ffmpeg
async def test_transcription_fails_closed_when_speech_is_not_configured(
    make_user, auth_client, app
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    app.state.settings.whisper_base_url = None

    response = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("recording.wav", _wav_bytes(), "audio/wav")},
        headers=csrf_headers(client),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


@requires_ffmpeg
async def test_speech_client_posts_pcm_wav_and_parses_json() -> None:
    settings = load_settings(env_file=None)
    settings.whisper_base_url = "http://speech.test"
    settings.whisper_model_name = "base"
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen["url"] = str(request.url)
        seen["body"] = body
        return httpx.Response(200, json={"text": " Book a follow-up "})

    result = await transcribe_upload(
        upload=_upload(_wav_bytes(duration=1.0)),
        language="bg",
        settings=settings,
        transport=httpx.MockTransport(handler),
    )
    assert result.text == "Book a follow-up"
    assert result.language == "bg"
    assert result.model == "base"
    assert seen["url"] == "http://speech.test/inference"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'name="language"' in body and b"bg" in body
    assert b"RIFF" in body  # the converted 16 kHz mono WAV is what is sent


@requires_ffmpeg
async def test_speech_client_auto_detects_language() -> None:
    settings = load_settings(env_file=None)
    settings.whisper_base_url = "http://speech.test"
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "text": " Book a follow-up ",
                "language": "english",
                "detected_language": "english",
                "language_probabilities": {"en": 0.98, "bg": 0.02},
            },
        )

    result = await transcribe_upload(
        upload=_upload(_wav_bytes(duration=1.0)),
        language="auto",
        settings=settings,
        transport=httpx.MockTransport(handler),
    )
    assert result.text == "Book a follow-up"
    assert result.language == "en"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'name="language"' in body and b"auto" in body
    assert b"verbose_json" in body

    def bulgarian(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": " Утре в десет часа ",
                "language_probabilities": {"bg": 0.99, "en": 0.01},
            },
        )

    bulgarian_result = await transcribe_upload(
        upload=_upload(_wav_bytes(duration=1.0)),
        language="auto",
        settings=settings,
        transport=httpx.MockTransport(bulgarian),
    )
    assert bulgarian_result.language == "bg"

    # A manual override must win over the model's detection.
    fixed = await transcribe_upload(
        upload=_upload(_wav_bytes(duration=1.0)),
        language="bg",
        settings=settings,
        transport=httpx.MockTransport(handler),
    )
    assert fixed.language == "bg"


def test_detected_language_handles_other_codes_and_fallbacks() -> None:
    assert voice_service._detected_language(
        {"language_probabilities": {"de": 0.99}}, requested="auto", text="Hallo"
    ) == "de"
    assert voice_service._detected_language(
        {}, requested="auto", text="Здравей"
    ) == "bg"
    assert voice_service._detected_language({}, requested="auto", text="Hello") == "en"
    assert voice_service._detected_language(
        {}, requested="en", text="Здравей"
    ) == "en"


@requires_ffmpeg
async def test_speech_client_rejects_unsupported_detected_language() -> None:
    settings = load_settings(env_file=None)
    settings.whisper_base_url = "http://speech.test"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": " Guten Tag ",
                "language_probabilities": {"de": 0.99},
            },
        )

    with pytest.raises(ValidationFailed) as rejected:
        await transcribe_upload(
            upload=_upload(_wav_bytes(duration=1.0)),
            language="auto",
            settings=settings,
            transport=httpx.MockTransport(handler),
        )
    assert "Bulgarian and English only" in rejected.value.message


@requires_ffmpeg
async def test_transcription_rejects_unsupported_detected_language(
    make_user, auth_client, monkeypatch
) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)

    async def fake_transcriber(wav_path, *, language, settings, transport=None):
        return voice_service.WhisperResult(text="Guten Tag", language="de")

    monkeypatch.setattr(voice_service, "_call_whisper", fake_transcriber)

    response = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "auto"},
        files={"file": ("recording.wav", _wav_bytes(), "audio/wav")},
        headers=csrf_headers(client),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert "Bulgarian and English only" in response.json()["error"]["message"]


@requires_ffmpeg
async def test_speech_client_maps_dependency_failures() -> None:
    settings = load_settings(env_file=None)
    settings.whisper_base_url = "http://speech.test"

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "7"})

    with pytest.raises(RateLimited) as limited:
        await transcribe_upload(
            upload=_upload(_wav_bytes()),
            language="en",
            settings=settings,
            transport=httpx.MockTransport(rate_limited),
        )
    assert limited.value.retry_after == 7

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(DependencyUnavailable):
        await transcribe_upload(
            upload=_upload(_wav_bytes()),
            language="en",
            settings=settings,
            transport=httpx.MockTransport(unreachable),
        )

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(DependencyUnavailable):
        await transcribe_upload(
            upload=_upload(_wav_bytes()),
            language="en",
            settings=settings,
            transport=httpx.MockTransport(malformed),
        )


async def test_voice_upload_has_a_strict_route_specific_body_limit(make_client) -> None:
    client = await make_client()
    client.cookies.set("csrf_token", "test-csrf-token-value")
    headers = {"X-CSRF-Token": "test-csrf-token-value"}

    # 2 MiB is below the voice allowance: the request reaches auth (401), not
    # the body-size rejection.
    allowed = await client.post(
        "/api/v1/voice/transcriptions",
        content=b"x" * (2 * 1024 * 1024),
        headers=headers,
    )
    assert allowed.status_code == 401, allowed.text

    # 5 MiB is above the configured 4 MiB voice allowance.
    oversized_voice = await client.post(
        "/api/v1/voice/transcriptions",
        content=b"x" * (5 * 1024 * 1024),
        headers=headers,
    )
    assert oversized_voice.status_code == 400
    assert "8388608" not in oversized_voice.json()["error"]["message"]
    assert "4194304" in oversized_voice.json()["error"]["message"]

    # Every other route stays at the global 1 MiB limit.
    oversized_global = await client.post(
        "/api/v1/ai/ask",
        content=b"x" * (2 * 1024 * 1024),
        headers=headers,
    )
    assert oversized_global.status_code == 400
    assert "1048576" in oversized_global.json()["error"]["message"]


async def test_voice_transcription_is_rate_limited(make_user, auth_client) -> None:
    doctor = await make_user(roles=("doctor",))
    client = await auth_client(doctor)
    for _ in range(voice_service.VOICE_MAX_REQUESTS_PER_MINUTE):
        response = await client.post(
            "/api/v1/voice/transcriptions",
            data={"language": "en"},
            files={"file": ("empty.webm", b"", "audio/webm")},
            headers=csrf_headers(client),
        )
        assert response.status_code == 400
    limited = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "en"},
        files={"file": ("empty.webm", b"", "audio/webm")},
        headers=csrf_headers(client),
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers.get("retry-after")
