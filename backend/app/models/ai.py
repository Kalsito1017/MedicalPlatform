"""`admin` schema model: per-user AI assistant conversation history.

Privacy position: `ai_conversation` is a PER-USER PRIVATE history of
AI-assistant conversations (questions, AI-generated answers, provenance),
scoped to its owner (`user_id`) and readable only by that owner through the
API. It stores synthetic content only. It is deliberately NOT an audit
table: `admin.audit_event` keeps recording only `ai.ask`/`mcp.*` metadata as
before and never payloads. There is deliberately NO soft delete and no
retention job yet; a production build with real clinical content must add a
retention/erasure policy first (docs/BACKLOG.md).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SCHEMA = "admin"


class AiConversation(Base):
    """One persisted question/answer exchange owned by one staff user.

    `patient_id` is NULL for general-lookup questions and carries the synthetic
    patient id for patient-scoped questions. It is intentionally NOT a foreign
    key: history survives record churn and lives in the `admin` (workflow)
    schema, not the clinical schemas. `used_tools` and `citations` are
    JSON-encoded lists in TEXT columns (decoded at the API boundary).
    """

    __tablename__ = "ai_conversation"
    __table_args__ = (
        Index("ix_ai_conversation_user_id_created_at", "user_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.staff_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, default=None)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    used_tools: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    citations: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
