"""Deterministic, fail-closed guardrails around the clinical LLM loop.

These checks are deliberately small and lexical. They do not replace Pydantic
request validation, MCP authorization, or tool-result size limits. They reject
only explicit attempts to leave the clinical assistant's scope, minimize the
record payload sent back to the model, and prevent unsafe model output from
reaching the response or conversation history.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any, Iterable, Literal

from app.llm.prompt import GENERAL_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.schemas.ai import QUESTION_MAX_CHARS

GuardrailCategory = Literal["input", "tool_scope", "output"]
GUARDRAIL_BLOCK_MESSAGE = "The request was blocked by the AI safety policy."

PATIENT_SCOPED_TOOLS = frozenset(
    {
        "get_patient_summary",
        "get_patient_lab_results",
        "get_recent_prescriptions",
    }
)


class GuardrailBlocked(Exception):
    """A guardrail rejected unsafe input, tool scope, or model output."""

    def __init__(self, category: GuardrailCategory) -> None:
        if category not in {"input", "tool_scope", "output"}:
            raise ValueError("invalid guardrail category")
        self.category = category
        # Never include the submitted question, tool arguments, or model answer.
        super().__init__(category)


def _detection_text(value: str) -> str:
    """Fold case, compatibility characters, separators, and hidden whitespace."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    characters: list[str] = []
    for character in folded:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf"} or character.isspace():
            characters.append(" ")
        elif character in {"_", "-", "–", "—"}:
            characters.append(" ")
        else:
            characters.append(character)
    return re.sub(r"\s+", " ", "".join(characters)).strip()


_REQUEST_RE = re.compile(
    r"\b(?:show|reveal|print|display|dump|give|tell|repeat|output|expose|leak|return|"
    r"share|provide|list|read|extract|disclose|run|execute|access|connect|use|open|"
    r"write|generate|bypass|ignore|disable|override|circumvent|evade|please)\b|"
    r"\b(?:can|could|would) you\b|\bi (?:need|want)\b|\bwhat (?:is|are)\b"
)
_PROMPT_TARGET_RE = re.compile(
    r"\b(?:(?:system|developer|hidden|initial|internal|private) "
    r"(?:prompt|prompts|instructions?|messages?)|your (?:prompt|instructions?|rules)|"
    r"(?:rules|instructions?) above)\b"
)
_SECRET_TARGET_RE = re.compile(
    r"\b(?:credentials?|passwords?|passwd|api keys?|access keys?|secret keys?|"
    r"private keys?|encryption keys?|session keys?|access tokens?|bearer tokens?|"
    r"refresh tokens?|client secrets?|dsn|connection strings?|database urls?|"
    r"environment variables?|\.env)\b"
)

# Each expression describes an explicit capability request, not a clinical term.
_FORBIDDEN_INPUT_RES = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:run|execute|write|generate|perform)\b.{0,60}\b"
        r"(?:sql|select|insert|update|delete|drop|alter|truncate)\b",
        r"\b(?:select|insert into|update|delete from|drop table|alter table|"
        r"truncate table|union select)\b.{0,100}\b(?:from|into|table|set|where)\b",
        r"\b(?:run|execute|open|spawn|access|use)\b.{0,60}\b"
        r"(?:shell|bash|terminal|command line|cmd|powershell|subprocess)\b",
        r"\b(?:read|list|show|dump|access|open|delete|write)\b.{0,60}"
        r"(?:/etc/|/var/|/root/|[a-z]:\\|\.env\b|file ?system|server files?|local files?)",
        r"\b(?:dump|access|connect|query|read)\b.{0,60}\b(?:raw )?(?:database|db)\b",
        r"\b(?:list|show|dump|read)\b.{0,50}\b(?:database tables?|db tables?|"
        r"database schemas?|db schemas?)\b",
        r"\b(?:bypass|ignore|disable|override|circumvent|evade|skip|work around)\b"
        r".{0,80}\b(?:authorization|authentication|permissions?|access controls?|"
        r"security|guardrails?|restrictions?|filters?)\b",
        r"\b(?:unauthorized|inaccessible|hidden)\b.{0,60}\b(?:patients?|records?)\b",
        r"\b(?:patients?|records?)\b.{0,80}\b(?:without (?:permission|authorization)|"
        r"not authorized|unauthorized|inaccessible|including hidden|outside (?:my )?"
        r"(?:scope|panel|care)|other doctors?|another doctor)\b",
        r"\b(?:all|every) patients? in (?:the )?(?:system|database|hospital)\b",
    )
)


