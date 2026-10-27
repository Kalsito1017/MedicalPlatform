"""Conversation-history tests (docs/API.md "AI assistant" history endpoints).

Covers: persistence of every successful ask (patient-scoped and general modes,
including tool provenance) into the caller's private history, the frozen ask
response shape staying untouched, per-user isolation (user A can NEVER see,
delete, or clear user B's history - cross-user reads are empty, cross-user
deletes are 404), pagination + patient filter bounds, clear-history 204 with
the `ai.history.clear` audit event, delete-own-row with `ai.history.delete`,
CSRF on non-GET, 401/403 gates, and the migration up/down/up cycle. No live
LLM calls anywhere: the provider is the scripted test double from conftest.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import (
    STUB_ANSWER,
    STUB_MODEL_NAME,
    ScriptedLLMProvider,
    audit_rows,
    csrf_headers,
    run_alembic,
    text_result,
    tool_call_result,
)

ASK_PATH = "/api/v1/ai/ask"
HISTORY_PATH = "/api/v1/ai/conversations"
ASK_RESPONSE_KEYS = {"answer", "used_tools", "citations", "model", "ai_generated", "disclaimer"}
ITEM_KEYS = {"id", "patient_id", "question", "answer", "used_tools", "citations", "model", "created_at"}


async def ask(client, patient_id, question="What are the latest lab results?"):
    return await client.post(
        ASK_PATH,
        json={"patient_id": str(patient_id), "question": question},
        headers=csrf_headers(client),
    )


async def ask_general(client, question="Which patients are on metformin?"):
    return await client.post(ASK_PATH, json={"question": question}, headers=csrf_headers(client))


def use_provider(monkeypatch, provider) -> None:
    from app.api.v1 import ai as ai_api

    monkeypatch.setattr(ai_api, "build_provider", lambda settings: provider)


async def seed_history(db_engine, rows: list[dict]) -> list[uuid.UUID]:
    """Insert history rows directly (explicit timestamps for stable ordering)."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import AiConversation

    ids: list[uuid.UUID] = []
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        for spec in rows:
            row_id = uuid.uuid4()
            session.add(
                AiConversation(
                    id=row_id,
                    user_id=spec["user_id"],
                    patient_id=spec.get("patient_id"),
                    question=spec.get("question", "Seeded question?"),
                    answer=spec.get("answer", "Seeded answer."),
                    used_tools=spec.get("used_tools", "[]"),
                    citations=spec.get("citations", "[]"),
                    model=spec.get("model", STUB_MODEL_NAME),
                    created_at=spec["created_at"],
                )
            )
            ids.append(row_id)
        await session.commit()
    return ids


def at(minutes_ago: int) -> datetime:
    return datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago)


# --- persistence on ask ----------------------------------------------------


@pytest.mark.postgres
async def test_ask_persists_the_patient_mode_exchange(
    monkeypatch, auth_client, make_user, clinical_data
) -> None:
    patient_id = str(clinical_data.patient_id)
    scripted = ScriptedLLMProvider(
        tool_call_result("get_patient_summary", {"patient_id": patient_id}),
        text_result("The latest fasting glucose is 6.100 mg/dL."),
    )
    use_provider(monkeypatch, scripted)

    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await ask(client, clinical_data.patient_id, question="What is the latest glucose?")
    assert response.status_code == 200, response.text
    # The frozen six-key ask response shape is unchanged by persistence.
    assert set(response.json()) == ASK_RESPONSE_KEYS

    page = (await client.get(HISTORY_PATH)).json()
    assert page["total"] == 1
    assert page["limit"] == 20 and page["offset"] == 0
    assert len(page["items"]) == 1

    item = page["items"][0]
    assert set(item) == ITEM_KEYS
    assert item["question"] == "What is the latest glucose?"
    assert item["answer"] == "The latest fasting glucose is 6.100 mg/dL."
    assert item["used_tools"] == ["get_patient_summary"]
    assert item["model"] == STUB_MODEL_NAME
    assert item["patient_id"] == patient_id
    assert {"type": "patient", "id": patient_id} in item["citations"]
    assert item["created_at"].endswith("Z")


