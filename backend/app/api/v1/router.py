"""API v1 router.

Sub-routers are registered here as feature agents land their endpoints:
  - Phase 5 (auth):     api_v1.include_router(auth.router)
  - Phase 6 (clinical): api_v1.include_router(patients.router)
  - Phase 10 (admin):   api_v1.include_router(staff.router), audit.router
  - Phase 8 (LLM):      api_v1.include_router(ai.router)

All routes registered here are under the /api/v1 prefix and must enforce
authorization with app.authorization.policy.require_permission.
"""
from __future__ import annotations

from fastapi import APIRouter

api_v1 = APIRouter(prefix="/api/v1")
