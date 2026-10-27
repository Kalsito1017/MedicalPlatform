"""add idempotency_record table

Revision ID: d4b8f2c6a1e3
Revises: 9f1c3d5a7b24
Create Date: 2026-10-27 14:00:00.000000

Recorded first responses for `Idempotency-Key` requests (app.api.idempotency,
docs/API.md "Idempotent mutations"). One row per (user_id, endpoint, key); the
unique constraint is the concurrency arbiter. `response_body` stores the JSON
response text with secrets already redacted (e.g. `generated_password` is never
kept - one-time passwords are shown once and never persisted here), and
`request_hash` is only a SHA-256 payload fingerprint for
different-payload-same-key detection - the request payload itself is never
stored. Expired rows are pruned opportunistically (retention window is a
code-level constant, see app.api.idempotency.RETENTION).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4b8f2c6a1e3'
down_revision: Union[str, None] = '9f1c3d5a7b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'idempotency_record',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('endpoint', sa.String(length=64), nullable=False),
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('response_body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['admin.staff_user.id'], name=op.f('fk_idempotency_record_user_id_staff_user'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_idempotency_record')),
        sa.UniqueConstraint('user_id', 'endpoint', 'key', name=op.f('uq_idempotency_record_user_endpoint_key')),
        schema='admin'
    )
    op.create_index('ix_idempotency_record_expires_at', 'idempotency_record', ['expires_at'], unique=False, schema='admin')


def downgrade() -> None:
    op.drop_index('ix_idempotency_record_expires_at', table_name='idempotency_record', schema='admin')
    op.drop_table('idempotency_record', schema='admin')
