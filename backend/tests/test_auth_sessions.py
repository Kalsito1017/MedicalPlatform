"""Session service tests: token hashing, expiry, revocation, revoke_all.

Unit tests run without a database; lifecycle tests are marked `postgres`.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sessions as sessions_service
from app.models import StaffUser, UserSession


def test_hash_session_token_is_sha256_hex() -> None:
    token = "raw-session-token-value"
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert sessions_service.hash_session_token(token) == expected
    assert len(expected) == 64
    assert sessions_service.hash_session_token(token) != token


def test_generated_tokens_are_unique_and_url_safe() -> None:
    tokens = {sessions_service.generate_session_token() for _ in range(10)}
    assert len(tokens) == 10
    for token in tokens:
        assert len(token) >= 40  # 32 random bytes -> >= 40 URL-safe chars
        assert token.replace("-", "").replace("_", "").isalnum()


@pytest.mark.postgres
async def test_create_and_load_session_roundtrip(db_engine, make_user) -> None:
    account = await make_user(roles=("doctor",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        raw_token, row = await sessions_service.create_session(db, user_id=account.id)
        await db.commit()
        assert row.id is not None

        loaded = await sessions_service.load_session(db, raw_token)
        assert loaded is not None
        session_row, user = loaded
        assert session_row.id == row.id
        assert user.id == account.id


@pytest.mark.postgres
async def test_raw_token_is_never_stored(db_engine, make_user) -> None:
    account = await make_user(roles=("nurse",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        raw_token, row = await sessions_service.create_session(db, user_id=account.id)
        await db.commit()

        stored = await db.scalar(select(UserSession).where(UserSession.id == row.id))
        assert stored is not None
        assert stored.token_hash == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        assert raw_token not in (stored.token_hash, str(stored.id))


@pytest.mark.postgres
async def test_load_session_rejects_unknown_token(db_engine) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        assert await sessions_service.load_session(db, "not-a-real-token") is None
        assert await sessions_service.load_session(db, "") is None


@pytest.mark.postgres
async def test_load_session_enforces_expiry_server_side(db_engine, make_user) -> None:
    account = await make_user(roles=("doctor",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        raw_token, row = await sessions_service.create_session(
            db, user_id=account.id, ttl_hours=-1
        )
        await db.commit()
        assert row.expires_at <= datetime.now(timezone.utc)

        assert await sessions_service.load_session(db, raw_token) is None


@pytest.mark.postgres
async def test_load_session_rejects_revoked_session(db_engine, make_user) -> None:
    account = await make_user(roles=("doctor",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        raw_token, _row = await sessions_service.create_session(db, user_id=account.id)
        await db.commit()

        assert await sessions_service.revoke_session(db, raw_token) is True
        await db.commit()
        assert await sessions_service.load_session(db, raw_token) is None
        # Revocation is idempotent: nothing left to revoke.
        assert await sessions_service.revoke_session(db, raw_token) is False


@pytest.mark.postgres
async def test_revoke_all_sessions_revokes_every_session(db_engine, make_user) -> None:
    account = await make_user(roles=("doctor",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        first, _ = await sessions_service.create_session(db, user_id=account.id)
        second, _ = await sessions_service.create_session(db, user_id=account.id)
        await db.commit()
        assert await sessions_service.load_session(db, first) is not None
        assert await sessions_service.load_session(db, second) is not None

        revoked = await sessions_service.revoke_all_sessions(db, account.id)
        await db.commit()
        assert revoked == 2
        assert await sessions_service.load_session(db, first) is None
        assert await sessions_service.load_session(db, second) is None
        # Nothing remains to revoke.
        assert await sessions_service.revoke_all_sessions(db, account.id) == 0


@pytest.mark.postgres
async def test_load_session_rejects_inactive_or_deleted_user(db_engine, make_user) -> None:
    inactive = await make_user(roles=("doctor",), is_active=False)
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        raw_token, _ = await sessions_service.create_session(db, user_id=inactive.id)
        await db.commit()
        assert await sessions_service.load_session(db, raw_token) is None

    active = await make_user(roles=("doctor",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        raw_token, _ = await sessions_service.create_session(db, user_id=active.id)
        await db.commit()
        # Soft-delete the user behind the session (separate transaction).
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        user_row = await db.get(StaffUser, active.id)
        user_row.deleted_at = datetime.now(timezone.utc)
        await db.commit()
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        assert await sessions_service.load_session(db, raw_token) is None


@pytest.mark.postgres
async def test_session_expiry_and_created_defaults(db_engine, make_user) -> None:
    account = await make_user(roles=("doctor",))
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        _raw, row = await sessions_service.create_session(db, user_id=account.id, ip="203.0.113.7")
        await db.commit()
        fresh = await db.get(UserSession, row.id)
        assert fresh is not None
        assert fresh.revoked_at is None
        assert fresh.ip == "203.0.113.7"
        assert fresh.user_agent is None
        ttl = fresh.expires_at - fresh.created_at
        assert timedelta(hours=7) < ttl <= timedelta(hours=8)


@pytest.mark.postgres
async def test_session_rows_cascade_when_user_is_deleted(db_engine, make_user) -> None:
    account = await make_user(roles=())  # no role grants (their FK is RESTRICT)
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        _raw, row = await sessions_service.create_session(db, user_id=account.id)
        await db.commit()
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        user_row = await db.get(StaffUser, account.id)
        await db.delete(user_row)  # hard delete: session rows cascade away
        await db.commit()
        assert await db.get(UserSession, row.id) is None
