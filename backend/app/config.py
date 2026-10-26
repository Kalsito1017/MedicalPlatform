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
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import SecretStr, ValidationError
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
    llm_enabled: bool = False
    llm_api_base_url: str = "https://opencode.ai/inference"
    llm_api_key: SecretStr | None = None
    llm_model: str = ""
    llm_max_tool_calls: int = 4
    llm_request_timeout_seconds: int = 45

    # --- Demo bootstrap (seed time only; never real credentials) ---
    demo_admin_email: str | None = None
    demo_admin_password: SecretStr | None = None
    demo_doctor_email: str | None = None
    demo_doctor_password: SecretStr | None = None
    demo_nurse_email: str | None = None
    demo_nurse_password: SecretStr | None = None
    demo_tech_admin_email: str | None = None
    demo_tech_admin_password: SecretStr | None = None

    # --- CORS / transport limits ---
    allowed_origins: str = "http://localhost:5173,http://localhost:8000"
    request_max_body_bytes: int = 1_048_576

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
