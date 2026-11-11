"""Central system-event registry.

Approved audit actions and operational event types only. Event payloads are
small flat dictionaries; no clinical text, request bodies, secrets, audio or
transcripts are accepted. Audit actions remain the durable source of truth;
operational events are a separate bounded stream.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

CATEGORIES = (
    "auth",
    "security",
    "patients",
    "encounters",
    "labs",
    "prescriptions",
    "appointments",
    "ai",
    "mcp",
    "voice",
    "administration",
    "system",
)
SEVERITIES = ("info", "success", "warning", "error", "security")

SAFE_METADATA_KEYS = frozenset(
    {
        "action",
        "language",
        "duration_ms",
        "audio_duration_seconds",
        "model",
        "provider",
        "status",
        "error_code",
        "tool",
        "mode",
        "tool_calls",
        "permission",
        "reason",
        "method",
        "route",
        "http_status",
        "count",
    }
)
MAX_METADATA_KEYS = 12
MAX_METADATA_STRING = 120


@dataclass(frozen=True)
class EventSpec:
    category: str
    severity: str
    description: str


AUDIT_EVENT_SPECS: dict[str, EventSpec] = {
    "auth.login.success": EventSpec("auth", "success", "User signed in"),
    "auth.login.failure": EventSpec("security", "security", "User sign-in failed"),
    "auth.logout": EventSpec("auth", "info", "User signed out"),
    "authz.denied": EventSpec("security", "security", "Authorization check denied"),
    "patient.read": EventSpec("patients", "info", "Patient record accessed"),
    "patient.summary.read": EventSpec("patients", "info", "Patient summary accessed"),
    "labs.read": EventSpec("labs", "info", "Laboratory records accessed"),
    "prescriptions.read": EventSpec("prescriptions", "info", "Prescription records accessed"),
    "patient.create": EventSpec("patients", "success", "Patient created"),
    "patient.delete": EventSpec("patients", "warning", "Patient removed"),
    "staff.create": EventSpec("administration", "success", "Staff account created"),
    "staff.update": EventSpec("administration", "info", "Staff account updated"),
    "staff.deactivate": EventSpec("administration", "warning", "Staff account deactivated"),
    "role.grant": EventSpec("administration", "success", "Role granted"),
    "role.revoke": EventSpec("administration", "warning", "Role revoked"),
    "session.revoke_all": EventSpec("administration", "warning", "All sessions revoked"),
    "analytics.read": EventSpec("administration", "info", "Analytics viewed"),
    "message.send": EventSpec("administration", "success", "Message sent"),
    "message.read": EventSpec("administration", "info", "Message read"),
    "ai.history.clear": EventSpec("ai", "info", "AI history cleared"),
    "ai.history.delete": EventSpec("ai", "info", "AI history entry deleted"),
    "appointment.create": EventSpec("appointments", "success", "Appointment created"),
    "appointment.update": EventSpec("appointments", "info", "Appointment updated"),
    "appointment.reschedule": EventSpec("appointments", "info", "Appointment rescheduled"),
    "appointment.cancel": EventSpec("appointments", "warning", "Appointment cancelled"),
    "appointment.denied": EventSpec("security", "security", "Appointment access denied"),
    "appointment.availability.update": EventSpec(
        "appointments", "info", "Doctor availability updated"
    ),
    "appointment.ai.propose": EventSpec(
        "appointments", "info", "AI appointment proposal generated"
    ),
    "appointment.remove_from_calendar": EventSpec(
        "appointments", "warning", "Cancelled appointment removed from calendar"
    ),
    "mcp.unknown_tool": EventSpec("security", "security", "Unknown MCP tool rejected"),
}

OPERATIONAL_EVENT_SPECS: dict[str, EventSpec] = {
    "ai.request.started": EventSpec("ai", "info", "AI request started"),
    "ai.request.completed": EventSpec("ai", "success", "AI request completed"),
    "ai.request.failed": EventSpec("ai", "error", "AI request failed"),
    "ai.request.timeout": EventSpec("ai", "error", "AI request timed out"),
    "ai.request.unavailable": EventSpec("ai", "error", "AI provider unavailable"),
    "ai.request.rate_limited": EventSpec("ai", "warning", "AI request rate limited"),
    "voice.transcription.started": EventSpec("voice", "info", "Transcription started"),
    "voice.transcription.completed": EventSpec("voice", "success", "Transcription completed"),
    "voice.transcription.failed": EventSpec("voice", "error", "Transcription failed"),
    "voice.transcription.unavailable": EventSpec("voice", "error", "Speech service unavailable"),
    "voice.transcription.validation_failed": EventSpec(
        "voice", "warning", "Audio validation failed"
    ),
    "voice.transcription.timeout": EventSpec("voice", "error", "Transcription timed out"),
    "voice.transcription.rate_limited": EventSpec("voice", "warning", "Transcription rate limited"),
    "appointment.conflict": EventSpec("appointments", "warning", "Appointment conflict detected"),
    "appointment.create_rejected": EventSpec(
        "appointments", "warning", "Appointment creation rejected"
    ),
    "system.startup": EventSpec("system", "info", "Application started"),
    "system.api_error": EventSpec("system", "error", "API request failed"),
    "system.rate_limited": EventSpec("system", "warning", "API request rate limited"),
    "system.dependency_unavailable": EventSpec(
        "system", "error", "Dependency unavailable"
    ),
}
ALL_EVENT_TYPES = frozenset(
    {*AUDIT_EVENT_SPECS, *OPERATIONAL_EVENT_SPECS, "ai.ask", "system.audit_event", "mcp.tool.call"}
)


def _audit_tool_name(action: str) -> str:
    return action[4:] if action.startswith("mcp.") and len(action) > 4 else "unknown"


def audit_event_spec(
    action: str, outcome: str, resource_type: str | None
) -> tuple[EventSpec, str, dict[str, Any]]:
    """Return (spec, approved event_type, safe metadata) for one audit row."""
    if action.startswith("mcp."):
        if action == "mcp.unknown_tool":
            spec = AUDIT_EVENT_SPECS[action]
            event_type = "mcp.unknown_tool"
            metadata = {"tool": _audit_tool_name(action)}
        else:
            event_type = "mcp.tool.call"
            metadata = {"tool": _audit_tool_name(action)}
            spec = EventSpec(
                "mcp",
                "security" if outcome == "denied" else ("error" if outcome == "error" else "success"),
                "MCP tool call " + ("denied" if outcome == "denied" else ("failed" if outcome == "error" else "completed")),
            )
        return spec, event_type, metadata

    if action == "ai.ask":
        spec = EventSpec(
            "ai",
            "security" if outcome == "denied" else ("error" if outcome == "error" else "success"),
            "AI assistant request " + ("denied" if outcome == "denied" else ("failed" if outcome == "error" else "completed")),
        )
        return spec, "ai.ask", {}

    known = AUDIT_EVENT_SPECS.get(action)
    if known is None:
        return EventSpec("system", "info", "Audit event"), "system.audit_event", {
            "action": _bounded_string(action)
        }
    spec = known
    if resource_type == "encounter" and action == "patient.read":
        spec = EventSpec("encounters", spec.severity, "Encounter accessed")
    if outcome == "denied":
        spec = EventSpec(spec.category, "security", spec.description)
    elif outcome == "error":
        spec = EventSpec(spec.category, "error", spec.description)
    return spec, action, {}


def _bounded_string(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return text[:MAX_METADATA_STRING]


def sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and bound flat operational metadata.

    Only allowlisted keys with scalar values survive. This is a deliberate
    allowlist: callers cannot smuggle clinical text, prompts, transcripts,
    bodies, secrets or nested structures into the log store.
    """
    if not isinstance(metadata, dict):
        return {}
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in SAFE_METADATA_KEYS:
            continue
        if value is None:
            result[key] = None
        elif isinstance(value, bool):
            result[key] = value
        elif isinstance(value, int):
            result[key] = value
        elif isinstance(value, float):
            if math.isfinite(value):
                result[key] = round(value, 3)
        elif isinstance(value, str):
            result[key] = value.strip()[:MAX_METADATA_STRING]
        if len(result) >= MAX_METADATA_KEYS:
            break
    return result


