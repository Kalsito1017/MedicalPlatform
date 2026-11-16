"""Versioned, local ICD-10 (МКБ-10) catalogue for diagnosis coding.

The catalogue is deliberately local: printing and coding never call NCPHA or
any external service. A small attributed demo subset ships in
``app/data/icd10_bg_seed.json``. A deployment that has obtained the necessary
permission may import the complete official NCPHA XLSX with
``scripts/import_icd10.py``; the generated ``app/data/icd10_bg.json`` is
gitignored and overrides/extends the seed entries.

The catalogue is not a clinical decision system. It validates that a code
exists in the configured classification and supplies its official Bulgarian
description. The doctor remains responsible for choosing and confirming the
diagnosis-code association.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_PATH = DATA_DIR / "icd10_bg_seed.json"
FULL_PATH = DATA_DIR / "icd10_bg.json"

# ICD-10: letter + two digits, optionally followed by a decimal point and 1-4
# alphanumeric characters. The exact stored code is preserved; the regex is a
# format gate, never proof of validity (membership in the catalogue is).
CODE_PATTERN = re.compile(r"^[A-Z][0-9]{2}(?:\.[0-9A-Z]{1,4})?$")
SEARCH_QUERY_MIN = 2
SEARCH_QUERY_MAX = 100
SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50

CodeStatus = Literal["confirmed", "missing", "review_required", "unknown"]


class Icd10CodeError(ValueError):
    """A submitted code is malformed, unknown, or not active."""


@dataclass(frozen=True)
class Icd10Entry:
    code: str
    name_bg: str
    name_en: str | None
    status: str
    level: str
    source: str


@dataclass(frozen=True)
class Icd10Catalogue:
    metadata: dict[str, Any]
    entries: dict[str, Icd10Entry] = field(default_factory=dict)

    @property
    def version(self) -> str:
        return str(self.metadata.get("version") or "unknown")

    def lookup(self, code: str | None) -> Icd10Entry | None:
        normalized = normalize_code(code)
        if not normalized:
            return None
        return self.entries.get(normalized)

    def describe(self, code: str | None) -> tuple[str | None, str | None, CodeStatus]:
        """Return (Bulgarian description, English description, status)."""
        normalized = normalize_code(code)
        if not normalized:
            return None, None, "missing"
        entry = self.entries.get(normalized)
        if entry is None:
            return None, None, "unknown"
        if entry.status != "active":
            return entry.name_bg, entry.name_en, "review_required"
        return entry.name_bg, entry.name_en, "confirmed"

    def search(self, query: str, *, limit: int = SEARCH_LIMIT_DEFAULT) -> list[Icd10Entry]:
        cleaned = " ".join(str(query or "").split())
        if len(cleaned) < SEARCH_QUERY_MIN:
            return []
        bounded = max(1, min(int(limit or SEARCH_LIMIT_DEFAULT), SEARCH_LIMIT_MAX))
        code_query = normalize_code(cleaned)
        text_query = cleaned.casefold()

        ranked: list[tuple[int, str, Icd10Entry]] = []
        for entry in self.entries.values():
            score: int | None = None
            if code_query and entry.code == code_query:
                score = 0
            elif code_query and CODE_PATTERN.match(code_query) and entry.code.startswith(code_query):
                score = 1
            elif entry.name_bg.casefold().startswith(text_query):
                score = 2
            elif text_query in entry.name_bg.casefold():
                score = 3
            elif entry.name_en and entry.name_en.casefold().startswith(text_query):
                score = 4
            elif entry.name_en and text_query in entry.name_en.casefold():
                score = 5
            if score is not None:
                ranked.append((score, entry.code, entry))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [entry for _, _, entry in ranked[:bounded]]

    def require_active(self, code: str | None) -> Icd10Entry:
        normalized = normalize_code(code)
        if not normalized or not CODE_PATTERN.match(normalized):
            raise Icd10CodeError("Malformed ICD-10 code.")
        entry = self.entries.get(normalized)
        if entry is None:
            raise Icd10CodeError("The ICD-10 code is not in the configured catalogue.")
        if entry.status != "active":
            raise Icd10CodeError(
                "The ICD-10 code is not active in the configured catalogue; choose an active code."
            )
        return entry

    def decorate(self, diagnosis: Any) -> Any:
        """Attach catalogue facts to a diagnosis ORM/read-model object."""
        code = getattr(diagnosis, "code", None)
        description_bg, description_en, status = self.describe(code)
        setattr(diagnosis, "code_description", description_bg)
        setattr(diagnosis, "code_description_en", description_en)
        setattr(diagnosis, "code_status", status)
        setattr(diagnosis, "code_version", self.version if status == "confirmed" else None)
        return diagnosis


def normalize_code(value: str | None) -> str | None:
    """Normalize user input without inventing decimal points or digits."""
    if value is None:
        return None
    cleaned = "".join(str(value).split()).upper()
    return cleaned or None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"ICD-10 catalogue is unreadable: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"ICD-10 catalogue is malformed: {path.name}")
    return payload


def _entries_from_payload(payload: dict[str, Any], *, source_file: str) -> dict[str, Icd10Entry]:
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError(f"ICD-10 catalogue has no entries list: {source_file}")
    entries: dict[str, Icd10Entry] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        code = normalize_code(raw.get("code"))
        name_bg = " ".join(str(raw.get("name_bg") or "").split())
        if not code or not CODE_PATTERN.match(code) or not name_bg:
            continue
        name_en = raw.get("name_en")
        name_en = " ".join(str(name_en).split()) if name_en else None
        status = str(raw.get("status") or "active").strip().lower()
        if status not in {"active", "superseded", "restricted", "withdrawn"}:
            status = "restricted"
        level = str(raw.get("level") or ("three" if "." not in code else "four"))
        entries[code] = Icd10Entry(
            code=code,
            name_bg=name_bg,
            name_en=name_en,
            status=status,
            level=level,
            source=str(raw.get("source") or source_file),
        )
    return entries


def load_catalogue(
    *,
    seed_path: Path = SEED_PATH,
    full_path: Path = FULL_PATH,
) -> Icd10Catalogue:
    """Load the seed subset and, when present, the locally imported full file.

    The full file is never required and is never committed. It overrides seed
    entries with the same code and supplies its own version metadata.
    """
    seed_payload = _load_json(seed_path)
    metadata = dict(seed_payload.get("metadata") or {})
    entries = _entries_from_payload(seed_payload, source_file=seed_path.name)

    if full_path.exists():
        full_payload = _load_json(full_path)
        full_metadata = full_payload.get("metadata")
        if isinstance(full_metadata, dict):
            metadata = {**metadata, **full_metadata}
        entries.update(_entries_from_payload(full_payload, source_file=full_path.name))

    return Icd10Catalogue(metadata=metadata, entries=entries)


@lru_cache(maxsize=1)
def get_catalogue() -> Icd10Catalogue:
    return load_catalogue()


def catalogue_metadata() -> dict[str, Any]:
    return dict(get_catalogue().metadata)


def search_entries(query: str, *, limit: int = SEARCH_LIMIT_DEFAULT) -> list[Icd10Entry]:
    return get_catalogue().search(query, limit=limit)


def require_active(code: str | None) -> Icd10Entry:
    return get_catalogue().require_active(code)


def decorate(diagnosis: Any) -> Any:
    return get_catalogue().decorate(diagnosis)


def decorate_many(diagnoses: Iterable[Any]) -> list[Any]:
    return [decorate(item) for item in diagnoses]
