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


async def _column_exists(url: str, table: str, column: str) -> bool:
    """Parameterized information_schema lookup of one qualified column."""
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
    schema, name = table.split(".", 1)
    connection = await asyncpg.connect(**connect_kwargs)  # type: ignore[arg-type]
    try:
        return await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 AND column_name = $3)",
            schema,
            name,
            column,
        )
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
    assert asyncio.run(_table_exists(test_db_url, "scheduling.appointment_event")) is True
    assert asyncio.run(_table_exists(test_db_url, "admin.message_attachment")) is True

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


# Tables and columns introduced by revision c7f2b4d9e013 (clinical data layer).
CLINICAL_LAYER_TABLES = (
    "patients.diagnosis",
    "patients.clinical_note",
    "patients.patient_assignment",
    "laboratory.test_catalog",
    "laboratory.reference_range",
    "laboratory.lab_order",
    "laboratory.specimen",
    "medications.prescription_item",
    "medications.prescription_history",
    "medications.prescription_change",
    "admin.permission",
)

CLINICAL_LAYER_COLUMNS = (
    ("patients.patient", "blood_type"),
    ("patients.encounter", "practitioner_id"),
    ("patients.encounter", "weight_kg"),
    ("patients.encounter", "height_cm"),
)


def test_clinical_data_layer_migration_up_down_up(migrated_test_db: str, test_db_url: str) -> None:
    """Revision c7f2b4d9e013 upgrades, downgrades, and re-upgrades cleanly.

    The downgrade must drop every new table and remove the added columns; the
    pre-existing schema must survive untouched.
    """
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    for table in CLINICAL_LAYER_TABLES:
        assert asyncio.run(_table_exists(test_db_url, table)) is True, table
    for table, column in CLINICAL_LAYER_COLUMNS:
        assert asyncio.run(_column_exists(test_db_url, table, column)) is True, f"{table}.{column}"

    result = run_alembic("downgrade", "e8c2a91f4d77")
    assert result.returncode == 0, result.stderr
    for table in CLINICAL_LAYER_TABLES:
        assert asyncio.run(_table_exists(test_db_url, table)) is False, table
    for table, column in CLINICAL_LAYER_COLUMNS:
        assert asyncio.run(_column_exists(test_db_url, table, column)) is False, f"{table}.{column}"
    assert asyncio.run(_table_exists(test_db_url, "patients.patient")) is True
    assert asyncio.run(_table_exists(test_db_url, "medications.prescription")) is True

    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    for table in CLINICAL_LAYER_TABLES:
        assert asyncio.run(_table_exists(test_db_url, table)) is True, table
    for table, column in CLINICAL_LAYER_COLUMNS:
        assert asyncio.run(_column_exists(test_db_url, table, column)) is True, f"{table}.{column}"
