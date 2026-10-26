# Deployment

**Scope:** step-by-step deployment of the frontend (Cloudflare Pages), backend (Render free web service), database (Neon free PostgreSQL), and the optional LLM (OpenCode Console), plus verification, rollback, and reset procedures.
**Status:** living document, updated as phases land. Written at Phase 1 — this is the *procedure*, prepared ahead of Phase 14–15. **All `[ ]` items are unchecked because deployment has not happened yet.** URLs, project names, and account specifics are filled in at deployment time; nothing here is invented.

Legend: `[ ]` = not yet done/verified.

---

## 0. Providers at a glance

| Layer | Provider | Free-tier caveats |
|---|---|---|
| Frontend | Cloudflare Pages (static, HTTPS) | plan limits exist; free forever not guaranteed |
| Backend | Render free web service | sleeps after inactivity (cold starts ~30–60 s), monthly limits |
| Database | Neon free PostgreSQL | autosuspends after inactivity (first-query latency), limited backups on free tier |
| LLM (optional) | OpenCode Console inference API | free-tier models return **403 for non-OpenCode clients** — paid model or Go subscription required; otherwise pay-per-use with a budget cap |

Expected cost: **$0/month baseline** (free tiers) + optional LLM pay-per-use **with a
spend cap set in the OpenCode Console**. $0 is not guaranteed forever; see §9.

---

## 1. Prerequisites

- [ ] Accounts created: Cloudflare, Render, Neon, (optional) OpenCode Console.
- [ ] Repository pushed to a Git host that Cloudflare Pages and Render can pull from.
- [ ] All secrets generated fresh for the hosted environment (never reuse local dev values).
- [ ] Repository + git history reviewed: no secrets, no real patient data.

## 2. Database — Neon

1. [ ] Create a Neon project (choose a region close to the Render service).
2. [ ] Create the **least-privilege application role** (no superuser, no CREATEDB, rights
   limited to the app schemas/tables) and its database.
3. [ ] Optionally create a separate `migrator` role for migrations when elevated rights
   are needed (matches `MIGRATION_DATABASE_URL` in `.env.example`).
4. [ ] Copy the connection string; it must use TLS (`sslmode=require` — Neon enforces TLS
   by default). It becomes `DATABASE_URL` in Render (SQLAlchemy async form:
   `postgresql+asyncpg://…`).
5. [ ] Run migrations: `cd backend && uv run alembic upgrade head` *(unverified until
   Phase 3/13)* pointed at the hosted database.
6. [ ] Load the synthetic seed: `cd backend && uv run python ../scripts/seed_demo_data.py`
   *(unverified until Phase 3/13)* with the `DEMO_*` env vars set (see §5). Seed data is
   fictional and labelled `provenance: "synthetic"`.
7. [ ] Verify: seeded demo accounts can log in and synthetic patients are listed.

## 3. Backend — Render (free web service)

1. [ ] Create a Web Service from the repository. Root directory: `backend/`.
2. [ ] Environment: Python 3.12 (project is pinned to 3.12 via `uv`).
3. [ ] Build command (expected — confirm against `backend/pyproject.toml` when it lands):
   `uv sync` *(unverified until Phase 14)*.