def operational_event_spec(event_type: str) -> EventSpec:
    spec = OPERATIONAL_EVENT_SPECS.get(event_type)
    if spec is None:
        raise ValueError(f"unapproved operational event type: {event_type!r}")
    return spec


def event_matches(
    event: dict[str, Any],
    *,
    severities: set[str] | None = None,
    categories: set[str] | None = None,
    event_type: str | None = None,
    actor_id: str | None = None,
    request_id: str | None = None,
    outcome: str | None = None,
    query: str | None = None,
) -> bool:
    """Apply the same approved filters used by the historical endpoint."""
    if severities and event.get("severity") not in severities:
        return False
    if categories and event.get("category") not in categories:
        return False
    if event_type and event.get("event_type") != event_type:
        return False
    if actor_id and str(event.get("actor_id") or "") != actor_id:
        return False
    if request_id and str(event.get("request_id") or "") != request_id:
        return False
    if outcome and str(event.get("outcome") or "") != outcome:
        return False
    if query:
        parts = [
            str(event.get(key) or "")
            for key in (
                "event_type",
                "resource_type",
                "resource_reference",
                "request_id",
            )
        ]
        # Operational history searches the approved flat metadata keys; audit
        # history searches the audit columns. Mirror that split on live events
        # so the same q filter cannot include different events live vs replay.
        if str(event.get("id", "")).startswith("op:"):
            metadata = event.get("metadata")
            if isinstance(metadata, dict):
                parts.extend(
                    str(value) for value in metadata.values() if value is not None
                )
        if query.lower() not in " ".join(parts).lower():
            return False
    return True
