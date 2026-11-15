"""Shared patient-name normalization for search and voice suggestions.

One transliteration table and one normalization rule keep the literal SQL
search, the transliterated fallback search, and the deterministic voice
suggestions consistent. Pure functions only: no database access, no model
calls, no logging, and no patient data leaves the process.

The fallback is deliberately conservative: it only runs when the literal
name/MRN search found nothing, scans a bounded patient page, and requires a
high similarity score before returning a patient.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sht",
    "ъ": "a",
    "ь": "y",
    "ю": "yu",
    "я": "ya",
    "ѝ": "i",
}

# Minimum score for the transliterated fallback. Exact transliteration scores
# 0.95-1.0; a one-letter misspelling still scores above 0.86, while unrelated
# names stay well below it.
MIN_TRANSLITERATION_SCORE = 0.86
MIN_PARTIAL_TOKEN_LENGTH = 2


def transliterate(value: str) -> str:
    """Map Bulgarian Cyrillic characters to their Latin equivalents."""
    return "".join(CYRILLIC_TO_LATIN.get(char, char) for char in value)


def normalize_name(value: str) -> str:
    """Casefold, strip accents, transliterate, and collapse to name tokens."""
    decomposed = unicodedata.normalize("NFKD", str(value).casefold())
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    transliterated = transliterate(without_marks)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", transliterated).split())


def name_match_score(
    normalized_query: str, normalized_name: str, *, allow_fuzzy: bool = True
) -> float:
    """Score a normalized search query against a normalized patient name.

    Returns 0.0-1.0. Exact and whole-token containment matches rank highest.
    With ``allow_fuzzy`` (the human picker), partial first/family-name searches
    and one-letter misspellings still match. Without it (the AI resolver), only
    script-insensitive exact/containment matches qualify, so a similar but
    different person is never auto-selected.
    """
    if not normalized_query or not normalized_name:
        return 0.0
    if normalized_query == normalized_name:
        return 1.0
    query_padded = f" {normalized_query} "
    name_padded = f" {normalized_name} "
    if name_padded in query_padded:
        return 0.98
    if query_padded in name_padded:
        return 0.95
    if not allow_fuzzy:
        return 0.0

    query_tokens = normalized_query.split()
    name_tokens = normalized_name.split()
    matched = 0
    for query_token in query_tokens:
        if len(query_token) < MIN_PARTIAL_TOKEN_LENGTH:
            continue
        if any(
            name_token.startswith(query_token)
            or (len(name_token) >= 3 and query_token.startswith(name_token))
            for name_token in name_tokens
        ):
            matched += 1
    coverage = matched / len(query_tokens) if query_tokens else 0.0
    ratio = SequenceMatcher(None, normalized_query, normalized_name).ratio()
    if coverage == 1.0:
        return max(ratio, 0.97)
    return max(ratio, coverage * 0.85)
