"""Migration tests: alembic upgrade/downgrade/upgrade against real PostgreSQL.

Marked `postgres`; skips with a clear reason when PostgreSQL is unreachable.
"""
from __future__ import annotations

import asyncio


from conftest import BACKEND_ROOT, run_alembic


async def _table_exists(url: str, table: str) -> bool:
    import asyncpg
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    connect_kwargs: dict[str, object] = {
        "host": parsed.host or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "database": parsed.database or "postgres",
        "timeout": 5,
    }
    if parsed.password:
        connect_kwargs["password"] = parsed.password
    connection = await asyncpg.connect(**connect_kwargs)  # type: ignore[arg-type]
    try:
        # Parameterized lookup of a qualified relation name.
        return await connection.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
    finally:
        await connection.close()


def test_migration_up_down_up(migrated_test_db: str, test_db_url: str) -> None:
    # Start from a clean slate regardless of earlier test order.
    result = run_alembic("downgrade", "base")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "patients.patient")) is False

    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "patients.patient")) is True
    assert asyncio.run(_table_exists(test_db_url, "admin.staff_user")) is True
    assert asyncio.run(_table_exists(test_db_url, "laboratory.lab_result")) is True
    assert asyncio.run(_table_exists(test_db_url, "medications.prescription")) is True

    result = run_alembic("downgrade", "base")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "patients.patient")) is False

    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "patients.patient")) is True


def test_alembic_cli_reads_env_file_when_no_env_vars(migrated_test_db: str, test_db_url: str) -> None:
    """Regression: the documented dev path (no DATABASE_URL exported, .env in cwd) must work.

    `migrations/env.py` falls back to `load_settings()` only when neither
    MIGRATION_DATABASE_URL nor DATABASE_URL is set. That branch previously
    crashed with `TypeError: 'bool' object is not iterable`.
    """
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, ".env").write_text(
            f"APP_SECRET_KEY=test-only-secret-not-for-production\nDATABASE_URL={test_db_url}\n",
            encoding="utf-8",
        )
        # Keep the temp cwd (so the .env fallback is exercised) while pointing
        # alembic at the real migrations directory via an absolute path.
        ini = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8").replace(
            "script_location = migrations",
            f"script_location = {BACKEND_ROOT / 'migrations'}",
        )
        Path(tmp, "alembic.ini").write_text(ini, encoding="utf-8")
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_SECRET_KEY"}
        }
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(Path(tmp, "alembic.ini")), "current"],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
    assert result.returncode == 0, result.stderr
    assert "(head)" in result.stdout
