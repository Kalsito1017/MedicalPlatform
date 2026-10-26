"""add user_session table

Revision ID: b7f3d2a91c04
Revises: 379adb815573
Create Date: 2026-10-26 15:10:00.000000

Server-side sessions (docs/SECURITY.md): the cookie carries a raw random token
and only its SHA-256 hash is stored here, so a database leak does not yield live
sessions. Expiry/revocation are enforced in the application layer.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7f3d2a91c04'
down_revision: Union[str, None] = '379adb815573'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_session',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['admin.staff_user.id'], name=op.f('fk_user_session_user_id_staff_user'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_session')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_user_session_token_hash')),
        schema='admin'
    )
    op.create_index('ix_user_session_user_id', 'user_session', ['user_id'], unique=False, schema='admin')


def downgrade() -> None:
    op.drop_index('ix_user_session_user_id', table_name='user_session', schema='admin')
    op.drop_table('user_session', schema='admin')
