"""Configuration validation tests: clear, non-secret errors."""
from __future__ import annotations

import pytest

from app.config import ConfigurationError, Settings, load_settings

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


def test_docs_enabled_follows_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:x@localhost:5432/db")

    monkeypatch.setenv("APP_ENV", "production")
    production = load_settings(env_file=None)
    assert production.docs_enabled is False

    monkeypatch.setenv("APP_ENV", "development")
    development = load_settings(env_file=None)
    assert development.docs_enabled is True
