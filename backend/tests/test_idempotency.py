"""Idempotency-Key tests (docs/API.md "Idempotent mutations").

`POST /api/v1/staff` and `POST /api/v1/ai/ask` honour an optional
`Idempotency-Key` header (app.api.idempotency): a replay of the same key + same
payload within the retention window returns the recorded response with
`Idempotency-Replayed: true` instead of re-executing (one account, one LLM
call, one history row, one audit event), the same key with a different payload
is 409 `conflict`, a missing header is unchanged behaviour, another user's
record is never replayed, expired records are forgotten, and the one-time
generated password is never stored in the record. The migration upgrades,
downgrades, and re-upgrades cleanly.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import csrf_headers, run_alembic

STAFF_PATH = "/api/v1/staff"
ASK_PATH = "/api/v1/ai/ask"
KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"


def idem_headers(client, key: str) -> dict[str, str]:
    return {**csrf_headers(client), KEY_HEADER: key}


async def create_staff(client, *, email: str | None = None, key: str | None = None, name: str = "Idem User"):
    payload = {
        "email": email or f"idem-{uuid.uuid4().hex[:10]}@example.com",
        "display_name": name,
    }
    headers = idem_headers(client, key) if key else csrf_headers(client)
    return await client.post(STAFF_PATH, json=payload, headers=headers)


async def staff_emails(db_engine) -> list[str]:
    from app.models import StaffUser

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        rows = await session.scalars(select(StaffUser.email))
        return list(rows)


async def record_rows(db_engine, key: str) -> list:
    from app.models import IdempotencyRecord

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        rows = await session.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.key == key)
        )
        return list(rows)


# --- POST /staff -----------------------------------------------------------


@pytest.mark.postgres
async def test_staff_create_replay_returns_recorded_response_with_one_account(
    db_engine, auth_client, make_user
) -> None:
    from app.models import StaffUser

    admin = await make_user(roles=("clinical_admin",))
    client = await auth_client(admin)
    key = f"staff-key-{uuid.uuid4().hex}"
    email = f"idem-{uuid.uuid4().hex[:10]}@example.com"

    first = await create_staff(client, email=email, key=key)
    assert first.status_code == 201
    assert first.headers.get(REPLAYED_HEADER) is None
    first_body = first.json()
    user_id = first_body["user"]["id"]
    assert first_body["generated_password"]

    second = await create_staff(client, email=email, key=key)
    assert second.status_code == 201
    assert second.headers.get(REPLAYED_HEADER) == "true"
    second_body = second.json()
    assert second_body["user"]["id"] == user_id
    # The one-time password is shown once: replays never re-send it.
    assert second_body["generated_password"] is None

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        users = list((await session.scalars(select(StaffUser).where(StaffUser.email == email))).all())
        assert len(users) == 1, "a replay must not create a second account"

    rows = await record_rows(db_engine, key)
    assert len(rows) == 1
    # The recorded response never contains the secret.
    assert first_body["generated_password"] not in rows[0].response_body
    assert rows[0].status_code == 201


@pytest.mark.postgres
async def test_staff_create_replay_with_different_payload_conflicts(
    db_engine, auth_client, make_user
) -> None:
    admin = await make_user(roles=("clinical_admin",))
    client = await auth_client(admin)
    key = f"staff-key-{uuid.uuid4().hex}"
    first = await create_staff(client, key=key, name="First Name")
    assert first.status_code == 201

    other_email = f"idem-{uuid.uuid4().hex[:10]}@example.com"
    second = await create_staff(client, email=other_email, key=key, name="Other Name")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"
    assert other_email not in await staff_emails(db_engine), "the conflicting create must not run"


@pytest.mark.postgres
async def test_staff_create_without_key_is_unchanged(auth_client, make_user) -> None:
    admin = await make_user(roles=("clinical_admin",))
    client = await auth_client(admin)
    first = await create_staff(client)
    second = await create_staff(client)
    assert first.status_code == 201 and second.status_code == 201
    assert first.headers.get(REPLAYED_HEADER) is None
    assert second.headers.get(REPLAYED_HEADER) is None
    assert first.json()["user"]["id"] != second.json()["user"]["id"]


@pytest.mark.postgres
async def test_staff_create_key_never_replays_across_users(
    db_engine, auth_client, make_user
) -> None:
    admin_a = await make_user(roles=("clinical_admin",))
    admin_b = await make_user(roles=("clinical_admin",))
    key = f"staff-key-{uuid.uuid4().hex}"

    first = await create_staff(await auth_client(admin_a), key=key)
    second = await create_staff(await auth_client(admin_b), key=key)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get(REPLAYED_HEADER) is None
    assert first.json()["user"]["id"] != second.json()["user"]["id"]
    rows = await record_rows(db_engine, key)
    assert len(rows) == 2, "records are scoped per user"


@pytest.mark.postgres
async def test_invalid_idempotency_key_is_a_validation_error(auth_client, make_user) -> None:
    admin = await make_user(roles=("clinical_admin",))
    client = await auth_client(admin)
    for bad in ("has spaces!", "x" * 201, ""):
        response = await client.post(
            STAFF_PATH,
            json={"email": f"bad-key-{uuid.uuid4().hex[:8]}@example.com", "display_name": "Bad Key"},
            headers=idem_headers(client, bad),
        )
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "validation_error"


# --- POST /ai/ask ----------------------------------------------------------


async def ask(client, *, question: str, key: str | None = None):
    headers = idem_headers(client, key) if key else csrf_headers(client)
    return await client.post(ASK_PATH, json={"question": question}, headers=headers)


async def conversation_count(db_engine) -> int:
    from app.models import AiConversation

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        rows = await session.scalars(select(AiConversation))
        return len(list(rows))


@pytest.mark.postgres
async def test_ask_replay_returns_recorded_answer_without_a_second_llm_call(
    db_engine, auth_client, make_user, stub_ai_provider
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    key = f"ask-key-{uuid.uuid4().hex}"
    question = "Which synthetic patients are on metformin?"

    first = await ask(client, question=question, key=key)
    assert first.status_code == 200
    assert first.headers.get(REPLAYED_HEADER) is None
    assert len(stub_ai_provider.calls) == 1
    history_before = await conversation_count(db_engine)

    second = await ask(client, question=question, key=key)
    assert second.status_code == 200
    assert second.headers.get(REPLAYED_HEADER) == "true"
    assert second.json() == first.json(), "the replay must be the recorded response"
    assert len(stub_ai_provider.calls) == 1, "a replay must not call the LLM again"

    assert await conversation_count(db_engine) == history_before, "no second history row"

    rows = await record_rows(db_engine, key)
    assert len(rows) == 1
    assert rows[0].status_code == 200


@pytest.mark.postgres
async def test_ask_replay_with_different_payload_conflicts(
    auth_client, make_user, stub_ai_provider
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    key = f"ask-key-{uuid.uuid4().hex}"

    first = await ask(client, question="First question about synthetic data?", key=key)
    assert first.status_code == 200
    second = await ask(client, question="A different question entirely?", key=key)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"
    assert len(stub_ai_provider.calls) == 1, "the conflicting ask must not run"


@pytest.mark.postgres
async def test_ask_without_key_repeats_normally(auth_client, make_user, stub_ai_provider) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    first = await ask(client, question="Anything new in the synthetic records?")
    second = await ask(client, question="Anything new in the synthetic records?")
    assert first.status_code == 200 and second.status_code == 200
    assert first.headers.get(REPLAYED_HEADER) is None
    assert second.headers.get(REPLAYED_HEADER) is None
    assert len(stub_ai_provider.calls) == 2


@pytest.mark.postgres
async def test_ask_key_never_replays_across_users(
    auth_client, make_user, stub_ai_provider
) -> None:
    doctor_a = await make_user(roles=("doctor",))
    doctor_b = await make_user(roles=("doctor",))
    key = f"ask-key-{uuid.uuid4().hex}"

    first = await ask(await auth_client(doctor_a), question="Shared key question one?", key=key)
    second = await ask(await auth_client(doctor_b), question="Shared key question one?", key=key)
    assert first.status_code == 200 and second.status_code == 200
    assert second.headers.get(REPLAYED_HEADER) is None
    assert len(stub_ai_provider.calls) == 2


@pytest.mark.postgres
async def test_expired_record_is_forgotten_and_the_request_executes(
    db_engine, auth_client, make_user, stub_ai_provider
) -> None:
    from datetime import datetime, timedelta, timezone

    from app.models import IdempotencyRecord

    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    key = f"ask-key-{uuid.uuid4().hex}"
    question = "Does this synthetic question expire cleanly?"

    first = await ask(client, question=question, key=key)
    assert first.status_code == 200
    assert len(stub_ai_provider.calls) == 1

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.key == key)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await session.commit()

    second = await ask(client, question=question, key=key)
    assert second.status_code == 200
    assert second.headers.get(REPLAYED_HEADER) is None, "an expired record must be forgotten"
    assert len(stub_ai_provider.calls) == 2
    rows = await record_rows(db_engine, key)
    assert len(rows) == 1, "the expired row is pruned and replaced"
    assert rows[0].expires_at > datetime.now(timezone.utc)


# --- insert-race semantics (unique constraint as the arbiter) ---------------


@pytest.mark.postgres
async def test_concurrent_insert_race_replays_the_winner(db_engine, make_user) -> None:
    """The second insert loses the unique constraint and serves the winner's record."""
    from app.api.idempotency import ENDPOINT_AI_ASK, IdempotencyContext

    account = await make_user(roles=("doctor",))
    key = f"ask-key-{uuid.uuid4().hex}"
    payload = {"question": "Race question?"}
    context = IdempotencyContext.build(
        key=key, user_id=account.id, endpoint=ENDPOINT_AI_ASK, payload=payload
    )
    assert context is not None

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        first = await context.record(session, status_code=200, body={"answer": "winner"})
        assert first is None
        await session.commit()

        lost = await context.record(session, status_code=200, body={"answer": "loser"})
        assert lost is not None, "the second insert must lose and replay"
        assert lost.status_code == 200
        assert lost.body == {"answer": "winner"}


# --- migration up/down/up ---------------------------------------------------


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
        return await connection.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
    finally:
        await connection.close()


@pytest.mark.postgres
def test_idempotency_migration_up_down_up(migrated_test_db: str, test_db_url: str) -> None:
    """Revision d4b8f2c6a1e3 upgrades, downgrades, and re-upgrades cleanly."""
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "admin.idempotency_record")) is True

    result = run_alembic("downgrade", "9f1c3d5a7b24")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "admin.idempotency_record")) is False
    assert asyncio.run(_table_exists(test_db_url, "admin.ai_conversation")) is True

    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "admin.idempotency_record")) is True
