"""`admin` schema models: staff users, roles, role grants, sessions, audit events.

Never stores plaintext passwords (argon2 hashes only), never stores raw session
tokens (SHA-256 hashes only), and never stores clinical payloads or secrets in
audit events.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin

SCHEMA = "admin"


class StaffUser(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "staff_user"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_staff_user_email_lower"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        # citext-like handling: store lowercase-normalized email.
        return value.strip().lower()


class Role(Base):
    __tablename__ = "role"
    __table_args__ = (
        # Deny by default at the database level too: only the three role keys
        # of the docs/API.md permission matrix may ever exist (owner decision
        # 2026-10-26: `tech_admin` was removed from the system entirely).
        CheckConstraint("key IN ('doctor', 'nurse', 'clinical_admin')", name="ck_role_key_valid"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class UserRole(Base):
    __tablename__ = "user_role"
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.staff_user.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.role.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.staff_user.id", ondelete="RESTRICT"),
        nullable=True,
    )


class UserSession(Base):
    """Server-side session record (docs/SECURITY.md "Sessions").

    The `cap_session` cookie carries a raw random secret token; only its SHA-256
    hash is stored here, so a database leak yields no usable session tokens.
    Expiry and revocation are enforced server-side: a session resolves only when
    `revoked_at IS NULL` and `expires_at` is in the future and its user is an
    active, non-deleted staff account.
    """

    __tablename__ = "user_session"
    __table_args__ = (
        Index("ix_user_session_user_id", "user_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.staff_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    # Client metadata kept for security auditing only; bounded, never payloads.
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AuditEvent(Base):
    """Immutable security-relevant event log.

    No clinical payloads and no secrets. `ip` is the direct client address when
    known; it is nullable and used for security auditing only.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_created_at", "created_at"),
        Index("ix_audit_event_actor_user_id", "actor_user_id"),
        Index("ix_audit_event_action", "action"),
        CheckConstraint("outcome IN ('success', 'denied', 'error')", name="ck_audit_event_outcome"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.staff_user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Permission(TimestampMixin, Base):
    """Permission catalog row (reference data — never soft-deleted).

    The authoritative permission set lives in `app.authorization.policy`; this
    table mirrors its keys (seeded literally, never imported across layers) so
    the database can join/audit permission keys. Unlike `admin.role.key` there
    is deliberately NO key enumeration CHECK: permission keys are expected to
    grow with new features and only need uniqueness.
    """

    __tablename__ = "permission"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
