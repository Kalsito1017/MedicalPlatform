#!/usr/bin/env python3
"""Check the complete real-audio speech-to-text path.

This uploads an actual recording through the authenticated appointment voice
endpoint, so it exercises the same path as the browser: login -> CSRF ->
multipart upload -> FFprobe/FFmpeg -> private whisper.cpp -> editable transcript.

Use synthetic/demo audio only. This repository is not cleared for real patient
data, and the script never saves the audio or transcript.

Typical use (from the repository root):

    cd backend
    uv run python ../scripts/check_voice_transcription.py \
        --audio /tmp/voice-sample.wav --language auto \
        --expect "Елена"

`--language auto` is the default: whisper.cpp detects Bulgarian or English
from the recording. Use `--language bg` or `--language en` to force one.
Only Bulgarian and English transcripts are accepted; any other detected
language is rejected by the endpoint.

Optional AI proposal check (requires a configured LLM; 503 is reported as a
soft warning because transcription itself already succeeded):

    uv run python ../scripts/check_voice_transcription.py \
        --audio /tmp/voice-sample.wav --language en --interpret

Recording examples:
    Linux (PulseAudio/PipeWire):
        ffmpeg -f pulse -i default -t 20 -ac 1 -ar 16000 /tmp/voice-sample.wav
    macOS:
        ffmpeg -f avfoundation -i ":0" -t 20 -ac 1 -ar 16000 /tmp/voice-sample.wav
    Windows: record with Voice Recorder and pass the resulting .m4a path.

The demo Doctor credentials are read from backend/.env by default. Override the
email or password environment-variable name if needed; the password is never
printed or placed on the command line.
"""
from __future__ import annotations

import argparse
import getpass
import mimetypes
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / "backend" / ".env"
DEFAULT_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
DEFAULT_EMAIL_VAR = "DEMO_DOCTOR_EMAIL"
DEFAULT_PASSWORD_VAR = "DEMO_DOCTOR_PASSWORD"

_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def resolve(name: str, env_file: dict[str, str], *, prompt: str | None = None) -> str | None:
    if name in os.environ and os.environ[name]:
        return os.environ[name]
    if env_file.get(name):
        return env_file[name]
    if prompt and sys.stdin.isatty():
        return getpass.getpass(prompt)
    return None


def audio_mime_type(path: Path) -> str:
    explicit = _AUDIO_TYPES.get(path.suffix.lower())
    if explicit:
        return explicit
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed:
        return guessed
    return "application/octet-stream"