def normalize_and_validate_input(question: str) -> str:
    """Normalize one AskRequest question and reject explicit unsafe requests.

    AskRequest already enforces 1..2000 characters and non-blank input. The
    repeated bound here protects direct loop callers and checks the normalized
    form as well. The returned string is safe to send as the untrusted question;
    no detection match or rejected value is ever included in an exception.
    """
    if not isinstance(question, str):
        raise GuardrailBlocked("input")
    normalized = unicodedata.normalize("NFKC", question).strip()
    if not normalized or len(normalized) > QUESTION_MAX_CHARS:
        raise GuardrailBlocked("input")

    detection = _detection_text(normalized)
    requested = _REQUEST_RE.search(detection) is not None
    if requested and (
        _PROMPT_TARGET_RE.search(detection) or _SECRET_TARGET_RE.search(detection)
    ):
        raise GuardrailBlocked("input")
    if any(pattern.search(detection) for pattern in _FORBIDDEN_INPUT_RES):
        raise GuardrailBlocked("input")
    return normalized


def _same_patient_id(value: Any, verified_patient_id: str) -> bool:
    if not isinstance(value, (str, uuid.UUID)):
        return False
    try:
        return str(uuid.UUID(str(value).strip())) == str(
            uuid.UUID(str(verified_patient_id).strip())
        )
    except (ValueError, AttributeError):
        return False


def _nested_patient_ids_match(value: Any, verified_patient_id: str) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() == "patient_id":
                if not _same_patient_id(item, verified_patient_id):
                    return False
            if not _nested_patient_ids_match(item, verified_patient_id):
                return False
    elif isinstance(value, list):
        return all(_nested_patient_ids_match(item, verified_patient_id) for item in value)
    return True


def enforce_tool_scope(
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    patient_id: str | None,
) -> None:
    """Reject out-of-scope model tool calls before MCP sees them.

    General lookup mode is unchanged. Patient-scoped mode has a closed set of
    three single-patient tools, and each call must contain the exact verified
    patient UUID. Recursive checking prevents a nested mismatch from being
    smuggled alongside a valid top-level argument.
    """
    if patient_id is None:
        return
    if tool_name not in PATIENT_SCOPED_TOOLS or not isinstance(arguments, dict):
        raise GuardrailBlocked("tool_scope")
    if "patient_id" not in arguments or not _same_patient_id(
        arguments["patient_id"], patient_id
    ):
        raise GuardrailBlocked("tool_scope")
    if not _nested_patient_ids_match(arguments, patient_id):
        raise GuardrailBlocked("tool_scope")


_ALWAYS_DROP_KEYS = frozenset({"mrn", "date_of_birth"})
# These links are not needed to interpret the flattened clinical tool results.
# Citation-bearing ids (`id`, `prescription_id`, and patient-row `patient_id`)
# are retained. In particular, `patient_id` is dropped only when the same row
# already has its own `id`; patient search/panel rows therefore keep their id.
_FOREIGN_KEYS_TO_DROP = frozenset(
    {
        "encounter_id",
        "medication_id",
        "practitioner_id",
        "staff_user_id",
        "user_id",
        "actor_user_id",
        "created_by",
        "updated_by",
    }
)


