"""add per-visit blood pressure and basic vitals

Revision ID: b6c7d8e9f0a1
Revises: f4a5b6c7d8e9
Create Date: 2026-11-11

Additive nullable columns on `patients.encounter`. Weight/height already exist;
these extend the existing per-visit vitals snapshot for the Outcomes view and
synthetic longitudinal data. Existing rows remain valid and are backfilled by
the demo seed script.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "encounter",
        sa.Column("systolic_bp", sa.SmallInteger(), nullable=True),
        schema="patients",
    )
    op.add_column(
        "encounter",
        sa.Column("diastolic_bp", sa.SmallInteger(), nullable=True),
        schema="patients",
    )
    op.add_column(
        "encounter",
        sa.Column("heart_rate", sa.SmallInteger(), nullable=True),
        schema="patients",
    )
    op.add_column(
        "encounter",
        sa.Column("temperature_c", sa.Numeric(precision=4, scale=1), nullable=True),
        schema="patients",
    )
    op.add_column(
        "encounter",
        sa.Column("oxygen_saturation", sa.SmallInteger(), nullable=True),
        schema="patients",
    )
    op.create_check_constraint(
        "ck_encounter_systolic_bp",
        "encounter",
        "systolic_bp IS NULL OR (systolic_bp BETWEEN 60 AND 260)",
        schema="patients",
    )
    op.create_check_constraint(
        "ck_encounter_diastolic_bp",
        "encounter",
        "diastolic_bp IS NULL OR (diastolic_bp BETWEEN 30 AND 160)",
        schema="patients",
    )
    op.create_check_constraint(
        "ck_encounter_heart_rate",
        "encounter",
        "heart_rate IS NULL OR (heart_rate BETWEEN 20 AND 250)",
        schema="patients",
    )
    op.create_check_constraint(
        "ck_encounter_temperature",
        "encounter",
        "temperature_c IS NULL OR (temperature_c BETWEEN 30 AND 45)",
        schema="patients",
    )
    op.create_check_constraint(
        "ck_encounter_oxygen_saturation",
        "encounter",
        "oxygen_saturation IS NULL OR (oxygen_saturation BETWEEN 50 AND 100)",
        schema="patients",
    )


def downgrade() -> None:
    for name in (
        "ck_encounter_oxygen_saturation",
        "ck_encounter_temperature",
        "ck_encounter_heart_rate",
        "ck_encounter_diastolic_bp",
        "ck_encounter_systolic_bp",
    ):
        op.drop_constraint(name, "encounter", schema="patients")
    for name in (
        "oxygen_saturation",
        "temperature_c",
        "heart_rate",
        "diastolic_bp",
        "systolic_bp",
    ):
        op.drop_column("encounter", name, schema="patients")
