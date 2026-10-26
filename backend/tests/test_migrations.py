"""Migration tests: alembic upgrade/downgrade/upgrade against real PostgreSQL.

Marked `postgres`; skips with a clear reason when PostgreSQL is unreachable.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import run_alembic


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
