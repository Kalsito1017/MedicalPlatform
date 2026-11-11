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
import wave
from pathlib import Path

import httpx
import pytest
from starlette.datastructures import Headers, UploadFile

from app.config import load_settings
from app.errors import DependencyUnavailable, RateLimited
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
        assert language == "bg"
        with wave.open(str(wav_path), "rb") as converted:
            assert converted.getnchannels() == 1
            assert converted.getframerate() == 16_000
            assert converted.getsampwidth() == 2
        observed["path"] = wav_path
        return "  Утре в десет часа  "

    monkeypatch.setattr(voice_service, "_call_whisper", fake_transcriber)
    monkeypatch.setattr(
        voice_service.tempfile, "TemporaryDirectory", tracked_temporary_directory
    )

    response = await client.post(
        "/api/v1/voice/transcriptions",
        data={"language": "bg"},
        files={"file": ("recording.wav", _wav_bytes(), "audio/wav")},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["text"] == "Утре в десет часа"
    assert payload["language"] == "bg"
    assert payload["model"] == "tiny"
    assert 0.9 <= payload["duration_seconds"] <= 1.1
    assert observed
    assert created_dirs and not Path(created_dirs[0]).exists()


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
    settings.whisper_model_name = "tiny"
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
    assert result.model == "tiny"
    assert seen["url"] == "http://speech.test/inference"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'name="language"' in body and b"bg" in body
    assert b"RIFF" in body  # the converted 16 kHz mono WAV is what is sent


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