@pytest.mark.postgres
async def test_ask_persists_the_general_mode_exchange(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await ask_general(client, "Which synthetic patients are on metformin?")
    assert response.status_code == 200, response.text
    assert set(response.json()) == ASK_RESPONSE_KEYS

    page = (await client.get(HISTORY_PATH)).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["question"] == "Which synthetic patients are on metformin?"
    assert item["answer"] == STUB_ANSWER
    assert item["used_tools"] == []
    assert item["citations"] == []
    assert item["patient_id"] is None  # general lookup mode is recorded as NULL


@pytest.mark.postgres
async def test_each_ask_writes_exactly_one_history_row(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    for question in ("First question?", "Second question?"):
        assert (await ask(client, clinical_data.patient_id, question)).status_code == 200

    page = (await client.get(HISTORY_PATH)).json()
    assert page["total"] == 2
    assert [item["question"] for item in page["items"]] == ["Second question?", "First question?"]


@pytest.mark.postgres
async def test_rejected_ask_never_persists_anything(
    auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    response = await ask(client, clinical_data.patient_id, question="x" * 2001)
    assert response.status_code == 400
    assert (await client.get(HISTORY_PATH)).json()["total"] == 0


# --- per-user isolation -----------------------------------------------------


@pytest.mark.postgres
async def test_history_is_per_user_and_never_leaks(
    auth_client, make_user, clinical_data
) -> None:
    doctor = await make_user(roles=("doctor",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    nurse_client = await auth_client(nurse)

    assert (await ask(doctor_client, clinical_data.patient_id, "Doctor question?")).status_code == 200
    assert (await ask_general(doctor_client, "Doctor general question?")).status_code == 200

    doctor_page = (await doctor_client.get(HISTORY_PATH)).json()
    assert doctor_page["total"] == 2

    # The second user sees NOTHING of the first user's history: no reads leak.
    for query in ("", f"?patient_id={clinical_data.patient_id}", "?limit=100&offset=0"):
        page = (await nurse_client.get(f"{HISTORY_PATH}{query}")).json()
        assert page["items"] == [], query
        assert page["total"] == 0, query

    doctor_ids = {item["id"] for item in doctor_page["items"]}
    for row_id in doctor_ids:
        # Cross-user deletes are concealed as 404 and change nothing.
        response = await nurse_client.delete(f"{HISTORY_PATH}/{row_id}", headers=csrf_headers(nurse_client))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    # Cross-user "clear" clears only the caller's own (empty) history.
    assert (await nurse_client.delete(HISTORY_PATH, headers=csrf_headers(nurse_client))).status_code == 204
    assert (await doctor_client.get(HISTORY_PATH)).json()["total"] == 2


# --- pagination + patient filter -------------------------------------------


@pytest.mark.postgres
async def test_history_pagination_and_patient_filter(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    other_patient = uuid.uuid4()
    now_id = account.id
    await seed_history(
        db_engine,
        [
            {"user_id": now_id, "patient_id": clinical_data.patient_id, "question": "newest", "created_at": at(1)},
            {"user_id": now_id, "question": "general middle", "created_at": at(2)},
            {"user_id": now_id, "patient_id": other_patient, "question": "other patient", "created_at": at(3)},
            {
                "user_id": now_id,
                "patient_id": clinical_data.patient_id,
                "question": "oldest",
                "created_at": at(4),
                "used_tools": '["get_patient_summary"]',
                "citations": '[{"type": "patient", "id": "p-x"}, {"type": "bogus_type", "id": "x"}]',
            },
        ],
    )
    client = await auth_client(account)

    # Newest first, standard envelope, deterministic paging.
    page = (await client.get(f"{HISTORY_PATH}?limit=2&offset=0")).json()
    assert page["total"] == 4
    assert [item["question"] for item in page["items"]] == ["newest", "general middle"]
    page = (await client.get(f"{HISTORY_PATH}?limit=2&offset=2")).json()
    assert [item["question"] for item in page["items"]] == ["other patient", "oldest"]

    # Optional patient filter: only that patient's scoped questions.
    page = (await client.get(f"{HISTORY_PATH}?patient_id={clinical_data.patient_id}")).json()
    assert page["total"] == 2
    assert [item["question"] for item in page["items"]] == ["newest", "oldest"]
    oldest = page["items"][1]
    assert oldest["used_tools"] == ["get_patient_summary"]
    # Citations with an unknown type are dropped at the API boundary, never
    # crash the read and never surface a malformed reference.
    assert oldest["citations"] == [{"type": "patient", "id": "p-x"}]


@pytest.mark.postgres
async def test_history_query_bounds_are_validation_errors(
    auth_client, make_user
) -> None:
    account = await make_user(roles=("doctor",))
    client = await auth_client(account)
    for query in ("?limit=0", "?limit=101", "?offset=-1", "?patient_id=not-a-uuid", "?q=unknown-filter"):
        response = await client.get(f"{HISTORY_PATH}{query}")
        assert response.status_code == 400, query
        assert response.json()["error"]["code"] == "validation_error", query


# --- clear + delete ---------------------------------------------------------


@pytest.mark.postgres
async def test_clear_history_is_204_and_audited(
    db_engine, auth_client, make_user, clinical_data
) -> None:
    account = await make_user(roles=("doctor",))
    bystander = await make_user(roles=("nurse",))
    mine = await seed_history(
        db_engine,
        [
            {"user_id": account.id, "patient_id": clinical_data.patient_id, "created_at": at(1)},
            {"user_id": account.id, "created_at": at(2)},
        ],
    )
    theirs = await seed_history(
        db_engine, [{"user_id": bystander.id, "patient_id": clinical_data.patient_id, "created_at": at(1)}]
    )
    client = await auth_client(account)

    response = await client.delete(HISTORY_PATH, headers=csrf_headers(client))
    assert response.status_code == 204
    assert response.content == b"" or response.text == ""

    page = (await client.get(HISTORY_PATH)).json()
    assert page["total"] == 0 and page["items"] == []
    # The other user's history is untouched.
    other_page = (await auth_client(bystander)).get(HISTORY_PATH)
    assert (await other_page).json()["total"] == 1
    assert len(mine) == 2 and len(theirs) == 1

    rows = await audit_rows(action="ai.history.clear", outcome="success")
    assert rows
    assert rows[-1].actor_user_id == account.id
    assert rows[-1].resource_type == "ai_conversation"
    assert rows[-1].resource_id is None
    blob = f"{rows[-1].action} {rows[-1].resource_type} {rows[-1].resource_id}"
    assert "Seeded question?" not in blob and "Seeded answer." not in blob


@pytest.mark.postgres
async def test_delete_own_row_only(db_engine, auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    stranger = await make_user(roles=("nurse",))
    mine = await seed_history(db_engine, [{"user_id": account.id, "created_at": at(1)}])
    theirs = await seed_history(db_engine, [{"user_id": stranger.id, "created_at": at(1)}])
    client = await auth_client(account)

    response = await client.delete(f"{HISTORY_PATH}/{mine[0]}", headers=csrf_headers(client))
    assert response.status_code == 204
    assert (await client.get(HISTORY_PATH)).json()["total"] == 0

    rows = await audit_rows(action="ai.history.delete", outcome="success")
    assert rows
    assert rows[-1].resource_id == str(mine[0])

    # A foreign row and an unknown id are both concealed as 404 and survive.
    unknown_id = uuid.uuid4()
    for row_id in (theirs[0], unknown_id):
        response = await client.delete(f"{HISTORY_PATH}/{row_id}", headers=csrf_headers(client))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    survivor = (await auth_client(stranger)).get(HISTORY_PATH)
    assert (await survivor).json()["total"] == 1
    denied = await audit_rows(action="ai.history.delete", outcome="denied")
    denied_ids = {row.resource_id for row in denied}
    assert str(theirs[0]) in denied_ids
    assert str(unknown_id) in denied_ids


@pytest.mark.postgres
async def test_non_get_requires_csrf(db_engine, auth_client, make_user) -> None:
    account = await make_user(roles=("doctor",))
    await seed_history(db_engine, [{"user_id": account.id, "created_at": at(1)}])
    client = await auth_client(account)

    response = await client.delete(HISTORY_PATH)  # csrf cookie exists, header missing
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "CSRF validation failed"
    assert (await client.get(HISTORY_PATH)).json()["total"] == 1


@pytest.mark.postgres
async def test_history_requires_authentication_and_patient_read(
    make_client, auth_client, make_user
) -> None:
    anonymous = await make_client()
    assert (await anonymous.get(HISTORY_PATH)).status_code == 401
    assert (await anonymous.delete(HISTORY_PATH, headers=csrf_headers(anonymous))).status_code == 401

    no_roles = await auth_client(await make_user(roles=()))
    for method in ("get", "delete"):
        response = await getattr(no_roles, method)(HISTORY_PATH, headers=csrf_headers(no_roles))
        assert response.status_code == 403, method
        assert response.json()["error"]["code"] == "forbidden"


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
def test_ai_history_migration_up_down_up(migrated_test_db: str, test_db_url: str) -> None:
    """Revision 9f1c3d5a7b24 upgrades, downgrades, and re-upgrades cleanly."""
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "admin.ai_conversation")) is True

    result = run_alembic("downgrade", "c7f2b4d9e013")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "admin.ai_conversation")) is False
    assert asyncio.run(_table_exists(test_db_url, "admin.staff_user")) is True

    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert asyncio.run(_table_exists(test_db_url, "admin.ai_conversation")) is True
