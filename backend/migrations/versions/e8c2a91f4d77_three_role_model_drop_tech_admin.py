"""three-role model: drop tech_admin (owner decision, 2026-10-26)

Revision ID: e8c2a91f4d77
Revises: b7f3d2a91c04
Create Date: 2026-10-26

The role model is now exactly `doctor`, `nurse`, `clinical_admin` (display
label "Admin"). `tech_admin` is deleted from the system entirely.

Inspection note: no role-key CHECK constraint or enum existed before this
revision — `admin.role.key` is a `varchar(50)` with a unique constraint and
`admin.user_role` carries no key enumeration — so there was nothing to
rewrite; a CHECK constraint allowing exactly the three role keys is added
here for database-level defense in depth (and dropped again on downgrade).

Upgrade:
  1. Staff users holding ONLY the `tech_admin` role are deactivated and
     soft-deleted (`is_active = false`, `deleted_at = now()`, matching the
     codebase's soft-delete pattern) and their live sessions are revoked.
     Rows are NEVER hard-deleted (audit-friendliness). Users holding
     `tech_admin` alongside another role simply lose the grant.
  2. `admin.user_role` grants for `tech_admin` are deleted, then the
     `admin.role` row.
  3. CHECK constraint `ck_role_key_valid` restricts `admin.role.key` to the
     three valid keys.

Downgrade:
  - Restores the `tech_admin` role row only (with the deterministic demo id
    used by the seed, so a later re-seed stays idempotent).
  - Role GRANTS are NOT restorable — they were deleted by the upgrade.
  - Accounts deactivated/soft-deleted by the upgrade are NOT reactivated and
    their sessions are NOT restored; only a new grant can make them usable
    `tech_admin` accounts again.
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e8c2a91f4d77'
down_revision: Union[str, None] = 'b7f3d2a91c04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TECH_ADMIN_KEY = 'tech_admin'
# Deterministic seed UUID for the role row (scripts/seed_demo_data.py
# demo_id("role", "tech_admin")); keeps re-seeding idempotent after a rollback.
TECH_ADMIN_ROLE_ID = uuid.UUID('743aba6f-82c5-5869-91d1-090a1e478f27')
TECH_ADMIN_ROLE_DESCRIPTION = 'Manages technical settings and user access; no clinical-data access.'

# Subselect: users holding the tech_admin role and no other role. Used before
# any grants are deleted (plain SQL, one bound constant: the role key).
ONLY_TECH_ADMIN_USERS = """
    SELECT ur.user_id
    FROM admin.user_role AS ur
    JOIN admin.role AS r ON r.id = ur.role_id
    WHERE r.key = :key
      AND NOT EXISTS (
          SELECT 1 FROM admin.user_role AS other
          JOIN admin.role AS ro ON ro.id = other.role_id
          WHERE other.user_id = ur.user_id AND ro.key <> :key
      )
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Deactivate + soft-delete the users that held ONLY tech_admin, and
    #    revoke their live sessions (deactivation is immediate in the app).
    bind.execute(
        sa.text(
            "UPDATE admin.staff_user SET is_active = false, deleted_at = now() "
            f"WHERE id IN ({ONLY_TECH_ADMIN_USERS}) AND deleted_at IS NULL"
        ),
        {"key": TECH_ADMIN_KEY},
    )
    bind.execute(
        sa.text(
            "UPDATE admin.user_session SET revoked_at = now() "
            f"WHERE user_id IN ({ONLY_TECH_ADMIN_USERS}) AND revoked_at IS NULL"
        ),
        {"key": TECH_ADMIN_KEY},
    )

    # 2. Delete the tech_admin grants, then the role row.
    bind.execute(
        sa.text(
            "DELETE FROM admin.user_role AS ur "
            "USING admin.role AS r "
            "WHERE ur.role_id = r.id AND r.key = :key"
        ),
        {"key": TECH_ADMIN_KEY},
    )
    bind.execute(
        sa.text("DELETE FROM admin.role WHERE key = :key"),
        {"key": TECH_ADMIN_KEY},
    )

    # 3. Database-level deny-by-default: only the three role keys may exist.
    op.create_check_constraint(
        'ck_role_key_valid',
        'role',
        "key IN ('doctor', 'nurse', 'clinical_admin')",
        schema='admin',
    )


def downgrade() -> None:
    # The CHECK constraint must go first: the restored key is not in it.
    op.drop_constraint('ck_role_key_valid', 'role', schema='admin', type_='check')

    # Restore the tech_admin role row ONLY. Grants are not restorable (they
    # were deleted by the upgrade) and deactivated/soft-deleted accounts are
    # deliberately not reactivated — see the module docstring.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO admin.role (id, key, description) "
            "VALUES (:id, :key, :description) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {
            "id": TECH_ADMIN_ROLE_ID,
            "key": TECH_ADMIN_KEY,
            "description": TECH_ADMIN_ROLE_DESCRIPTION,
        },
    )