4. [ ] Start command (expected): `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   *(unverified until Phase 14)*.
5. [ ] Set all environment variables from §5 in the Render dashboard (secret values are
   generated, never copied from this document).
6. [ ] Health check path: **`/health/ready`** (checks the database; the optional LLM does
   **not** gate readiness). `/health/live` exists for process liveness.
7. [ ] Deploy and confirm `/health/ready` returns 200 from the public URL.
8. [ ] Confirm the LLM key (if set) exists only here — not in the frontend, not in the repo.

## 4. Frontend — Cloudflare Pages

1. [ ] Create a Pages project connected to the repository. Output directory: `frontend/`
   (buildless — no build step; if a build command is required by the UI, use `echo ok`
   or the frontend agent's documented equivalent).
2. [ ] Configure the frontend's **API base URL** to the deployed backend origin. The
   canonical variable is `API_BASE_URL` (see `.env.example`); the exact injection
   mechanism (Pages build env vs. generated `config.js`) is finalized by the frontend
   agent in Phase 9/14 — **fill in the concrete step here at that time**.
3. [ ] Deploy and confirm the site loads over HTTPS with no mixed-content requests.
4. [ ] Custom domain: only if one is already owned and its cost is accepted; otherwise
   use the free `*.pages.dev` subdomain. Record the final URL here at deployment time:
   **Public URL: `<to be filled in Phase 15 — do not invent>`**.

## 5. Environment variables

Set in the Render dashboard (backend) unless noted. **Examples are placeholders — never
real values.**

| Name | Purpose | Where set | Example placeholder |
|---|---|---|---|
| `APP_ENV` | runtime mode (`development` / `test` / `production`) | Render | `production` |
| `APP_SECRET_KEY` | signing/secret material (32+ random bytes) | Render | `change-me-32-byte-random-string` |
| `PUBLIC_ORIGIN` | origin the browser loads the app from | Render | `https://<frontend-pages-domain>` |
| `API_BASE_URL` | backend base URL used by the frontend | Cloudflare Pages (and local `.env`) | `https://<backend-onrender-domain>` |
| `DATABASE_URL` | least-privilege app DB connection (TLS) | Render | `postgresql+asyncpg://app:change-me@host/db` |
| `MIGRATION_DATABASE_URL` | migration-only connection (elevated role) | local / CI, Render if migrated there | `postgresql+asyncpg://migrator:change-me@host/db` |
| `SESSION_COOKIE_NAME` | session cookie name | Render | `cap_session` |
| `SESSION_TTL_HOURS` | session lifetime | Render | `8` |
| `LLM_ENABLED` | AI feature on/off kill switch | Render | `false` |
| `LLM_API_BASE_URL` | OpenCode inference base URL | Render | `https://opencode.ai/inference` |
| `LLM_API_KEY` | service-account key ("Models: Request" only) | Render (secret) | `oc_sk_replace_me` |
| `LLM_MODEL` | default model | Render | `glm-5.3-flash` |
| `LLM_MAX_TOOL_CALLS` | tool-call bound per question | Render | `4` |
| `LLM_REQUEST_TIMEOUT_SECONDS` | LLM request timeout | Render | `45` |
| `ALLOWED_ORIGINS` | CORS allowlist (single frontend origin) | Render | `https://<frontend-pages-domain>` |
| `DEMO_ADMIN_EMAIL` / `DEMO_ADMIN_PASSWORD` | seeds the clinical_admin demo account | seed-time env only | `admin@example.com` / `replace-me-at-seed-time` |
| `DEMO_DOCTOR_EMAIL` / `DEMO_DOCTOR_PASSWORD` | seeds the doctor demo account | seed-time env only | `doctor@example.com` / `replace-me-at-seed-time` |
| `DEMO_NURSE_EMAIL` / `DEMO_NURSE_PASSWORD` | seeds the nurse demo account | seed-time env only | `nurse@example.com` / `replace-me-at-seed-time` |
| `DEMO_TECH_ADMIN_EMAIL` / `DEMO_TECH_ADMIN_PASSWORD` | seeds the tech_admin demo account | seed-time env only | `techadmin@example.com` / `replace-me-at-seed-time` |

Rules: never commit populated values; generate strong `APP_SECRET_KEY`, DB passwords,
and demo passwords per environment; `DEMO_*` values are set only when running the seed.

## 6. CORS, HTTPS, cookies, and the internal MCP boundary

- [ ] `ALLOWED_ORIGINS` contains **exactly one** origin: the deployed frontend
  (e.g. `https://<frontend-pages-domain>`). No wildcards, no extra localhosts in prod.
- [ ] HTTPS everywhere: Cloudflare Pages and Render both terminate TLS; all requests are
  HTTPS so the `Secure` cookie attribute is satisfied and there are no mixed-content
  warnings.
- [ ] Cookies: `cap_session` is `HttpOnly; Secure; SameSite=Lax; Path=/`; CSRF
  double-submit applies to all non-GET requests (`X-CSRF-Token` header).
- [ ] **MCP is internal-only.** There is no public MCP route or transport. It runs inside
  the backend process and is unreachable from the internet. Verify by attempting to
  reach any MCP-looking path from outside — expect 404.
- [ ] LLM is **optional**: the app must pass smoke tests with `LLM_ENABLED=false`. If
  enabled, set a **budget cap** in the OpenCode Console on the service-account key
  (restricted to "Models: Request"), and remember free-tier models return 403 for
  external clients — configure a paid model or accept that the AI feature stays off.

