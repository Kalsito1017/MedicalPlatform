"""ICD-10 catalogue search response schemas."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Icd10EntryOut(BaseModel):
    """One configured ICD-10 entry. `name_bg` is the official description."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name_bg: str
    name_en: str | None = None
    status: str
    level: str


class Icd10CatalogueOut(BaseModel):
    """Catalogue provenance shown with search results."""

    version: str
    source_organization: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    scope: str | None = None
    license_note: str | None = None


class Icd10SearchOut(BaseModel):
    items: list[Icd10EntryOut]
    catalogue: Icd10CatalogueOut