def minimize_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a minimized deep copy of an already size-capped MCP payload.

    This only removes fields, so existing MCP truncation markers and byte caps
    remain intact. It never mutates the MCP result or any REST-facing object.
    """

    def minimize(value: Any) -> Any:
        if isinstance(value, dict):
            minimized: dict[str, Any] = {}
            for key, item in value.items():
                normalized_key = key.casefold() if isinstance(key, str) else key
                if normalized_key in _ALWAYS_DROP_KEYS:
                    continue
                if normalized_key in _FOREIGN_KEYS_TO_DROP:
                    continue
                if normalized_key == "patient_id" and "id" in value:
                    continue
                minimized[key] = minimize(item)
            return minimized
        if isinstance(value, list):
            return [minimize(item) for item in value]
        return value

    return minimize(payload)


_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----", re.IGNORECASE)
_DSN_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|sqlite)://",
    re.IGNORECASE,
)
_EXTERNAL_URL_RE = re.compile(r"\b(?:https?|ftp)://|(?<![\w.])www\.", re.IGNORECASE)
_HTML_RE = re.compile(
    r"<\s*/?\s*[a-z][^>\n]{0,2000}>|<!doctype\b[^>\n]{0,200}>|"
    r"<!--[\s\S]{0,2000}-->",
    re.IGNORECASE,
)
_EVENT_HANDLER_RE = re.compile(
    r"\bon(?:abort|blur|change|click|error|focus|input|keydown|keypress|keyup|load|"
    r"mousedown|mouseenter|mouseleave|mousemove|mouseout|mouseover|mouseup|reset|"
    r"resize|scroll|select|submit|unload)\s*=",
    re.IGNORECASE,
)
_JAVASCRIPT_RE = re.compile(r"\bjavascript\s*:", re.IGNORECASE)
_SECRET_OUTPUT_RES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:api[ _-]?key|access[ _-]?token|auth[ _-]?token|refresh[ _-]?token|"
        r"client[ _-]?secret|secret[ _-]?key|password|passwd|credentials?)\b"
        r"\s*(?:=|:|\bis\b)\s*[^\s,;]{4,}",
        r"\bbearer\s+[a-z0-9._~+/=-]{8,}",
        r"\boc_sk_[a-z0-9_-]+\b",
        r"\bsk-[a-z0-9_-]{8,}\b",
        r"\bakia[a-z0-9]{12,}\b",
        r"\beyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\b",
        r"\b(?:dbname|database)\s*=\s*[^\s;]+[\s;]{1,20}"
        r"(?:[^\s;]+\s*=\s*[^\s;]+[\s;]{0,20}){0,5}"
        r"(?:password|pwd)\s*=",
    )
)
_INTERNAL_CONFIG_RE = re.compile(
    r"\b(?:llm|database|postgres|postgresql|redis|jwt|secret|app|mcp)_[a-z0-9_]+\b|"
    r"\b(?:app\.config|os\.environ|settings\.[a-z_]+|system_prompt|"
    r"general_system_prompt)\b",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})(?![0-9a-f])",
    re.IGNORECASE,
)
_SHORT_UUID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{8})-(?:…|\.{3})(?![\w.])", re.IGNORECASE)


def _words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def _prompt_shingles(size: int = 12) -> frozenset[tuple[str, ...]]:
    shingles: set[tuple[str, ...]] = set()
    for prompt in (SYSTEM_PROMPT, GENERAL_SYSTEM_PROMPT):
        words = _words(prompt)
        shingles.update(words[index : index + size] for index in range(len(words) - size + 1))
    return frozenset(shingles)


_PROMPT_SHINGLES = _prompt_shingles()


def _contains_prompt_fragment(answer: str) -> bool:
    words = _words(answer)
    size = 12
    return any(
        words[index : index + size] in _PROMPT_SHINGLES
        for index in range(len(words) - size + 1)
    )


def _authorized_citation_ids(citations: Iterable[dict[str, str]]) -> set[str]:
    authorized: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict) or "id" not in citation:
            continue
        try:
            authorized.add(str(uuid.UUID(str(citation["id"]))))
        except (ValueError, AttributeError):
            continue
    return authorized


def validate_output(answer: str, *, citations: Iterable[dict[str, str]]) -> None:
    """Fail closed on unsafe answer text or citations absent from tool results."""
    if not isinstance(answer, str):
        raise GuardrailBlocked("output")
    if (
        _PRIVATE_KEY_RE.search(answer)
        or _DSN_RE.search(answer)
        or _EXTERNAL_URL_RE.search(answer)
        or _HTML_RE.search(answer)
        or _EVENT_HANDLER_RE.search(answer)
        or _JAVASCRIPT_RE.search(answer)
        or _INTERNAL_CONFIG_RE.search(answer)
        or _contains_prompt_fragment(answer)
        or any(pattern.search(answer) for pattern in _SECRET_OUTPUT_RES)
    ):
        raise GuardrailBlocked("output")

    authorized = _authorized_citation_ids(citations)
    for match in _UUID_RE.finditer(answer):
        if str(uuid.UUID(match.group(1))) not in authorized:
            raise GuardrailBlocked("output")
    for match in _SHORT_UUID_RE.finditer(answer):
        prefix = match.group(1).casefold()
        if not any(citation_id.startswith(prefix) for citation_id in authorized):
            raise GuardrailBlocked("output")
