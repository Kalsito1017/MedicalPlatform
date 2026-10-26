"""Shared test fixtures.

Postgres-marked tests need a reachable PostgreSQL database. Defaults:
  TEST_DATABASE_URL = postgresql+asyncpg://app:change-me@localhost:5432/clinical_demo_test
Start it with `docker compose up -d db`. When Postgres is unreachable, those
tests SKIP with a clear reason (never silently fake a pass).

Tests always target the isolated test database; the development database is
never touched by the test suite.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
TEST_DB_NAME = "clinical_demo_test"
DEFAULT_TEST_DB_URL = f"postgresql+asyncpg://app:change-me@localhost:5432/{TEST_DB_NAME}"

# Applied before any app import so Settings resolves without a .env file.
os.environ["APP_ENV"] = "test"
os.environ["APP_SECRET_KEY"] = "test-only-secret-not-for-production"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
os.environ["MIGRATION_DATABASE_URL"] = os.environ["DATABASE_URL"]
os.environ["ALLOWED_ORIGINS"] = "http://localhost:5173,http://localhost:8000"

TEST_DB_URL = os.environ["DATABASE_URL"]

UNREACHABLE_REASON = (
    "PostgreSQL is not reachable at the configured TEST_DATABASE_URL. "
    "Start it with `docker compose up -d db` (or point TEST_DATABASE_URL at a running server)."
)


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """Run an alembic command against the isolated test database."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    env["MIGRATION_DATABASE_URL"] = TEST_DB_URL
    env.setdefault("APP_SECRET_KEY", "test-only-secret-not-for-production")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND_ROOT / "alembic.ini"), *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def run_seed(extra_env: dict[str, str | None] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the demo seed script against the isolated test database.

    A None value in extra_env removes that variable from the environment.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    env.setdefault("APP_SECRET_KEY", "test-only-secret-not-for-production")
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "seed_demo_data.py")],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


async def _ensure_test_database() -> None:
    """Create the default test database when missing (constant DDL, no interpolation)."""
    import asyncpg
    from sqlalchemy.engine import make_url

    parsed = make_url(TEST_DB_URL)
    if parsed.database != TEST_DB_NAME:
        return  # custom TEST_DATABASE_URL: the database must already exist
    connect_kwargs: dict[str, object] = {
        "host": parsed.host or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "database": "postgres",
        "timeout": 5,
    }
    if parsed.password:
        connect_kwargs["password"] = parsed.password
    connection = await asyncpg.connect(**connect_kwargs)  # type: ignore[arg-type]
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME)
        if not exists:
            # TEST_DB_NAME is a module constant, validated below; never derived
            # from request/user input. CREATE DATABASE cannot be parameterized.
            assert TEST_DB_NAME.replace("_", "").isalnum(), "unsafe test database name"
            await connection.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def test_db_url() -> str:
    return TEST_DB_URL


@pytest.fixture(scope="session")
def migrated_test_db() -> str:
    """Ensure the test database exists and is migrated (alembic upgrade head)."""
    try:
        asyncio.run(_ensure_test_database())
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(f"{UNREACHABLE_REASON} (cause: {type(exc).__name__})")

    result = run_alembic("upgrade", "head")
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return TEST_DB_URL


@pytest.fixture
async def db_engine(migrated_test_db: str) -> AsyncIterator[object]:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(migrated_test_db, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def app():
    from app.config import load_settings
    from app.main import create_app

    return create_app(load_settings(env_file=None))


@pytest.fixture
async def client(app) -> AsyncIterator[object]:
    import httpx

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as value:
        yield value
