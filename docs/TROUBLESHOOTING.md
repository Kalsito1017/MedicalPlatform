# Troubleshooting

**Scope:** common failures in local development and in the deployed demo, with symptoms, likely causes, and fixes — plus how to gather diagnostics without leaking secrets.
**Status:** living document, updated as phases land. Written at Phase 1; entries are based on the designed behavior in `docs/API.md` and `.env.example`. Fix commands are marked *(unverified until Phase 13)* where the code that runs them does not exist yet.

---

## Quick triage

| Symptom | Jump to |
|---|---|
| Backend exits immediately at startup | [Missing environment variable](#1-missing-environment-variable-at-startup) |
| `alembic` errors | [Migrations fail](#2-migrations-fail) |
| Seed script creates duplicates | [Seed duplicates](#3-seed-duplicates) |
| Login fails with 401 or 403 | [Login 401/403 confusion](#4-login-401403-confusion) |
| Browser console: CORS / blocked response | [CORS errors](#5-cors-errors) |
| Requests seem unauthenticated despite logging in | [Cookie not sent](#6-cookie-not-sent) |
| Site "down" after being idle | [Cold start](#7-render-cold-start-looks-like-downtime) / [Autosuspend](#8-neon-autosuspend-latency) |
| AI feature returns 403 or 503 | [LLM 403](#9-llm-403-from-a-free-tier-model) / [LLM 503](#10-llm-503-when-disabled-or-unreachable) |
| `docker compose up` fails | [Port conflicts](#11-docker-compose-port-conflicts) |
| `uv sync` / import errors | [Python version mismatch](#12-uvpython-version-mismatch) |

---

## 1. Missing environment variable at startup

**Symptoms:** backend refuses to start; log line naming a missing/invalid setting (by
name, never by value); readiness never becomes healthy.

**Cause:** required env var unset or still a placeholder. Startup validation
(`backend/app/config.py`, Phase 2) fails fast on purpose.

**Fix:**
1. Compare your `.env` (local) or Render dashboard env (deployed) against `.env.example`.
2. Set every required variable. Placeholder values like `change-me-…` are detected and rejected.
3. Restart the service. Locally: `cd backend && uv run uvicorn app.main:app --reload`
   *(unverified until Phase 4)*.
4. Check `/health/ready` — it must return 200 once config and DB are OK.

Never paste real values into an issue or chat. Report the **variable name** only.

## 2. Migrations fail

**Symptoms:** `uv run alembic upgrade head` errors — version conflict, "relation already
exists", or connection/authentication failure.

**Fix:**
1. Connection problem? Verify `DATABASE_URL` / `MIGRATION_DATABASE_URL` (TLS required for
   Neon; the URL must be the `postgresql+asyncpg://` form). Confirm the Neon project is
   not suspended (first connection after idle can be slow — retry once).
2. "Already exists": the database was partially migrated. Inspect
   `alembic current` vs `alembic heads` *(unverified until Phase 3)*. For synthetic demo
   data the simplest fix is a **database reset** (drop schemas → `alembic upgrade head` →
   reseed; see `docs/DEPLOYMENT.md` §9).
3. Permission denied: migrations may need the `migrator` role
   (`MIGRATION_DATABASE_URL`), not the least-privilege app role.
4. Never run migrations as the database superuser.

## 3. Seed duplicates

**Symptoms:** running `scripts/seed_demo_data.py` twice creates duplicate patients/users.

**Fix:** the seed must be idempotent or support an explicit reset flag *(documented by
the script when it lands in Phase 3 — check `--help`)*. If you hit duplicates anyway:
reset the database (drop schemas → migrations → seed) per `docs/DEPLOYMENT.md` §9.
Report the non-idempotent behavior as a bug with the script's `--help` output.

## 4. Login 401/403 confusion

Know the difference (`docs/API.md`):

- **`401 unauthenticated`** — no session, or the session expired (`SESSION_TTL_HOURS`,
  default 8). Login again. A stale tab after logout/expire is the usual cause.
- **`403 forbidden`** — you are logged in but the role lacks the permission (see the
  matrix in `docs/SECURITY.md`), **or** the request failed CSRF validation (missing
  `X-CSRF-Token` on a non-GET request), **or** it is the self-escalation guard.
- Login endpoint itself returns a **uniform** failure for unknown email vs wrong
  password by design — that is not a bug. Rate limiting returns `429 rate_limited`;
  wait for `Retry-After` seconds.

**Fix:** call `GET /api/v1/auth/me` to see the current user's roles and effective
permissions. If roles look wrong, check `admin.user_role` (or ask an admin).

## 5. CORS errors

**Symptoms:** browser console: "blocked by CORS policy"; the API works via `curl` but not
from the UI.

**Fix:**
1. `ALLOWED_ORIGINS` on the backend must contain **exactly** the origin the browser is
   on — scheme + host + port must match (`https://<frontend-domain>` in production;
   `http://localhost:5173` or `http://localhost:8000` locally per `.env.example`).
2. After changing it, restart the backend (env changes need a restart).
3. No wildcards (`*`) in production; no trailing slash or path in origin values.
4. Preflight (`OPTIONS`) failures usually mean a custom header (e.g. `X-CSRF-Token`) is
   not in the allowed headers — the backend configures this; report it as a bug if
   preflight fails with the correct origin.

## 6. Cookie not sent

**Symptoms:** every request returns `401` immediately after a successful login; the
session cookie seems present in devtools on the login response but not on later requests.

**Causes and fixes:**
- **`Secure` attribute:** cookies are only sent over HTTPS (or `http://localhost`).
  If you serve the frontend over plain HTTP on a non-localhost host (e.g.
  `http://192.168.x.x:5173` from a phone), the cookie is dropped. Use HTTPS or
  `localhost` tunneling.
- **`SameSite=Lax`:** cross-site requests do not carry the cookie. If the frontend
  origin differs from the API origin *and* is treated as a different site (different
  registrable domain), the browser will not send it. Production is one frontend origin
  + one API origin over HTTPS; ensure `PUBLIC_ORIGIN` and `ALLOWED_ORIGINS` are set to
  the real frontend origin.
- **Origin mismatch:** logged in against `localhost` but browsing `127.0.0.1` (or vice
  versa) — different origins, cookie path/domain mismatch. Pick one hostname and stick
  to it.
- **CSRF:** the cookie may be sent fine but the missing `X-CSRF-Token` header on a
  non-GET request yields `403`. Check the request headers.

## 7. Render cold start looks like downtime

**Symptoms:** the site/API hangs for ~30–60 s after being idle, then works normally.
No error responses.

**Cause:** Render free web services sleep after inactivity. This is expected and
documented; it is not an outage.

**Fix/wait:** reload after the first slow request. Do not "fix" this by adding
complexity; if it matters, a paid tier or an external pinger (with the caveats noted in
`docs/BACKLOG.md`) is the answer. Record observed cold-start time in the deployment log.

## 8. Neon autosuspend latency

**Symptoms:** first request after idle is slow or times out; subsequent requests are
fast; `/health/ready` may briefly fail while the database wakes.

**Cause:** Neon free tier suspends the compute after inactivity.

**Fix:** retry once. For the readiness probe, allow a retry/warm-up window. If timeouts
persist, check the Neon dashboard for compute state and quota exhaustion.

## 9. LLM 403 from a free-tier model

**Symptoms:** `POST /api/v1/ai/ask` fails; logs show the upstream provider returned 403.

**Cause (known constraint):** OpenCode free-tier models return **HTTP 403 for
non-OpenCode clients**. A paid model or an OpenCode **Go subscription** is required for
the AI feature.

**Fix:** use a paid model in `LLM_MODEL` with a service-account key that has "Models:
Request" permission and a Console budget cap, or run with `LLM_ENABLED=false` — every
non-AI workflow is fully functional without the LLM. Never respond to a 403 by
embedding keys in the frontend or disabling TLS.

## 10. LLM 503 when disabled or unreachable

**Symptoms:** `POST /api/v1/ai/ask` returns `503 dependency_unavailable`; the UI shows
the "AI unavailable" state; everything else works.

**This is correct behavior**, not an outage: the LLM is an optional dependency and never
gates readiness (`/health/ready` stays 200). Check `LLM_ENABLED`, `LLM_API_BASE_URL`,
`LLM_API_KEY`, and provider status. The endpoint is also rate limited (`429`), bounded,
and time-limited by design.

## 11. docker compose port conflicts

**Symptoms:** `docker compose up` fails with "port is already allocated" (Postgres
default `5432`, or the app port `8000`/`5173`).

**Fix:**
1. Find the occupant: `ss -ltnp | grep 5432` (or the conflicting port).
2. Stop the conflicting local Postgres/service, or change the published port mapping in
   `docker-compose.yml` locally (e.g. `5433:5432`) and update `DATABASE_URL` to match.
3. Compose service name/db credentials must match `DATABASE_URL`; the canonical values
   live in `docker-compose.yml` + `.env.example` *(verify when Phase 2/13 lands)*.

## 12. uv/Python version mismatch

**Symptoms:** `uv sync` resolves oddly, native wheels build from source (slow/failing on
`asyncpg`, `greenlet`, `argon2-cffi`), or imports fail with syntax/version errors.

**Fix:**
1. The project targets **Python 3.12** (Docker image `python:3.12-slim`). Check
   `uv python list` and let `uv` manage 3.12: `uv python install 3.12`.
2. Re-sync: `cd backend && uv sync`.
3. Avoid system Python 3.11/3.13/3.14 mismatches; `uv` pins the interpreter per
   project.
4. In Docker, rebuild: `docker compose build --no-cache`.

---

## Getting diagnostics safely

- **Log locations:**
  - Local backend: terminal running `uv run uvicorn …` *(unverified until Phase 4)*.
  - Local containers: `docker compose logs -f backend db`.
  - Render: dashboard → service → Logs.
  - Frontend (Cloudflare Pages): dashboard → Pages → deployment logs (static hosting;
  the interesting logs are browser console + backend).
- **Request correlation:** every API response carries `X-Request-Id`; the same id
  appears in server logs and in `error.request_id` of the JSON error envelope. Include
  this id in bug reports — it is the fastest path to the matching log lines.
  When reporting, also include: timestamp (with timezone), endpoint + method, HTTP
  status, and the error `code` from the envelope.
- **What must be redacted before sharing anything:**
  - Passwords, password hashes, `DEMO_*` values.
  - Session cookies, CSRF tokens, `Authorization` headers.
  - LLM API keys (`oc_sk_…`) and database connection strings (usernames/hosts included).
  - Full clinical payloads — even synthetic ones, if logs are being posted publicly,
  share only the smallest relevant excerpt.
  - Environment variable **values**; names are fine.
- Structured logs are redacted by the application (Phase 11), but treat anything you
  export manually as sensitive and scrub it yourself before pasting.
