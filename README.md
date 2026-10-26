# Clinical AI Platform

A browser-based clinical AI demo: a vanilla HTML/CSS/JS frontend, a FastAPI backend with
role-based authorization and audit logging, a PostgreSQL database of **synthetic**
patient records, and an internal MCP server that lets an optional LLM answer questions
about those records through four narrow, read-only tools. It is built with
production-grade foundations (deny-by-default authorization, server-side sessions,
parameterized queries, audit events) as a publicly deployable demo.

> **Disclaimer — read this first:** This application runs on **synthetic demo data only**.
> It is **not clinically validated**, is **not a medical device**, and is **not for real
> patient data**. Nothing in it — including AI-generated output — is a diagnosis,
> treatment recommendation, or validated clinical decision. Real patient data requires a
> separate security, privacy, legal, clinical, and regulatory review (see
> `docs/SECURITY.md`).

**Live demo:** not yet deployed — see `docs/DEPLOYMENT.md`. The verified URL will be
posted here only after deployment succeeds (Phase 15). No URL is claimed until then.

**Status:** in active development. The Phase 1 deliverables (architecture, security, and
API-contract documentation) are written; implementation phases are landing per
`PLAN.md` (the lead maintains the phase tracker there). Commands marked below are
*(unverified until Phase 13)* — this README is written ahead of the code and will be
corrected against reality as phases land.

---

## Architecture (summary)

```text
Browser ──HTTPS──> Cloudflare Pages (frontend/, static, buildless)
   │
   └──HTTPS JSON API (/api/v1, cookie session + CSRF)──> Render (backend/, FastAPI)
                                                          │
                              ┌───────────────────────────┼──────────────────────┐
                              v                           v                      v (optional,
                     Neon (PostgreSQL)          internal MCP server       OpenCode Console
                     admin/patients/            (4 read-only tools,       inference API,
                     laboratory/medications     same authz layer)         server-side key)
```

Hard rules: the browser and the LLM never touch the database; the MCP server is
internal-only and gets no privileged credentials; acting identity always comes from the
verified server-side session; every patient-data operation goes through one shared
authorization layer. Full detail: **`docs/ARCHITECTURE.md`**. Permissions:
**`docs/SECURITY.md`**. API contract: **`docs/API.md`**.

---

## Local quickstart

Target: a new engineer running locally in under an hour. Prerequisites:

| Tool | Version | Notes |
|---|---|---|
| Docker + Docker Compose | recent | runs the local PostgreSQL service |
| `uv` | latest | manages the backend's Python environment |
| Python | 3.12 (via `uv`) | pinned for binary-wheel coverage; Docker image `python:3.12-slim` |
| Node | 24.x | only for frontend tooling/tests — the frontend itself is buildless |

Steps *(commands follow the planned layout: `backend/`, `scripts/seed_demo_data.py`,
`docker-compose.yml` with a `db` service; marked unverified until Phase 13)*:

```bash
# 1. Clone
git clone <repository-url> MedicalPlatform
cd MedicalPlatform

# 2. Configure environment (placeholder values — fill them in; never commit .env)
cp .env.example .env
# edit .env: set APP_SECRET_KEY, DATABASE_URL (match docker-compose db credentials),
# DEMO_* accounts, and leave LLM_ENABLED=false for now

# 3. Start the local database (named volume, persists across restarts)
docker compose up -d db          # (unverified until Phase 13)

# 4. Install backend dependencies
cd backend
uv sync                          # (unverified until Phase 13)

# 5. Create the schema
uv run alembic upgrade head      # (unverified until Phase 3/13)

# 6. Load synthetic demo data (idempotent/resettable)
uv run python ../scripts/seed_demo_data.py   # (unverified until Phase 3/13)

# 7. Run the API server (default http://localhost:8000)
uv run uvicorn app.main:app --reload         # (unverified until Phase 4/13)

# 8. Open the frontend
# See docs/ARCHITECTURE.md §8: the local frontend is served at http://localhost:8000
# (backend-served in dev) or http://localhost:5173 (static). Exact mechanism lands in
# Phase 9/13 and will be confirmed here. Both origins are pre-allowed in .env.example.
```

Health check: `curl http://localhost:8000/health/ready` should return 200 once the
database is reachable (`/health/live` is process liveness only).

Docker alternative: `docker compose up --build` runs the full local stack once
Phase 13 lands — exact services and commands will be confirmed here then.

---

## Demo accounts

Demo credentials are **never committed**. They are seeded from the `DEMO_*` variables in
your local `.env` at seed time (step 6) — you choose the passwords locally. On the
public demo, use the documented demo credentials provided at deployment time, and
remember a shared public account gives no real accountability.

| Account | Env variables | Role | Permissions (see `docs/SECURITY.md` for the full matrix) |
|---|---|---|---|
| Doctor | `DEMO_DOCTOR_EMAIL` / `DEMO_DOCTOR_PASSWORD` | `doctor` | `patient:read`, `lab:read`, `prescription:read` |
| Nurse | `DEMO_NURSE_EMAIL` / `DEMO_NURSE_PASSWORD` | `nurse` | `patient:read`, `lab:read`, `prescription:read`, `staff:read` |
| Clinical admin | `DEMO_ADMIN_EMAIL` / `DEMO_ADMIN_PASSWORD` | `clinical_admin` | `staff:read`, `staff:manage`, `role:assign`, `audit:read` |
| Tech admin | `DEMO_TECH_ADMIN_EMAIL` / `DEMO_TECH_ADMIN_PASSWORD` | `tech_admin` | `staff:read`, `staff:manage`, `role:assign`, `audit:read` |

