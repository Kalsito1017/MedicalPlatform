"""add ai_conversation history table

Revision ID: 9f1c3d5a7b24
Revises: c7f2b4d9e013
Create Date: 2026-10-27 09:00:00.000000

Per-user private AI assistant history (docs/API.md "AI assistant"):
questions, AI-generated answers, and provenance scoped to one staff user.
User-scoped workflow data in `admin`, deliberately NOT an audit table (audit
keeps metadata-only rows) and deliberately NOT soft-deleted - users clear
their own history explicitly via the API. A production build with real
clinical content must add a retention/erasure policy first (docs/BACKLOG.md).
`patient_id` is a plain UUID (NULL = general lookup), not a foreign key, so
history survives clinical-record churn across schemas.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9f1c3d5a7b24'
down_revision: Union[str, None] = 'c7f2b4d9e013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_conversation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('patient_id', sa.Uuid(), nullable=True),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('used_tools', sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('citations', sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['admin.staff_user.id'], name=op.f('fk_ai_conversation_user_id_staff_user'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_conversation')),
        schema='admin'
    )
    op.create_index('ix_ai_conversation_user_id_created_at', 'ai_conversation', ['user_id', 'created_at'], unique=False, schema='admin')


def downgrade() -> None:
    op.drop_index('ix_ai_conversation_user_id_created_at', table_name='ai_conversation', schema='admin')
    op.drop_table('ai_conversation', schema='admin')
