"""Per-user AI conversation history (docs/API.md "AI assistant" history).

Read/write side for `admin.ai_conversation`: the caller's own question/answer
exchanges with their provenance (`used_tools`, `citations`, `model`). Every
query is scoped to exactly one `user_id` - the API passes the session
principal's id and this layer never offers an unscoped read, so one user can
never see another user's history. This is private workflow data, not audit
data: audit keeps metadata-only `ai.ask` rows (app.audit.logger) and history
content is never audited or logged.

Writes do NOT commit here - the caller commits once per request lifecycle
(the ask endpoint persists the exchange with its audit event in a single
commit, so a retried request can never double-write within one lifecycle).
`used_tools` and `citations` are JSON-encoded lists in TEXT columns; decode
failures degrade to empty lists, never to a failed read.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AiConversation


def encode_list(values: list[Any]) -> str:
    """JSON-encode a bounded list of provenance entries for TEXT storage."""
    return json.dumps([_jsonable(value) for value in values], separators=(",", ":"), ensure_ascii=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def decode_list(raw: str | None) -> list[Any]:
    """Decode a stored provenance list; malformed rows read as empty lists."""
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except ValueError:
        return []
    return decoded if isinstance(decoded, list) else []


def decode_used_tools(row: AiConversation) -> list[str]:
    return [str(tool) for tool in decode_list(row.used_tools)]


def decode_citations(row: AiConversation) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for entry in decode_list(row.citations):
        if isinstance(entry, dict):
            entry_type = entry.get("type")
            entry_id = entry.get("id")
            if isinstance(entry_type, str) and isinstance(entry_id, str):
                citations.append({"type": entry_type, "id": entry_id})
    return citations


async def save_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    patient_id: uuid.UUID | None,
    question: str,
    answer: str,
    used_tools: list[str],
    citations: list[dict[str, str]],
    model: str,
) -> AiConversation:
    """Stage one exchange in the caller's history (the caller commits)."""
    row = AiConversation(
        user_id=user_id,
        patient_id=patient_id,
        question=question,
        answer=answer,
        used_tools=encode_list(used_tools),
        citations=encode_list(citations),
        model=model,
    )
    db.add(row)
    await db.flush()
    return row


async def list_conversations(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int,
    offset: int,
    patient_id: uuid.UUID | None = None,
) -> tuple[list[AiConversation], int]:
    """Newest-first history page for ONE user (never anyone else's rows)."""
    filters = [AiConversation.user_id == user_id]
    if patient_id is not None:
        filters.append(AiConversation.patient_id == patient_id)

    count_stmt = select(func.count()).select_from(AiConversation).where(*filters)
    total = (await db.scalar(count_stmt)) or 0

    rows = (
        await db.execute(
            select(AiConversation)
            .where(*filters)
            .order_by(AiConversation.created_at.desc(), AiConversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows), int(total)


async def get_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> AiConversation | None:
    """One exchange, only when it belongs to `user_id` (foreign rows are None)."""
    return await db.scalar(
        select(AiConversation).where(
            AiConversation.id == conversation_id,
            AiConversation.user_id == user_id,
        )
    )


async def delete_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> bool:
    """Delete one OWNED exchange (the caller commits). False when not owned."""
    row = await get_conversation(db, user_id=user_id, conversation_id=conversation_id)
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True


async def clear_conversations(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Delete every exchange of ONE user (the caller commits). Returns the count."""
    count = (await db.scalar(select(func.count()).select_from(AiConversation).where(AiConversation.user_id == user_id))) or 0
    await db.execute(delete(AiConversation).where(AiConversation.user_id == user_id))
    await db.flush()
    return int(count)
