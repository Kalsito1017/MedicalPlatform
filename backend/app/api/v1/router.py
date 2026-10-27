"""API v1 router.

Sub-routers registered here:
  - Phase 5 (auth):     auth.router (login, logout, me)
  - Phase 6 (clinical): patients.router (patients, summary, labs,
                        prescriptions, encounters)
  - Phase 5/10 (admin): staff.router, audit.router
  - Phase 8 (LLM):      ai.router (POST /ai/ask)
  - Wave U (analytics): analytics.router (GET /analytics/summary, Admin only)

All routes registered here are under the /api/v1 prefix and must enforce
authorization with app.authorization.policy.require_permission (deny by
default). Non-GET routes are CSRF-protected by app.auth.csrf.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import ai, analytics, audit, auth, patients, staff

api_v1 = APIRouter(prefix="/api/v1")

api_v1.include_router(auth.router)
api_v1.include_router(patients.router)
api_v1.include_router(staff.router)
api_v1.include_router(audit.router)
api_v1.include_router(ai.router)
api_v1.include_router(analytics.router)
