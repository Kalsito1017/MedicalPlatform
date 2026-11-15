"""Deterministic “Did you mean?” patient suggestions for voice transcripts.

This is a read-only helper: it never writes, never calls an LLM, and never
returns patient ids or clinical data. It scans a bounded patient page, compares
candidate names with a small Bulgarian Cyrillic→Latin transliteration plus
`difflib.SequenceMatcher`, and recognizes spaced/hyphenated MRN forms such as
``M-R-N-0-0-0-1``.

The endpoint that uses this service re-checks `patient:read`; results are only
display names and MRNs that the caller can already read through the normal
patient search.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.services.patient_names import normalize_name as _normalize

PATIENT_SUGGESTION_SCAN_LIMIT = 1000
MAX_PATIENT_SUGGESTIONS = 3
MAX_NAME_CANDIDATES = 8
MIN_NAME_SCORE = 0.78
MAX_NAME_CANDIDATE_LENGTH = 80

_MRN_CANONICAL_RE = re.compile(r"(?i)\bmrn[\s\-–]*([a-z0-9]{3,20})")
_MRN_SPACED_RE = re.compile(
    r"(?i)\bm(?:\s*[-–]\s*)r(?:\s*[-–]\s*)n((?:\s*[-–]\s*[a-z0-9]){3,20})"
)
_NAME_RE = re.compile(
    r"(?<![\w'’-])([A-ZА-ЯЁ][a-zа-яё'’-]+(?:\s+[A-ZА-ЯЁ][a-zа-яё'’-]+){1,3})(?![\w'’-])"
)


@dataclass(frozen=True)
class PatientSuggestion:
    display_name: str
    mrn: str
    matched_name_text: str | None
    matched_mrn_text: str | None


@dataclass(frozen=True)
class _NameCandidate:
    raw: str
    start: int
    end: int


def _name_candidates(text: str) -> list[_NameCandidate]:
    seen: set[str] = set()
    candidates: list[_NameCandidate] = []
    for match in _NAME_RE.finditer(text):
        raw = " ".join(match.group(1).split())
        if len(raw) > MAX_NAME_CANDIDATE_LENGTH:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(_NameCandidate(raw=raw, start=match.start(1), end=match.end(1)))
        if len(candidates) >= MAX_NAME_CANDIDATES:
            break
    return candidates


def _canonical_mrn(raw: str) -> str | None:
    value = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    if not 3 <= len(value) <= 20:
        return None
    return f"MRN-{value}"


def _mrn_candidates(text: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern in (_MRN_CANONICAL_RE, _MRN_SPACED_RE):
        for match in pattern.finditer(text):
            canonical = _canonical_mrn(match.group(1))
            if canonical is None or canonical in seen:
                continue
            seen.add(canonical)
            candidates.append((match.group(0), canonical))
    return candidates


def _name_present(text: str, display_name: str) -> bool:
    needle = _normalize(display_name)
    if not needle:
        return False
    return f" {needle} " in f" {_normalize(text)} "


def _best_name(
    candidates: list[_NameCandidate], display_name: str
) -> tuple[_NameCandidate, float] | None:
    target = _normalize(display_name)
    if not target:
        return None
    best: tuple[_NameCandidate, float] | None = None
    for candidate in candidates:
        normalized = _normalize(candidate.raw)
        if not normalized:
            continue
        score = SequenceMatcher(None, normalized, target).ratio()
        if best is None or score > best[1]:
            best = (candidate, score)
    return best


async def suggest_patients(db: AsyncSession, *, text: str) -> list[PatientSuggestion]:
    """Return up to three bounded suggestions for likely patient references."""
    if len(text.strip()) < 3:
        return []

    name_candidates = _name_candidates(text)
    mrn_candidates = _mrn_candidates(text)
    if not name_candidates and not mrn_candidates:
        return []

    # Exact MRNs bypass the name-scan cap: a long patient table must not hide a
    # correct identifier. Names still use the bounded first-page scan.
    mrn_rows = []
    canonical_mrns = [canonical for _, canonical in mrn_candidates]
    if canonical_mrns:
        mrn_rows = list(
            (
                await db.execute(
                    select(Patient.display_name, Patient.mrn)
                    .where(
                        Patient.not_deleted(),
                        Patient.mrn.in_(canonical_mrns),
                    )
                    .limit(len(canonical_mrns))
                )
            ).all()
        )
    name_rows = list(
        (
            await db.execute(
                select(Patient.display_name, Patient.mrn)
                .where(Patient.not_deleted())
                .order_by(Patient.display_name, Patient.mrn)
                .limit(PATIENT_SUGGESTION_SCAN_LIMIT)
            )
        ).all()
    )
    if not name_rows and not mrn_rows:
        return []

    rows_by_mrn = {
        str(mrn).casefold(): (str(display_name), str(mrn))
        for display_name, mrn in [*mrn_rows, *name_rows]
    }
    rows = list(rows_by_mrn.values())
    folded_text = text.casefold()
    patient_by_mrn = dict(rows_by_mrn)

    ranked: list[tuple[int, float, PatientSuggestion]] = []
    seen_mrns: set[str] = set()

    # Explicit MRN references have the strongest signal. Even if the spoken
    # name is misspelled, the canonical name/MRN can be suggested immediately.
    for raw_mrn, canonical in mrn_candidates:
        patient = patient_by_mrn.get(canonical.casefold())
        if patient is None:
            continue
        display_name, mrn = patient
        canonical_present = canonical.casefold() in folded_text
        name_present = _name_present(text, display_name)
        if canonical_present and name_present:
            continue
        best = _best_name(name_candidates, display_name)
        matched_name = (
            best[0].raw
            if best is not None and best[1] >= MIN_NAME_SCORE and not name_present
            else None
        )
        suggestion = PatientSuggestion(
            display_name=display_name,
            mrn=mrn,
            matched_name_text=matched_name,
            matched_mrn_text=None if canonical_present else raw_mrn,
        )
        ranked.append((0, best[1] if best else 0.0, suggestion))
        seen_mrns.add(mrn.casefold())

    # Name-only similarity, still bounded to the first 1,000 active patients.
    for display_name, mrn in rows:
        key = str(mrn).casefold()
        if key in seen_mrns:
            continue
        if _name_present(text, display_name):
            continue
        best = _best_name(name_candidates, display_name)
        if best is None or best[1] < MIN_NAME_SCORE:
            continue
        ranked.append(
            (
                1,
                best[1],
                PatientSuggestion(
                    display_name=str(display_name),
                    mrn=str(mrn),
                    matched_name_text=best[0].raw,
                    matched_mrn_text=None,
                ),
            )
        )
        seen_mrns.add(key)

    ranked.sort(key=lambda item: (item[0], -item[1], item[2].mrn))
    return [item[2] for item in ranked[:MAX_PATIENT_SUGGESTIONS]]