def error_summary(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code") or "error"
            message = error.get("message") or ""
            return f"HTTP {response.status_code} {code}: {message}".strip()
    return f"HTTP {response.status_code}: {response.text[:300]}"


def normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def print_proposal(payload: dict) -> None:
    patient = payload.get("patient") or {}
    candidates = payload.get("patient_candidates") or []
    print("AI proposal (not saved):")
    print(f"  patient:  {patient.get('display_name') or patient.get('mrn') or (f'{len(candidates)} candidate(s)' if candidates else 'not resolved')}")
    print(f"  date:     {payload.get('date') or 'not stated'}")
    print(f"  time:     {payload.get('start_time') or 'not stated'}")
    print(f"  duration: {payload.get('duration_minutes')} minutes")
    print(f"  type:     {payload.get('appointment_type') or 'not stated'}")
    print(f"  notes:    {payload.get('notes') or 'none'}")
    print(f"  can_confirm: {payload.get('can_confirm')}")
    for warning in payload.get("warnings") or []:
        print(f"  warning:  {warning}")
    for missing in payload.get("missing_fields") or []:
        print(f"  missing:  {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audio", required=True, help="Path to a real WAV/WebM/OGG/MP3/M4A/MP4 recording.")
    parser.add_argument("--language", choices=("auto", "bg", "en"), default="auto")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--email", help=f"Login email (default: {DEFAULT_EMAIL_VAR} from the env file).")
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_VAR)
    parser.add_argument("--expect", help="Optional case-insensitive phrase expected in the transcript.")
    parser.add_argument("--interpret", action="store_true", help="Also call the AI proposal endpoint with the transcript.")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    audio = Path(args.audio).expanduser()
    if not audio.is_file():
        print(f"Audio file not found: {audio}", file=sys.stderr)
        return 2
    if audio.stat().st_size == 0:
        print(f"Audio file is empty: {audio}", file=sys.stderr)
        return 2

    env_file = read_env_file(Path(args.env_file).expanduser())
    email = args.email or resolve(DEFAULT_EMAIL_VAR, env_file)
    if not email:
        print(
            f"Missing login email. Set --email or {DEFAULT_EMAIL_VAR} in {args.env_file}.",
            file=sys.stderr,
        )
        return 2
    password = resolve(
        args.password_env,
        env_file,
        prompt=f"Password for {email}: ",
    )
    if not password:
        print(
            f"Missing password. Export {args.password_env} or set it in {args.env_file}.",
            file=sys.stderr,
        )
        return 2

    print(f"Checking speech-to-text against {args.base_url}")
    print(f"Audio: {audio} ({audio.stat().st_size / 1024:.1f} KB), language={args.language}")
    print("Privacy: use synthetic/demo audio only; audio and transcript are not retained.")

    with httpx.Client(base_url=args.base_url, timeout=args.timeout, follow_redirects=False) as client:
        try:
            health = client.get("/health/ready")
        except httpx.HTTPError as exc:
            print(f"Backend unreachable: {type(exc).__name__}", file=sys.stderr)
            return 2
        if health.status_code != 200:
            print(f"Backend is not ready: {error_summary(health)}", file=sys.stderr)
            return 2
        print("Backend readiness: 200")

        # Bootstrap the CSRF cookie exactly like the browser does.
        client.get("/api/v1/auth/me")
        csrf = client.cookies.get("csrf_token")
        if not csrf:
            print("No CSRF cookie was issued by /auth/me.", file=sys.stderr)
            return 2
        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        if login.status_code != 200:
            print(f"Login failed: {error_summary(login)}", file=sys.stderr)
            return 2
        csrf = client.cookies.get("csrf_token") or csrf
        print(f"Login: 200 ({email})")

        mime_type = audio_mime_type(audio)
        try:
            with audio.open("rb") as handle:
                response = client.post(
                    "/api/v1/voice/transcriptions",
                    data={"language": args.language},
                    files={"file": (audio.name, handle, mime_type)},
                    headers={"X-CSRF-Token": csrf},
                )
        except httpx.HTTPError as exc:
            print(f"Transcription request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        if response.status_code != 200:
            print(f"Transcription failed: {error_summary(response)}", file=sys.stderr)
            if response.status_code == 503:
                print(
                    "The private speech service or WHISPER_BASE_URL is unavailable. "
                    "Try: docker compose --profile speech up -d",
                    file=sys.stderr,
                )
            return 1

        payload = response.json()
        transcript = str(payload.get("text") or "")
        print(
            f"Transcription: 200 (model={payload.get('model')}, "
            f"language={payload.get('language')}, "
            f"duration={payload.get('duration_seconds')}s)"
        )
        print("Transcript:")
        print(transcript or "(empty — no speech was detected)")

        if not transcript.strip():
            print("No speech was detected. Record a longer/cleaner sample.", file=sys.stderr)
            return 1

        if args.expect:
            if normalized(args.expect) not in normalized(transcript):
                print(
                    f"Expected phrase not found: {args.expect!r}",
                    file=sys.stderr,
                )
                return 1
            print(f"Expected phrase found: {args.expect!r}")

        if args.interpret:
            try:
                proposal = client.post(
                    "/api/v1/appointments/interpret",
                    json={"text": transcript},
                    headers={"X-CSRF-Token": csrf},
                )
            except httpx.HTTPError as exc:
                print(f"Interpret request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 2
            if proposal.status_code == 200:
                print()
                print_proposal(proposal.json())
            elif proposal.status_code == 503:
                print()
                print(
                    "AI proposal is unavailable (503). Transcription itself succeeded; "
                    "configure LLM_API_KEY or use manual scheduling.",
                    file=sys.stderr,
                )
            else:
                print(f"AI proposal failed: {error_summary(proposal)}", file=sys.stderr)
                return 2

    print()
    print("RESULT: speech-to-text path succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