## 7. Verification checklist (mirrors PLAN.md Phase 15 smoke test)

Run against the **public deployed URL**, not localhost:

- [ ] 1. Homepage opens over HTTPS.
- [ ] 2. Login works with a documented synthetic demo account.
- [ ] 3. Correct role and permitted navigation are displayed.
- [ ] 4. Patient search finds a seeded synthetic patient.
- [ ] 5. Patient detail page opens.
- [ ] 6. Synthetic labs and prescriptions are readable.
- [ ] 7. AI summary works if configured; otherwise the graceful unavailable/demo state shows.
- [ ] 8. An unauthorized API operation is rejected by the backend (403/404), not just hidden.
- [ ] 9. Admin panel is denied for a non-admin (403 at the API).
- [ ] 10. Admin panel role management works for an authorized admin.
- [ ] 11. After logout, protected pages and API calls stop working (401).
- [ ] 12. Reload: data still available (persistence across app restart).
- [ ] 13. Network inspection: no DB credential, LLM key, or privileged secret in any response or asset.
- [ ] 14. Logs and audit events show the key operations (login, denials, record reads, role changes).
- [ ] 15. Visible synthetic-data / non-clinical-use notice is present.

Additional checks:

- [ ] `/health/ready` returns 200; `/health/live` returns 200.
- [ ] CORS: a request from any other origin is rejected.
- [ ] Database persistence verified across an app redeploy/restart.
- [ ] Cold-start and autosuspend behavior observed and recorded (durations).
- [ ] Incognito browser and narrow mobile viewport tested.
- [ ] Browser console and server logs free of errors and secrets.

## 8. Rollback procedure

Frontend (Cloudflare Pages): [ ] roll back to a previous deployment from the Pages
dashboard, or redeploy the last known-good commit.

Backend (Render): [ ] redeploy the previous commit (Render keeps prior images/releases —
use the dashboard rollback if available, otherwise `git revert` + redeploy).

Database: rollbacks are **not** automatic. Alembic supports `uv run alembic downgrade
<revision>`, but downgrades are only safe for reversible migrations and are tested only
locally. If a migration is destructive: restore from backup (see §10 limits) or rebuild
from migrations + synthetic seed. Because all data is synthetic, **rebuild is an
acceptable recovery path**: reset and reseed.

## 9. Database reset / demo-data refresh

Because every record is synthetic, a full reset is always safe:

1. [ ] Drop and recreate the app schemas (or recreate the Neon database/branch).
2. [ ] `cd backend && uv run alembic upgrade head`
3. [ ] `cd backend && uv run python ../scripts/seed_demo_data.py` with `DEMO_*` set.
4. [ ] Verify login + patient list.

The seed is idempotent or resettable by design (Phase 3 acceptance criterion); the exact
flags are documented by `scripts/seed_demo_data.py` when it lands. Resetting clears
audit history along with the data — expected for a demo.

## 10. Known free-tier risks and cost

| Risk | Impact | Mitigation |
|---|---|---|
| Render free tier sleeps | First request after idle takes ~30–60 s (looks like downtime) | Documented in README/TROUBLESHOOTING; health-check pings reduce surprise but are not a fix |
| Neon free tier autosuspends | First query after idle is slow | Same; connection retry logic in backend |
| Quotas/limits (requests, hours, bandwidth, storage) | Service throttling or stoppage at month end | Keep demo traffic light; monitor provider dashboards |
| LLM spend | Pay-per-use charges if enabled | Console budget cap; `LLM_ENABLED` kill switch; rate limit on the AI endpoint |
| Free-tier terms change | Features/pricing shift under the project | Provider knowledge isolated in env vars + this doc; see `docs/BACKLOG.md` B15 (IaC) |
| No PITR on free DB tier | Point-in-time recovery unavailable | Accept for synthetic demo; rebuild-from-seed is the recovery path; B10 tracks real backups |

Cost summary: **$0/month baseline** (all free tiers) + optional LLM pay-per-use bounded
by a budget cap. This is an expectation, not a guarantee.

## 11. Post-deployment record (fill in at Phase 15)

- Public URL: `<pending — do not invent>`
- Deployment commit: `<pending>`
- Services status (frontend / API / DB / MCP internal / LLM): `<pending>`
- Smoke test results: `<pending>`
- Observed cold-start / autosuspend timings: `<pending>`
