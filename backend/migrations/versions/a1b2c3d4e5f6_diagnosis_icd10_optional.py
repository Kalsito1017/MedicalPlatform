"""Make diagnosis ICD-10 code optional and format-checked.

The existing ``patients.diagnosis.code`` column already stores the
classification code. This migration reuses it as the ICD-10 (МКБ-10) code
instead of adding a duplicate column:

* allow NULL for an unclassified/pending diagnosis;
* reject malformed codes at the database boundary;
* keep existing diagnosis text and patient/encounter relationships untouched.

Membership in the configured ICD-10 catalogue is validated by the service/API,
not by this format check.

Revision ID: a1b2c3d4e5f6
Revises: d4e5f6a7b8c9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "diagnosis",
        "code",
        existing_type=sa.Text(),
        nullable=True,
        schema="patients",
    )
    op.execute("UPDATE patients.diagnosis SET code = NULL WHERE code = ''")
    op.create_check_constraint(
        "ck_diagnosis_code_format",
        "diagnosis",
        r"code IS NULL OR code ~ '^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$'",
        schema="patients",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_diagnosis_code_format",
        "diagnosis",
        schema="patients",
        type_="check",
    )
    op.execute("UPDATE patients.diagnosis SET code = '' WHERE code IS NULL")
    op.alter_column(
        "diagnosis",
        "code",
        existing_type=sa.Text(),
        nullable=False,
        schema="patients",
    )
