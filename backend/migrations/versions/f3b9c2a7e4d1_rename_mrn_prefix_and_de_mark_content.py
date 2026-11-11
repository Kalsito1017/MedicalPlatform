"""rename patient MRN prefix to MRN- and de-mark seeded content

Revision ID: f3b9c2a7e4d1
Revises: d4b8f2c6a1e3
Create Date: 2026-11-02 09:00:00.000000

Product de-marking: patient MRNs change prefix ``DEMO-`` -> ``MRN-`` (e.g.
``DEMO-0024`` -> ``MRN-0024``) and the ``ck_patient_mrn_synthetic`` CHECK
follows (``mrn LIKE 'MRN-%'``; the provenance CHECK is unchanged). Existing
rows are rewritten in place; the mapping is injective so the ``uq_patient_mrn``
uniqueness guarantee is preserved. ``substring(mrn from 6)`` drops the five
characters ``DEMO-`` (PostgreSQL ``substring`` is 1-based) and the guard
``WHERE mrn LIKE 'DEMO-%'`` keeps already-renamed rows untouched, so the
upgrade is idempotent.

The same rewrite de-marks seeded TEXT content in place: the `` (демо)`` name
suffix, the ``Синтетично демо:`` summary wrapper, the ``Синтетична
демонстрационна бележка`` note wrapper, and the ``Измислени данни — не се
използват за медицинска практика.`` caveat sentence (the two benign
prompt-injection trigger texts keep their quoted trigger phrases - they are
security test data - and lose only the demo framing). Medication catalog codes
change ``DEMO-MED-%`` -> ``MED-%`` (injective, unique-constraint preserving).
No other schema changes.

UPGRADING AN EXISTING DEV/DEMO DATABASE: run this migration and nothing else.
A reseed is NOT required: the seed's natural keys derive from the stable
patient spec position (scripts/seed_demo_data.py ``demo_id``), not from the
MRN, so renamed MRNs never churn row ids; rows seeded before the rename carry
ids derived from the old MRN strings and the seed recognises both key forms
(legacy-key probe), so one post-migration seed run creates 0 rows.

The downgrade restores the identifier scheme exactly (MRN prefix, medication
codes, and the original CHECK expression). The seeded-content text clean-up is
deliberately one-way: re-adding display labels inside free text cannot
reconstruct the original strings exactly (e.g. the name suffix position inside
note bodies), and downgrade/upgrade cycles stay idempotent because the upgrade's
text replacements are no-ops on already-clean text.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'f3b9c2a7e4d1'
down_revision: Union[str, None] = 'd4b8f2c6a1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# One statement per text marker (plain replaces: exact, injective where it
# matters, and safe on already-clean rows because the patterns simply do not
# match). See the module docstring for the upgrade-path note.
_DE_MARK_STATEMENTS: tuple[str, ...] = (
    # display names: the visible '(демо)' marker suffix
    "UPDATE patients.patient SET display_name = replace(display_name, ' (демо)', '')",
    # encounter summaries: trigger-exercise wrappers first (they carry the
    # type word inside the wrapper), then the generic wrapper and caveat
    "UPDATE patients.encounter SET summary = replace(summary, 'Синтетично демо амбулаторен преглед:', 'Амбулаторен преглед:')",
    "UPDATE patients.encounter SET summary = replace(summary, 'Синтетично демо телемедицинска консултация:', 'Телемедицинска консултация:')",
    "UPDATE patients.encounter SET summary = replace(summary, 'Синтетично демо: ', '')",
    "UPDATE patients.encounter SET summary = replace(summary, 'дискусия на синтетичен план за лечение', 'дискусия на предложен план за лечение')",
    "UPDATE patients.encounter SET summary = replace(summary, 'сподели измислен разказ', 'сподели разказ')",
    "UPDATE patients.encounter SET summary = replace(summary, ' Измислени данни — не се използват за медицинска практика.', '')",
    # clinical note bodies: the synthetic-note wrapper, the name-suffix marker
    # inside the composed text, the trigger framing, and the caveat sentence
    "UPDATE patients.clinical_note SET body = replace(body, 'Синтетична демонстрационна бележка за ', 'Бележка за ')",
    "UPDATE patients.clinical_note SET body = replace(body, ' (демо)', '')",
    "UPDATE patients.clinical_note SET body = replace(body, 'разказа измислен разказ', 'сподели разказ')",
    "UPDATE patients.clinical_note SET body = replace(body, ' Измислени данни — не се използват за медицинска практика.', '')",
    # medication catalog codes (unique-constraint preserving: injective prefix)
    "UPDATE medications.medication SET code = replace(code, 'DEMO-MED-', 'MED-') WHERE code LIKE 'DEMO-MED-%'",
)


def upgrade() -> None:
    # Drop the old CHECK first: the MRN rewrite below would violate it.
    op.drop_constraint('ck_patient_mrn_synthetic', 'patient', schema='patients', type_='check')
    op.execute(
        "UPDATE patients.patient SET mrn = 'MRN-' || substring(mrn from 6) WHERE mrn LIKE 'DEMO-%'"
    )
    op.create_check_constraint(
        'ck_patient_mrn_synthetic', 'patient', "mrn LIKE 'MRN-%'", schema='patients'
    )
    for statement in _DE_MARK_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Identifier scheme restored exactly (constraint dropped first so the
    # reverse MRN rewrite cannot violate it). Seeded-content text clean-up is
    # one-way; see the module docstring.
    op.execute(
        "UPDATE medications.medication SET code = replace(code, 'MED-', 'DEMO-MED-') WHERE code LIKE 'MED-%'"
    )
    op.drop_constraint('ck_patient_mrn_synthetic', 'patient', schema='patients', type_='check')
    op.execute(
        "UPDATE patients.patient SET mrn = 'DEMO-' || substring(mrn from 5) WHERE mrn LIKE 'MRN-%'"
    )
    op.create_check_constraint(
        'ck_patient_mrn_synthetic', 'patient', "mrn LIKE 'DEMO-%'", schema='patients'
    )
