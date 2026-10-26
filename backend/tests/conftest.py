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
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
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

# Test-only password marker. Tests assert this value never appears in audit
# rows, logs, or API responses. It is not a real credential.
SENTINEL_PASSWORD = "Sentinel-P@ssw0rd-9f3c2b"
ALL_ROLE_KEYS = ("doctor", "nurse", "clinical_admin", "tech_admin")


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


# ---------------------------------------------------------------------------
# Phase 5/6 fixtures: staff accounts, clients, deterministic clinical data
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_login_rate_limiter() -> AsyncIterator[None]:
    """Isolate the in-process login limiter between tests."""
    from app.auth.ratelimit import login_rate_limiter

    login_rate_limiter.reset()
    yield
    login_rate_limiter.reset()


@pytest.fixture(autouse=True)
async def _shared_engine_per_event_loop() -> AsyncIterator[None]:
    """Give every test a fresh app.db.base engine.

    pytest-asyncio runs each test in its own event loop and asyncpg connections
    are loop-bound, so the shared request-scoped engine must not survive across
    tests. Production keeps one engine for the process lifetime.
    """
    from app.db import base as db_base

    async def _reset() -> None:
        try:
            await db_base.dispose_engine()
        except Exception:  # noqa: BLE001 - stale loop-bound leftovers are dropped
            pass
        finally:
            db_base._engine = None
            db_base._session_factory = None

    await _reset()
    yield
    await _reset()


@dataclass(frozen=True)
class StaffAccount:
    id: uuid.UUID
    email: str
    password: str
    roles: tuple[str, ...]


@pytest.fixture
async def make_user(db_engine) -> Callable[..., Awaitable[StaffAccount]]:
    """Create an active staff account with role grants and a known password."""
    from argon2 import PasswordHasher
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Role, StaffUser, UserRole

    hasher = PasswordHasher()

    async def _make(
        *,
        roles: tuple[str, ...] = ("doctor",),
        email: str | None = None,
        password: str = SENTINEL_PASSWORD,
        is_active: bool = True,
        display_name: str | None = None,
    ) -> StaffAccount:
        account_email = email or f"test-user-{uuid.uuid4().hex[:12]}@example.com"
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            role_ids: dict[str, uuid.UUID] = {}
            for key in ALL_ROLE_KEYS:
                role = await session.scalar(select(Role).where(Role.key == key))
                if role is None:
                    role = Role(key=key, description="test role")
                    session.add(role)
                    await session.flush()
                role_ids[key] = role.id
            user = StaffUser(
                email=account_email,
                display_name=display_name or f"Test User {account_email}",
                password_hash=hasher.hash(password),
                is_active=is_active,
            )
            session.add(user)
            await session.flush()
            for key in roles:
                session.add(UserRole(user_id=user.id, role_id=role_ids[key]))
            await session.commit()
        return StaffAccount(id=user.id, email=account_email, password=password, roles=tuple(roles))

    return _make


@pytest.fixture
async def make_client(app) -> AsyncIterator[Callable[[], Awaitable[object]]]:
    """Factory for fresh anonymous httpx clients (one per call)."""
    import httpx

    created: list = []

    async def _make():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        created.append(client)
        return client

    yield _make
    for item in created:
        await item.aclose()


@pytest.fixture
def api_login(make_client) -> Callable[[StaffAccount], Awaitable[object]]:
    """Log in through the real API and return the authenticated client."""

    async def _login(account: StaffAccount):
        client = await make_client()
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": account.email, "password": account.password},
        )
        assert response.status_code == 200, response.text
        return client

    return _login


@pytest.fixture
def auth_client(db_engine, make_client) -> Callable[[StaffAccount], Awaitable[object]]:
    """Authenticated client WITHOUT the login round-trip (fast permission matrix).

    Creates the session row through app.auth.sessions and injects the raw
    cookie — the same identity path the API resolves. The full login flow is
    covered by tests/test_auth_api.py.
    """

    async def _for(account: StaffAccount):
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.auth.sessions import create_session

        client = await make_client()
        async with AsyncSession(db_engine, expire_on_commit=False) as db:
            raw_token, _ = await create_session(db, user_id=account.id)
            await db.commit()
        client.cookies.set("cap_session", raw_token)
        client.cookies.set("csrf_token", "test-csrf-token-value")
        return client

    return _for


