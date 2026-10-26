"""clinical data layer: diagnoses, notes, assignments, lab orders, Rx audit trail

Revision ID: c7f2b4d9e013
Revises: e8c2a91f4d77
Create Date: 2026-10-26

Schema target from design/Architecture.png (PostgreSQL box), adapted to the
codebase conventions. Upgrade:

  1. `patients.patient` gains `blood_type` (TEXT, CHECK-constrained to the
     eight ABO/Rh combinations plus 'unknown'; NULL = not recorded. Free text
     was rejected: blood group is a closed clinical vocabulary and AI lookups
     need unambiguous values).
  2. `patients.encounter` gains `practitioner_id` (FK -> admin.staff_user,
     nullable, ON DELETE SET NULL — staff attribution metadata keeps the visit
     when the account is removed) and a per-visit vitals snapshot
     `weight_kg` / `height_cm` (Numeric(5,2), never float) with range CHECKs.
  3. New tables (UUID PKs, UTC timestamptz, created_at/updated_at):
     - patients.diagnosis        (soft-deleted clinical record)
     - patients.clinical_note    (soft-deleted clinical record; one row per
       complete SOAP-style note with symptoms/assessment/proposed_plan columns)
     - patients.patient_assignment (link table, lifecycle via ended_at —
       mirrors admin.user_role: no soft-delete)
     - laboratory.test_catalog   (catalog, never soft-deleted)
     - laboratory.reference_range (catalog; FK-ish test_code -> test_catalog.code)
     - laboratory.lab_order      (soft-deleted clinical record)
     - laboratory.specimen       (soft-deleted clinical record)
     - medications.prescription_item (soft-deleted clinical record)
     - medications.prescription_history (immutable lifecycle event log —
       mirrors admin.audit_event: never soft-deleted)
     - medications.prescription_change (immutable field-level diff log)
     - admin.permission          (catalog, never soft-deleted)

Soft-delete rule (codebase convention): first-class clinical record rows of an
already soft-deleted class (patient/encounter/lab/prescription) carry
`deleted_at`; link rows, immutable event logs, and catalog/reference tables do
not. Clinical FKs are RESTRICT; staff attribution FKs are SET NULL.

Downgrade drops every new table and removes the added columns/constraints
cleanly. No data is migrated in either direction (the tables are new).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7f2b4d9e013'
down_revision: Union[str, None] = 'e8c2a91f4d77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. patients.patient: blood type (closed ABO/Rh vocabulary).
    op.add_column('patient', sa.Column('blood_type', sa.Text(), nullable=True), schema='patients')
    op.create_check_constraint(
        'ck_patient_blood_type',
        'patient',
        "blood_type IS NULL OR blood_type IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'unknown')",
        schema='patients',
    )

    # 2. patients.encounter: clinician attribution + per-visit vitals snapshot.
    op.add_column('encounter', sa.Column('practitioner_id', sa.Uuid(), nullable=True), schema='patients')
    op.add_column('encounter', sa.Column('weight_kg', sa.Numeric(precision=5, scale=2), nullable=True), schema='patients')
    op.add_column('encounter', sa.Column('height_cm', sa.Numeric(precision=5, scale=2), nullable=True), schema='patients')
    op.create_check_constraint(
        'ck_encounter_weight_range',
        'encounter',
        'weight_kg IS NULL OR (weight_kg > 0 AND weight_kg < 600)',
        schema='patients',
    )
    op.create_check_constraint(
        'ck_encounter_height_range',
        'encounter',
        'height_cm IS NULL OR (height_cm > 0 AND height_cm < 300)',
        schema='patients',
    )
    op.create_foreign_key(
        op.f('fk_encounter_practitioner_id_staff_user'),
        'encounter',
        'staff_user',
        ['practitioner_id'],
        ['id'],
        source_schema='patients',
        referent_schema='admin',
        ondelete='SET NULL',
    )

    # 3a. patients.diagnosis: problem-list entries (soft-deleted clinical record).
    op.create_table(
        'diagnosis',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('patient_id', sa.Uuid(), nullable=False),
        sa.Column('encounter_id', sa.Uuid(), nullable=True),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('onset_date', sa.Date(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'resolved', 'ruled_out')", name='ck_diagnosis_status'),
        sa.ForeignKeyConstraint(['encounter_id'], ['patients.encounter.id'], name=op.f('fk_diagnosis_encounter_id_encounter'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.patient.id'], name=op.f('fk_diagnosis_patient_id_patient'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_diagnosis')),
        schema='patients'
    )
    op.create_index('ix_diagnosis_patient_id', 'diagnosis', ['patient_id'], unique=False, schema='patients')

    # 3b. patients.clinical_note: one row per complete SOAP-style note.
    op.create_table(
        'clinical_note',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('patient_id', sa.Uuid(), nullable=False),
        sa.Column('encounter_id', sa.Uuid(), nullable=True),
        sa.Column('author_id', sa.Uuid(), nullable=True),
        sa.Column('note_type', sa.Text(), nullable=False),
        sa.Column('symptoms', sa.Text(), nullable=False),
        sa.Column('assessment', sa.Text(), nullable=False),
        sa.Column('proposed_plan', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "note_type IN ('progress_note', 'consult_note', 'telehealth_note', 'nurse_note')",
            name='ck_clinical_note_type',
        ),
        sa.ForeignKeyConstraint(['author_id'], ['admin.staff_user.id'], name=op.f('fk_clinical_note_author_id_staff_user'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['encounter_id'], ['patients.encounter.id'], name=op.f('fk_clinical_note_encounter_id_encounter'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.patient.id'], name=op.f('fk_clinical_note_patient_id_patient'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_note')),
        schema='patients'
    )
    op.create_index('ix_clinical_note_patient_id_created_at', 'clinical_note', ['patient_id', 'created_at'], unique=False, schema='patients')

    # 3c. patients.patient_assignment: care-team link rows (no soft-delete).
    op.create_table(
        'patient_assignment',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('patient_id', sa.Uuid(), nullable=False),
        sa.Column('staff_user_id', sa.Uuid(), nullable=False),
        sa.Column('care_role', sa.Text(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("care_role IN ('attending', 'covering')", name='ck_patient_assignment_care_role'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.patient.id'], name=op.f('fk_patient_assignment_patient_id_patient'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['staff_user_id'], ['admin.staff_user.id'], name=op.f('fk_patient_assignment_staff_user_id_staff_user'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_patient_assignment')),
        sa.UniqueConstraint('patient_id', 'staff_user_id', 'care_role', name='uq_patient_assignment_patient_staff_role'),
        schema='patients'
    )
    op.create_index('ix_patient_assignment_staff_user_id', 'patient_assignment', ['staff_user_id'], unique=False, schema='patients')

    # 3d. laboratory.test_catalog: catalog (no soft-delete).
    op.create_table(
        'test_catalog',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('unit', sa.Text(), nullable=True),
        sa.Column('ref_low', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('ref_high', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            'ref_low IS NULL OR ref_high IS NULL OR ref_high >= ref_low',
            name='ck_test_catalog_ref_range',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_test_catalog')),
        sa.UniqueConstraint('code', name=op.f('uq_test_catalog_code')),
        schema='laboratory'
    )

    # 3e. laboratory.reference_range: advisory demographic ranges (catalog).
    op.create_table(
        'reference_range',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('test_code', sa.Text(), nullable=False),
        sa.Column('sex', sa.Text(), nullable=True),
        sa.Column('age_min_years', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('age_max_years', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('ref_low', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('ref_high', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("sex IS NULL OR sex IN ('female', 'male', 'any')", name='ck_reference_range_sex'),
        sa.CheckConstraint(
            'age_min_years IS NULL OR age_max_years IS NULL OR age_max_years >= age_min_years',
            name='ck_reference_range_ages',
        ),
        sa.CheckConstraint('ref_high >= ref_low', name='ck_reference_range_ref_order'),
        sa.ForeignKeyConstraint(['test_code'], ['laboratory.test_catalog.code'], name=op.f('fk_reference_range_test_code_test_catalog'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_reference_range')),
        schema='laboratory'
    )
    op.create_index('ix_reference_range_test_code', 'reference_range', ['test_code'], unique=False, schema='laboratory')

    # 3f. laboratory.lab_order: one per ordering event (soft-deleted record).
    op.create_table(
        'lab_order',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('patient_id', sa.Uuid(), nullable=False),
        sa.Column('encounter_id', sa.Uuid(), nullable=True),
        sa.Column('ordering_practitioner_id', sa.Uuid(), nullable=True),
        sa.Column('ordered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('ordered', 'in_progress', 'completed', 'cancelled')",
            name='ck_lab_order_status',
        ),
        sa.ForeignKeyConstraint(['encounter_id'], ['patients.encounter.id'], name=op.f('fk_lab_order_encounter_id_encounter'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['ordering_practitioner_id'], ['admin.staff_user.id'], name=op.f('fk_lab_order_ordering_practitioner_id_staff_user'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.patient.id'], name=op.f('fk_lab_order_patient_id_patient'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_lab_order')),
        schema='laboratory'
    )
    op.create_index('ix_lab_order_patient_id_ordered_at', 'lab_order', ['patient_id', 'ordered_at'], unique=False, schema='laboratory')

    # 3g. laboratory.specimen: physical specimens (soft-deleted record).
    op.create_table(
        'specimen',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('lab_order_id', sa.Uuid(), nullable=False),
        sa.Column('specimen_type', sa.Text(), nullable=False),
        sa.Column('collected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "specimen_type IN ('blood', 'urine', 'swab', 'saliva', 'stool', 'tissue')",
            name='ck_specimen_type',
        ),
        sa.CheckConstraint(
            "status IN ('collected', 'received', 'in_lab', 'resulted', 'rejected')",
            name='ck_specimen_status',
        ),
        sa.CheckConstraint(
            'collected_at IS NULL OR received_at IS NULL OR received_at >= collected_at',
            name='ck_specimen_times',
        ),
        sa.ForeignKeyConstraint(['lab_order_id'], ['laboratory.lab_order.id'], name=op.f('fk_specimen_lab_order_id_lab_order'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_specimen')),
        schema='laboratory'
    )
    op.create_index('ix_specimen_lab_order_id', 'specimen', ['lab_order_id'], unique=False, schema='laboratory')

    # 3h. medications.prescription_item: line items (soft-deleted record).
    op.create_table(
        'prescription_item',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('prescription_id', sa.Uuid(), nullable=False),
        sa.Column('medication_id', sa.Uuid(), nullable=True),
        sa.Column('medication_name', sa.Text(), nullable=False),
        sa.Column('dose_amount', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('dose_unit', sa.Text(), nullable=False),
        sa.Column('sequence_no', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('sequence_no >= 1', name='ck_prescription_item_sequence_no'),
        sa.ForeignKeyConstraint(['medication_id'], ['medications.medication.id'], name=op.f('fk_prescription_item_medication_id_medication'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['prescription_id'], ['medications.prescription.id'], name=op.f('fk_prescription_item_prescription_id_prescription'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_prescription_item')),
        sa.UniqueConstraint('prescription_id', 'sequence_no', name='uq_prescription_item_prescription_sequence'),
        schema='medications'
    )
    op.create_index('ix_prescription_item_prescription_id', 'prescription_item', ['prescription_id'], unique=False, schema='medications')

    # 3i. medications.prescription_history: lifecycle event log (immutable).
    op.create_table(
        'prescription_history',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('prescription_id', sa.Uuid(), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('change_type', sa.Text(), nullable=False),
        sa.Column('from_value', sa.Text(), nullable=True),
        sa.Column('to_value', sa.Text(), nullable=True),
        sa.Column('changed_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "change_type IN ('created', 'status_changed', 'dose_changed', 'stopped', 'cancelled', 'completed', 'restarted')",
            name='ck_prescription_history_change_type',
        ),
        sa.ForeignKeyConstraint(['changed_by'], ['admin.staff_user.id'], name=op.f('fk_prescription_history_changed_by_staff_user'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['prescription_id'], ['medications.prescription.id'], name=op.f('fk_prescription_history_prescription_id_prescription'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_prescription_history')),
        schema='medications'
    )
    op.create_index('ix_prescription_history_prescription_id_changed_at', 'prescription_history', ['prescription_id', 'changed_at'], unique=False, schema='medications')

    # 3j. medications.prescription_change: field-level diff log (immutable).
    op.create_table(
        'prescription_change',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('prescription_id', sa.Uuid(), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('field', sa.Text(), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('changed_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['changed_by'], ['admin.staff_user.id'], name=op.f('fk_prescription_change_changed_by_staff_user'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['prescription_id'], ['medications.prescription.id'], name=op.f('fk_prescription_change_prescription_id_prescription'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_prescription_change')),
        schema='medications'
    )
    op.create_index('ix_prescription_change_prescription_id_changed_at', 'prescription_change', ['prescription_id', 'changed_at'], unique=False, schema='medications')

    # 3k. admin.permission: permission catalog (no soft-delete).
    op.create_table(
        'permission',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_permission')),
        sa.UniqueConstraint('key', name=op.f('uq_permission_key')),
        schema='admin'
    )


def downgrade() -> None:
    # Reverse dependency order; new tables first, then the added columns.
    op.drop_table('permission', schema='admin')

    op.drop_index('ix_prescription_change_prescription_id_changed_at', table_name='prescription_change', schema='medications')
    op.drop_table('prescription_change', schema='medications')
    op.drop_index('ix_prescription_history_prescription_id_changed_at', table_name='prescription_history', schema='medications')
    op.drop_table('prescription_history', schema='medications')
    op.drop_index('ix_prescription_item_prescription_id', table_name='prescription_item', schema='medications')
    op.drop_table('prescription_item', schema='medications')

    op.drop_index('ix_specimen_lab_order_id', table_name='specimen', schema='laboratory')
    op.drop_table('specimen', schema='laboratory')
    op.drop_index('ix_lab_order_patient_id_ordered_at', table_name='lab_order', schema='laboratory')
    op.drop_table('lab_order', schema='laboratory')
    op.drop_index('ix_reference_range_test_code', table_name='reference_range', schema='laboratory')
    op.drop_table('reference_range', schema='laboratory')
    op.drop_table('test_catalog', schema='laboratory')

    op.drop_index('ix_patient_assignment_staff_user_id', table_name='patient_assignment', schema='patients')
    op.drop_table('patient_assignment', schema='patients')
    op.drop_index('ix_clinical_note_patient_id_created_at', table_name='clinical_note', schema='patients')
    op.drop_table('clinical_note', schema='patients')
    op.drop_index('ix_diagnosis_patient_id', table_name='diagnosis', schema='patients')
    op.drop_table('diagnosis', schema='patients')

    op.drop_constraint('fk_encounter_practitioner_id_staff_user', 'encounter', schema='patients', type_='foreignkey')
    op.drop_constraint('ck_encounter_height_range', 'encounter', schema='patients', type_='check')
    op.drop_constraint('ck_encounter_weight_range', 'encounter', schema='patients', type_='check')
    op.drop_column('encounter', 'height_cm', schema='patients')
    op.drop_column('encounter', 'weight_kg', schema='patients')
    op.drop_column('encounter', 'practitioner_id', schema='patients')

    op.drop_constraint('ck_patient_blood_type', 'patient', schema='patients', type_='check')
    op.drop_column('patient', 'blood_type', schema='patients')
