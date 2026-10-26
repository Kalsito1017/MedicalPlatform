"""Patient-access policy — ONE function, called by every clinical read.

Demo policy (docs/API.md, docs/SECURITY.md): any active principal holding
`patient:read` may read any SYNTHETIC patient in the demo dataset. There is no
patient-level restriction beyond the permission check.

Signature (binding design): ``check_patient_access(principal, patient_id)``.
Callers that already hold a database session pass it as ``db=`` to reuse the
transaction; otherwise the check opens a short-lived session of its own.

REPLACING THIS WITH A REAL POLICY (production seam):
a real deployment must restrict each principal to patients with whom they have
a care relationship (attending clinician, care-team membership, or an explicit
assignment/episode link). Replace the body of the check with a query against
that relationship table — e.g. "does a care_team row exist for
(principal.user_id, patient_id) or is the patient in the caller's assigned
ward?" — keeping the signature and the semantics below. Everything upstream
(API handlers, MCP tools, the LLM tool layer) calls only this function, so the
policy change lands in exactly one place. The concealment contract stays:
callers translate `False` into `404 not_found`, never `403`, so the existence
of a patient is never confirmed to a principal without access.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.policy import Principal, has_permission
from app.db.base import get_session_factory
from app.models.patient import Patient


async def check_patient_access(
    principal: Principal,
    patient_id: uuid.UUID | str,
    *,
    db: AsyncSession | None = None,
) -> bool:
    """True when `principal` may read this patient's record; False otherwise.

    Demo policy: `patient:read` grants access to every synthetic patient, but
    only while the patient row exists and is not soft-deleted — unknown,
    soft-deleted, and concealed ids are all indistinguishable to the caller
    (they surface as 404). See the module docstring for the production seam.
    """
    if principal is None or not principal.is_active:
        return False
    if not has_permission(principal.roles, "patient:read"):
        return False
    try:
        patient_uuid = patient_id if isinstance(patient_id, uuid.UUID) else uuid.UUID(str(patient_id))
    except ValueError:
        return False

    async def _row_exists(session: AsyncSession) -> bool:
        found = await session.scalar(
            select(Patient.id).where(Patient.id == patient_uuid, Patient.not_deleted())
        )
        return found is not None

    if db is not None:
        return await _row_exists(db)
    async with get_session_factory()() as session:
        return await _row_exists(session)
