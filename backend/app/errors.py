"""Application exception hierarchy.

Every exception maps to the frozen error envelope from docs/API.md:
{"error": {"code", "message", "request_id", "details"}}. Handlers live in
app.main. Later agents raise these instead of returning ad-hoc error bodies.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for errors that are safe to surface to clients."""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class ValidationFailed(AppError):
    status_code = 400
    code = "validation_error"
    default_message = "Malformed input."


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    default_message = "Authentication is required."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission to perform this action."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    default_message = "The requested resource was not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    default_message = "The request conflicts with the current state of the resource."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Too many requests. Please retry later."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.retry_after = retry_after
        super().__init__(message, details=details)


class DependencyUnavailable(AppError):
    status_code = 503
    code = "dependency_unavailable"
    default_message = "A required dependency is temporarily unavailable."