def csrf_headers(client) -> dict[str, str]:
    """X-CSRF-Token header matching the client's csrf_token cookie (if any)."""
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


async def audit_rows(**filters) -> list:
    """Audit events (importable helper), optionally filtered by column values."""
    from sqlalchemy import select

    from app.db.base import get_session_factory
    from app.models import AuditEvent

    async with get_session_factory()() as session:
        stmt = select(AuditEvent)
        for key, value in filters.items():
            stmt = stmt.where(getattr(AuditEvent, key) == value)
        stmt = stmt.order_by(AuditEvent.created_at, AuditEvent.id)
        return list((await session.scalars(stmt)).all())


@dataclass
class ClinicalData:
    """Deterministic synthetic fixture data (no RNG; values are exact)."""

    token: str
    patient_id: uuid.UUID
    patient_name: str
    patient_mrn: str
    empty_patient_id: uuid.UUID
    empty_patient_name: str
    soft_deleted_patient_id: uuid.UUID
    encounter_id: uuid.UUID
    old_encounter_id: uuid.UUID
    soft_deleted_encounter_id: uuid.UUID
    soft_deleted_lab_id: uuid.UUID
    lab_ids: list[uuid.UUID]
    prescription_ids: list[uuid.UUID]


@pytest.fixture
async def clinical_data(db_engine) -> AsyncIterator[ClinicalData]:
    """Create one synthetic patient with bounded clinical history.

    Values are fixed (Decimal strings, fixed UTC timestamps) so tests can make
    exact assertions; `token` is unique per test so `?q=` filters match exactly
    this fixture even against a persistent test database.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Encounter, LabResult, Patient, Prescription

    token = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    patient_id = uuid.uuid4()
    empty_patient_id = uuid.uuid4()
    soft_deleted_patient_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    old_encounter_id = uuid.uuid4()
    soft_deleted_encounter_id = uuid.uuid4()
    soft_deleted_lab_id = uuid.uuid4()
    lab_ids = [uuid.uuid4() for _ in range(4)]
    prescription_ids = [uuid.uuid4() for _ in range(3)]

    patient_name = f"Fixture Alpha {token}"
    empty_patient_name = f"Fixture Beta {token}"

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        # Explicit dependency order + flushes (the models have no ORM
        # relationships, so the unit of work cannot topologically sort them).
        session.add_all(
            [
                Patient(
                    id=patient_id,
                    mrn=f"DEMO-T{token}A",
                    display_name=patient_name,
                    given_name="Fixture",
                    family_name=f"Alpha {token}",
                    date_of_birth=date(1980, 1, 15),
                    sex="female",
                    provenance="synthetic",
                ),
                Patient(
                    id=empty_patient_id,
                    mrn=f"DEMO-T{token}B",
                    display_name=empty_patient_name,
                    given_name="Fixture",
                    family_name=f"Beta {token}",
                    date_of_birth=date(1992, 6, 30),
                    sex="male",
                    provenance="synthetic",
                ),
                Patient(
                    id=soft_deleted_patient_id,
                    mrn=f"DEMO-T{token}C",
                    display_name=f"Fixture Gone {token}",
                    given_name="Fixture",
                    family_name=f"Gone {token}",
                    date_of_birth=date(1970, 12, 1),
                    sex="unknown",
                    provenance="synthetic",
                    deleted_at=now,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Encounter(
                    id=encounter_id,
                    patient_id=patient_id,
                    occurred_at=datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc),
                    encounter_type="annual exam",
                    summary=f"Synthetic demo encounter {token}. Fictional data, not for clinical use.",
                ),
                Encounter(
                    id=old_encounter_id,
                    patient_id=patient_id,
                    occurred_at=datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
                    encounter_type="lab draw",
                    summary=f"Synthetic demo encounter {token} (older).",
                ),
                Encounter(
                    id=soft_deleted_encounter_id,
                    patient_id=patient_id,
                    occurred_at=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
                    encounter_type="urgent care visit",
                    summary=f"Soft-deleted synthetic encounter {token}.",
                    deleted_at=now,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                LabResult(
                    id=lab_ids[0],
                    patient_id=patient_id,
                    encounter_id=encounter_id,
                    test_code="GLU",
                    test_name="Glucose, fasting",
                    value_num=Decimal("5.500"),
                    unit="mg/dL",
                    ref_low=Decimal("4.000"),
                    ref_high=Decimal("6.000"),
                    status="final",
                    collected_at=datetime(2026, 10, 1, 7, 30, tzinfo=timezone.utc),
                    resulted_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
                ),
                LabResult(
                    id=lab_ids[1],
                    patient_id=patient_id,
                    encounter_id=encounter_id,
                    test_code="GLU",
                    test_name="Glucose, fasting",
                    value_num=Decimal("6.100"),
                    unit="mg/dL",
                    ref_low=Decimal("4.000"),
                    ref_high=Decimal("6.000"),
                    status="final",
                    collected_at=datetime(2026, 10, 3, 7, 30, tzinfo=timezone.utc),
                    resulted_at=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
                ),
                LabResult(
                    id=lab_ids[2],
                    patient_id=patient_id,
                    encounter_id=encounter_id,
                    test_code="K",
                    test_name="Potassium",
                    value_num=Decimal("4.200"),
                    unit="mmol/L",
                    ref_low=Decimal("3.500"),
                    ref_high=Decimal("5.100"),
                    status="final",
                    collected_at=datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc),
                    resulted_at=datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc),
                ),
                LabResult(
                    id=lab_ids[3],
                    patient_id=patient_id,
                    test_code="TSH",
                    test_name="TSH",
                    value_text="within range",
                    unit="mIU/L",
                    status="final",
                    collected_at=datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc),
                ),
                LabResult(
                    id=soft_deleted_lab_id,
                    patient_id=patient_id,
                    test_code="HGB",
                    test_name="Hemoglobin",
                    value_num=Decimal("13.000"),
                    unit="g/dL",
                    status="final",
                    collected_at=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc),
                    deleted_at=now,
                ),
                Prescription(
                    id=prescription_ids[0],
                    patient_id=patient_id,
                    medication_name="Metformin",
                    dose_amount=Decimal("500.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="twice daily",
                    status="active",
                    start_date=date(2026, 1, 1),
                ),
                Prescription(
                    id=prescription_ids[1],
                    patient_id=patient_id,
                    medication_name="Lisinopril",
                    dose_amount=Decimal("10.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="once daily",
                    status="active",
                    start_date=date(2026, 2, 1),
                ),
                Prescription(
                    id=prescription_ids[2],
                    patient_id=patient_id,
                    medication_name="Amoxicillin",
                    dose_amount=Decimal("250.000"),
                    dose_unit="mg",
                    route="oral",
                    frequency="three times daily",
                    status="completed",
                    start_date=date(2025, 11, 1),
                    end_date=date(2025, 11, 14),
                ),
            ]
        )
        await session.commit()

    data = ClinicalData(
        token=token,
        patient_id=patient_id,
        patient_name=patient_name,
        patient_mrn=f"DEMO-T{token}A",
        empty_patient_id=empty_patient_id,
        empty_patient_name=empty_patient_name,
        soft_deleted_patient_id=soft_deleted_patient_id,
        encounter_id=encounter_id,
        old_encounter_id=old_encounter_id,
        soft_deleted_encounter_id=soft_deleted_encounter_id,
        soft_deleted_lab_id=soft_deleted_lab_id,
        lab_ids=lab_ids,
        prescription_ids=prescription_ids,
    )
    yield data
