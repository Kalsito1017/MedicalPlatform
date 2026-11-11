"""Application configuration and structured logging.

Secrets are held in SecretStr fields so they are redacted from repr/log output.
Startup validation reports missing or invalid variables by NAME only; values are
never included in error messages.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Log record fields whose values must never appear in logs.
SENSITIVE_FIELD_MARKERS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "api_key",
    "authorization",
    "cookie",
    "session",
)
REDACTED = "[REDACTED]"

# Absolute ceiling for the route-specific voice upload allowance. The body-size
# middleware clamps the configured value to this constant.
VOICE_MAX_UPLOAD_HARD_LIMIT = 8 * 1024 * 1024

_DSN_CREDENTIALS_RE = re.compile(r"(://[^/\s:@]+):[^/\s@]+@")


class ConfigurationError(RuntimeError):
    """Raised when configuration is missing or invalid. Never contains secret values."""


class Settings(BaseSettings):
    """Environment-driven application settings.

    Required variables: APP_SECRET_KEY, DATABASE_URL. Everything else has a safe
    development default. See .env.example for the canonical variable list.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core application ---
    app_env: Literal["development", "test", "production"] = "development"
    app_secret_key: SecretStr
    public_origin: str = "http://localhost:8000"
    api_base_url: str = "http://localhost:8000"
    api_docs_enabled: bool | None = None  # None = enabled outside production
    log_level: str = "INFO"

    # --- Database ---
    database_url: SecretStr
    migration_database_url: SecretStr | None = None

    # --- Sessions (consumed by the Phase 5 auth agent) ---
    session_cookie_name: str = "cap_session"
    session_ttl_hours: int = 8
    # Session/CSRF cookie `Secure` attribute: None = automatic (Secure when
    # APP_ENV=production or the request arrives over HTTPS); True/False force it.
    session_cookie_secure: bool | None = None
    csrf_cookie_name: str = "csrf_token"

    # --- LLM (optional; the app runs fully without it) ---
    # None = auto: enabled when LLM_API_KEY is present. False = explicit kill
    # switch. True = require a key (still fails closed if it is missing).
    llm_enabled: bool | None = None
    # OpenCode Go endpoint (requires an active Go subscription). The standard
    # https://opencode.ai/inference endpoint remains documented for funded keys.
    llm_api_base_url: str = "https://opencode.ai/inference/go"
    llm_api_key: SecretStr | None = None
    llm_model: str = ""
    llm_max_tool_calls: int = 4
    llm_request_timeout_seconds: int = 45

    # --- Scheduling / clinic timezone ---
    clinic_timezone: str = "Europe/Sofia"

    # --- Voice transcription (optional self-hosted whisper.cpp) ---
    whisper_base_url: str | None = None
    whisper_timeout_seconds: int = 45
    whisper_model_name: str = "tiny"
    whisper_auth_mode: Literal["none", "bearer", "gcp_iam"] = "none"
    whisper_bearer_token: SecretStr | None = None
    whisper_audience: str | None = None
    # Hard cap for the single route that may exceed the global 1 MiB body limit.
    # The middleware clamps to this value, so a bad environment value cannot
    # open an unbounded upload path.
    voice_max_upload_bytes: int = 4 * 1024 * 1024
    voice_max_duration_seconds: int = 60
    voice_ffmpeg_timeout_seconds: int = 30

    @field_validator("clinic_timezone")
    @classmethod
    def _validate_clinic_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("CLINIC_TIMEZONE must be a valid IANA timezone name.") from exc
        return value

    @field_validator("voice_max_upload_bytes")
    @classmethod
    def _validate_voice_upload_size(cls, value: int) -> int:
        if not 64 * 1024 <= value <= VOICE_MAX_UPLOAD_HARD_LIMIT:
            raise ValueError(
                "VOICE_MAX_UPLOAD_BYTES must be between 65536 and "
                f"{VOICE_MAX_UPLOAD_HARD_LIMIT} bytes."
            )
        return value

    @field_validator("voice_max_duration_seconds")
    @classmethod
    def _validate_voice_duration(cls, value: int) -> int:
        if not 1 <= value <= 300:
            raise ValueError("VOICE_MAX_DURATION_SECONDS must be between 1 and 300.")
        return value

    @field_validator("whisper_timeout_seconds", "voice_ffmpeg_timeout_seconds")
    @classmethod
    def _validate_voice_timeouts(cls, value: int) -> int:
        if not 1 <= value <= 120:
            raise ValueError("Voice timeouts must be between 1 and 120 seconds.")
        return value

    @field_validator("whisper_base_url")
    @classmethod
    def _validate_whisper_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parsed = urlparse(value.strip())
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "WHISPER_BASE_URL must be an http(s) origin without credentials, path, or query."
            )
        return value.strip().rstrip("/")

    @model_validator(mode="after")
    def _validate_whisper_auth(self) -> "Settings":
        if self.whisper_auth_mode == "bearer" and (
            self.whisper_bearer_token is None
            or not self.whisper_bearer_token.get_secret_value().strip()
        ):
            raise ValueError(
                "WHISPER_BEARER_TOKEN is required when WHISPER_AUTH_MODE=bearer."
            )
        if self.whisper_auth_mode == "gcp_iam" and not (self.whisper_audience or "").strip():
            raise ValueError(
                "WHISPER_AUDIENCE is required when WHISPER_AUTH_MODE=gcp_iam."
            )
        return self

    # --- Demo bootstrap (seed time only; never real credentials) ---
    demo_admin_email: str | None = None
    demo_admin_password: SecretStr | None = None
    demo_doctor_email: str | None = None
    demo_doctor_password: SecretStr | None = None
    demo_nurse_email: str | None = None
    demo_nurse_password: SecretStr | None = None

    # --- CORS / transport limits ---
    allowed_origins: str = "http://localhost:5173,http://localhost:8000"
    request_max_body_bytes: int = 1_048_576

    # --- Static frontend (single-origin local dev and the Docker image) ---
    # None = auto-detect <repo>/frontend next to backend/. In production the
    # frontend is served by Cloudflare Pages and this stays unused.
    frontend_dir: str | None = None

    @property
    def resolved_frontend_dir(self) -> Path | None:
        """Absolute frontend directory to serve, or None when unavailable.

        An explicit FRONTEND_DIR must exist (a wrong value disables static
        serving rather than guessing). The default is the repository layout:
        <repo>/frontend beside <repo>/backend. API-only deployments simply have
        no frontend directory and keep serving /api/v1 and /health/* normally.
        """
        if self.frontend_dir:
            candidate = Path(self.frontend_dir).expanduser()
            return candidate.resolve() if candidate.is_dir() else None
        default = Path(__file__).resolve().parents[2] / "frontend"
        return default.resolve() if default.is_dir() else None

    @property
    def cors_origins(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into validated, de-duplicated origins."""
        raw = [part.strip() for part in self.allowed_origins.split(",")]
        origins: list[str] = []
        for origin in raw:
            if not origin:
                continue
            parsed = urlparse(origin)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/"):
                raise ConfigurationError(
                    "Invalid or missing configuration. Invalid variables: ALLOWED_ORIGINS "
                    "(entries must be http(s) origins, e.g. https://app.example.com)."
                )
            if origin not in origins:
                origins.append(origin)
        return origins

    @property
    def docs_enabled(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return self.app_env != "production"

    @property
    def effective_migration_url(self) -> SecretStr:
        """Migrations may use elevated credentials; prefer MIGRATION_DATABASE_URL."""
        return self.migration_database_url or self.database_url


def load_settings(*, env_file: str | None = ".env") -> Settings:
    """Build Settings, converting validation failures into a names-only error.

    Pass env_file=None to read the environment only (tests).
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        missing: list[str] = []
        invalid: list[str] = []
        for error in exc.errors():
            name = str(error.get("loc", ("unknown",))[-1]).upper()
            if error.get("type") == "missing":
                missing.append(name)
            else:
                invalid.append(name)
        parts: list[str] = ["Invalid or missing configuration."]
        if missing:
            parts.append(f"Missing variables: {', '.join(sorted(set(missing)))}.")
        if invalid:
            parts.append(f"Invalid variables: {', '.join(sorted(set(invalid)))}.")
        parts.append("See .env.example for the expected variable names (values are never printed).")
        # `from None`: the chained ValidationError may contain submitted values.
        raise ConfigurationError(" ".join(parts)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached application settings. Tests can call get_settings.cache_clear()."""
    return load_settings()


# ---------------------------------------------------------------------------
# Structured logging with secret redaction
# ---------------------------------------------------------------------------


def scrub(text: str) -> str:
    """Mask inline credentials (e.g. postgres://user:password@host) in free text."""
    return _DSN_CREDENTIALS_RE.sub(r"\1:" + REDACTED + "@", text)


def redact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Redact values of sensitive-named fields (recursively for nested dicts)."""
    redacted: dict[str, Any] = {}
    for key, value in fields.items():
        lowered = key.lower()
        if any(marker in lowered for marker in SENSITIVE_FIELD_MARKERS):
            redacted[key] = REDACTED
        elif isinstance(value, dict):
            redacted[key] = redact_fields(value)
        else:
            redacted[key] = value
    return redacted


_LOG_RECORD_STANDARD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line; extra fields included; sensitive fields redacted."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _LOG_RECORD_STANDARD_FIELDS:
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        payload = redact_fields(payload)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Attach the structured handler to the root logger (idempotent).

    Existing handlers (e.g. pytest's capture handler) are left untouched.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    if any(getattr(handler, "_clinical_platform_handler", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler._clinical_platform_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
