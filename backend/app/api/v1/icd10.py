"""ICD-10 catalogue search (authenticated, bounded, coding-only).

The catalogue is local and versioned; this endpoint never calls an external
classification service and never returns the complete catalogue in one
unbounded response. Search is limited to accounts that can write diagnoses
(`patient:write`), i.e. Doctor and Clinical Admin; Nurses remain read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import reject_unknown_query
from app.authorization.policy import Principal, require_permission
from app.schemas.icd10 import Icd10CatalogueOut, Icd10EntryOut, Icd10SearchOut
from app.services import icd10 as icd10_service

router = APIRouter(tags=["icd10"])


@router.get("/icd10/search", response_model=Icd10SearchOut)
async def search_icd10(
    q: str = Query(min_length=icd10_service.SEARCH_QUERY_MIN, max_length=icd10_service.SEARCH_QUERY_MAX),
    limit: int = Query(
        default=icd10_service.SEARCH_LIMIT_DEFAULT,
        ge=1,
        le=icd10_service.SEARCH_LIMIT_MAX,
    ),
    _unknown: None = Depends(reject_unknown_query("q", "limit")),
    principal: Principal = Depends(require_permission("patient:write")),
) -> Icd10SearchOut:
    """Search by ICD-10 code, Bulgarian name, or English name (bounded)."""
    entries = icd10_service.search_entries(q, limit=limit)
    metadata = icd10_service.catalogue_metadata()
    return Icd10SearchOut(
        items=[Icd10EntryOut.model_validate(entry) for entry in entries],
        catalogue=Icd10CatalogueOut(
            version=str(metadata.get("version") or "unknown"),
            source_organization=metadata.get("source_organization"),
            source_url=metadata.get("source_url"),
            retrieved_at=metadata.get("retrieved_at"),
            scope=metadata.get("scope"),
            license_note=metadata.get("license_note"),
        ),
    )