Note the deliberate asymmetries: clinical roles have **no** staff/role/audit
administration; admin roles have **no** clinical-data access; technical administration
grants **no** clinical access; nobody can assign roles to themselves.

---

## Testing

Backend tests need PostgreSQL — start it first. The suite runs against the isolated
`clinical_demo_test` database and skips loudly if Postgres is unreachable:

```bash
docker compose up -d db
cd backend
uv sync
uv run pytest -q
uv run ruff check .
```

Frontend checks need Node 24 but no `npm install` — the frontend is buildless:

```bash
cd frontend
node tests/smoke.mjs
node tests/render-smoke.mjs
```

Repository security scan (stdlib-only Python, safe to run anywhere):

```bash
python3 scripts/security_check.py
```

The backend suite covers permission mapping, API auth/roles/validation/pagination,
migrations and seed behavior, MCP authorization and bounds, mocked LLM behavior,
escalation attempts, and audit events. The frontend checks cover static hygiene, module
import safety, api.js behaviour against a stubbed fetch, and render-path scenarios
against a DOM shim. The security scan checks tracked files for committed key material,
forbidden files (`.env`, keys, dumps), `.gitignore` coverage, frontend hygiene
(no external resources, web storage, or console logging), and database credentials —
it reports `file:line` and pattern names only, never secret values.

CI (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`,
in three jobs: `backend` (ruff + pytest against a `postgres:16` service container, and
the job fails if DB-backed tests were skipped), `frontend` (both Node check scripts),
and `security` (`scripts/security_check.py` plus a dependency audit of `backend/uv.lock`
via `pip-audit`, with advisory severity resolved through the OSV database). The
dependency audit **fails on HIGH/CRITICAL advisories** and warns on MODERATE/LOW; to
accept a specific advisory while a fix is prepared, set the repository variable
`SECURITY_AUDIT_ALLOW` to its comma-separated IDs and track a remediation issue, or set
`SECURITY_AUDIT_MODE=warn` to downgrade the gate temporarily. CI uses only mocks and
synthetic fixtures — no live LLM or external API calls — and references no secrets, so
pull requests from forks receive nothing sensitive.

---

## Repository map

```text
MedicalPlatform/
├── PLAN.md                  execution plan (phases 0–15) and progress log
├── README.md                this file
├── .env.example             environment template (placeholders only)
├── docker-compose.yml       local dev stack (Postgres)
├── frontend/                static HTML/CSS/JS (Cloudflare Pages)
├── backend/                 FastAPI app (auth, authorization, services, MCP, LLM)
│   ├── app/                 application code
│   ├── migrations/          Alembic
│   └── tests/               pytest suite
├── scripts/                 seed_demo_data.py, healthcheck.py
├── docs/
│   ├── API.md               frozen API contract (source of truth for endpoints)
│   ├── ARCHITECTURE.md      system design and decision log
│   ├── SECURITY.md          permissions, threats, audit, secrets, non-claims
│   ├── BACKLOG.md           deferred production requirements
│   ├── DEPLOYMENT.md        provider setup, verification, rollback
│   ├── TROUBLESHOOTING.md   failures and fixes
│   └── INITIAL_ASSESSMENT.md Phase 0 findings
└── .github/workflows/       CI
```

---

## Known limitations and caveats

- **Synthetic data only; not for clinical use.** Not clinically validated; not a medical
  device (see the disclaimer above).
- **Free-tier hosting, honestly:** Render free web services **sleep** (cold starts of
  roughly 30–60 s after idle); Neon free databases **autosuspend** (slow first query);
  quotas and limits apply; **$0/month is a baseline expectation, not a guarantee**. See
  `docs/DEPLOYMENT.md` §10.
- **AI is optional.** The app is fully functional with `LLM_ENABLED=false`. Known
  constraint: OpenCode free-tier models return HTTP 403 for non-OpenCode clients — a
  paid model or Go subscription is required to enable the AI feature
  (`docs/TROUBLESHOOTING.md` §9).
- **Not production-hardened:** no MFA, SSO, SCIM, backups/PITR, monitoring/on-call, or
  retention/erasure workflows yet. The full honest inventory — covered, deferred, and
  out of scope — is **`docs/BACKLOG.md`**.
- Simplified demo access policy (role-based, not patient-care-relationship-based).
- Single-tenant.

## Reporting issues and license

- **Issues:** use the repository's issue tracker. For security-sensitive reports, do not
  include exploit details or secrets publicly — a private reporting channel/contact is
  being set up (placeholder; see `docs/SECURITY.md` §8). Include the `X-Request-Id` from
  the failing response and follow the redaction list in `docs/TROUBLESHOOTING.md`.
- **License:** placeholder — no license has been chosen yet. Until one is added, no
  usage rights are granted beyond review. The lead will set this before public release.
