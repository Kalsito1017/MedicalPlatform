"""Configuration validation and logging redaction tests."""
from __future__ import annotations

import json
import logging

import pytest

from app.config import (
    ConfigurationError,
    JsonLogFormatter,
    Settings,
    load_settings,
    redact_fields,
    scrub,
)


def test_redact_fields_masks_sensitive_names() -> None:
    redacted = redact_fields(
        {
            "password": "hunter2",
            "api_key": "oc_sk_123",
            "Authorization": "Bearer abc",
            "cookie": "cap_session=abc",
            "session_token": "abc",
            "request_id": "req-1",
            "nested": {"secret": "s3cr3t", "action": "login"},
        }
    )
    assert redacted["password"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["cookie"] == "[REDACTED]"
    assert redacted["session_token"] == "[REDACTED]"
    assert redacted["nested"]["secret"] == "[REDACTED]"
    assert redacted["nested"]["action"] == "login"
    assert redacted["request_id"] == "req-1"


def test_scrub_masks_inline_dsn_credentials() -> None:
    scrubbed = scrub("connect failed postgresql+asyncpg://app:change-me@localhost:5432/db")
    assert "change-me" not in scrubbed
    assert "app" in scrubbed


def test_formatter_output_is_json_with_redaction() -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "msg", None, None)
    record.password = "hunter2"  # type: ignore[attr-defined]
    record.request_id = "req-2"  # type: ignore[attr-defined]
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["password"] == "[REDACTED]"
    assert payload["request_id"] == "req-2"
    assert payload["level"] == "INFO"
    assert "hunter2" not in json.dumps(payload)

SECRET_SENTINEL = "sentinel-secret-value-9f3c2b"


def test_missing_required_variables_names_only(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert "DATABASE_URL" in message
    assert SECRET_SENTINEL not in message
    assert "APP_SECRET_KEY" not in message  # set vars are not reported as missing
    assert "Missing variables" in message


def test_invalid_variable_reported_by_name_only(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://app:{SECRET_SENTINEL}@localhost:5432/db")
    monkeypatch.setenv("APP_ENV", "not-a-real-environment")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert "APP_ENV" in message
    assert "Invalid variables" in message
    assert SECRET_SENTINEL not in message


def test_secret_values_never_in_repr() -> None:
    settings = Settings(
        _env_file=None,
        app_secret_key=SECRET_SENTINEL,
        database_url=f"postgresql+asyncpg://app:{SECRET_SENTINEL}@localhost:5432/db",
    )
    assert SECRET_SENTINEL not in repr(settings)
    assert SECRET_SENTINEL not in str(settings.model_dump())
    assert settings.app_secret_key.get_secret_value() == SECRET_SENTINEL


def test_cors_origins_parsed_and_deduplicated(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:x@localhost:5432/db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173, http://localhost:5173,https://app.example.com")

    settings = load_settings(env_file=None)
    assert settings.cors_origins == ["http://localhost:5173", "https://app.example.com"]


def test_invalid_cors_origin_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:x@localhost:5432/db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "javascript:alert(1)")

    settings = load_settings(env_file=None)
    with pytest.raises(ConfigurationError) as excinfo:
        _ = settings.cors_origins
    assert "ALLOWED_ORIGINS" in str(excinfo.value)


def test_public_origin_must_be_an_origin(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:x@localhost:5432/db")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://app.example.com/a/path")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)
    assert "PUBLIC_ORIGIN" in str(excinfo.value)
    assert SECRET_SENTINEL not in str(excinfo.value)


def test_public_origin_trailing_slash_is_normalized(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:x@localhost:5432/db")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://app.example.com/")

    settings = load_settings(env_file=None)
    assert settings.public_origin == "https://app.example.com"


def test_docs_enabled_follows_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:x@localhost:5432/db")

    monkeypatch.setenv("APP_ENV", "production")
    production = load_settings(env_file=None)
    assert production.docs_enabled is False

    monkeypatch.setenv("APP_ENV", "development")
    development = load_settings(env_file=None)
    assert development.docs_enabled is True
