# Initial Assessment (Phase 0)

**Date:** 2026-10-26
**Repository:** `/mnt/c/Users/Kaloyan/MedicalPlatform`
**Status at assessment:** Greenfield — no application code existed.

## What was found

| Item | Finding |
|---|---|
| Tracked files | None. Directory contained only `PLAN (1).md` (renamed to `PLAN.md`) and `production-requirements.txt`. |
| Existing code | No frontend, backend, database, MCP, or LLM code. Nothing to preserve or migrate. |
| Git | The parent directory `/mnt/c/Users/Kaloyan` was (and remains) a git repo with one commit and zero tracked files. **Decision:** this project gets its own repository inside `MedicalPlatform/`; the home-directory repo is left untouched. |
| Tests / lint / build | None existed. No baseline failures to record. |
| Secrets / patient-like data | None found. |

## Toolchain available

| Tool | Version | Notes |
|---|---|---|
| Python | 3.14.4 (system), 3.11.16 (uv-managed) | Project is pinned to **Python 3.12** via `uv` for binary-wheel coverage (`asyncpg`, `greenlet`, `argon2-cffi`); Docker image uses `python:3.12-slim`. Fallback: 3.11. |
| Node | v24.18.0 | Frontend is deliberately buildless vanilla JS (ES modules); Node is used only for tooling/tests (e.g. Playwright). |
| Docker | 29.1.3 + Compose v5.6.0 | Local dev environment (Phase 13). |
| `uv` | available at `/home/kaloyan/.local/bin/uv` | Dependency management for the backend. |
| `psql` | not installed | Local DB access goes through Docker or the app. |

## Current architecture (target)

See `docs/ARCHITECTURE.md`. Summary: static frontend (Cloudflare Pages) → FastAPI (Render) →
PostgreSQL (Neon) + optional LLM via OpenCode Console inference API. MCP server is an internal
module of the backend and is never publicly exposed.

## Baseline commands

| Command | Purpose | Result at baseline |
|---|---|---|
| `cd backend && uv sync` | Install backend deps | N/A — no code yet |
| `uv run pytest` | Backend tests | N/A — no code yet |
| `docker compose up --build` | Full local stack | N/A — no code yet |

These rows are updated as each phase lands.

## Environment variables

Canonical list lives in `.env.example` (placeholder values only). Missing required configuration
fails startup with a clear, non-secret error message.

## Scope decision (recorded from requirements discussion)

The build target is **PLAN.md's public synthetic-data demo, with production-grade foundations**.
Items from `production-requirements.txt` that are explicitly deferred (MFA, SSO/SAML, SCIM,
multi-tenancy, backups/PITR/DR, on-call, SOC 2, BAAs, retention/erasure workflows) are tracked in
`docs/BACKLOG.md`. Real patient data is **out of scope** and requires separate security, privacy,
legal, clinical, and regulatory review.
