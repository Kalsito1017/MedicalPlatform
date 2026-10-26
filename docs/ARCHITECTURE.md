# Architecture

**Scope:** system context, trust boundaries, data flows, schema overview, component responsibilities, deployment topology, and the decision log for the Clinical AI Platform.
**Status:** living document, updated as phases land. Written at Phase 1; the schema and component sections describe the target structure from `PLAN.md` and are annotated where implementation is still in progress. Source of truth for API shape is `docs/API.md`; for permissions see `docs/SECURITY.md`.

---

## 1. System context

This is a browser-based clinical AI application running on **synthetic demo data only**. Five logical components:

```text
                    Browser (desktop / mobile)
                        |
                        |  HTTPS (static assets)
                        v
        +---------------------------------------------+
        |  Cloudflare Pages                           |
        |  frontend/  — vanilla HTML/CSS/JS, buildless |
        +---------------------------------------------+
                        |
                        |  HTTPS JSON API  (/api/v1)
                        |  cookie session + CSRF token
                        v
        +---------------------------------------------+
        |  Render (free web service)                  |
        |  backend/  — FastAPI (Python 3.12)          |
        |                                             |
        |  app/auth           sessions, argon2id, CSRF|
        |  app/authorization  policy.py  <-- THE      |
        |                         single permission   |
        |                         matrix              |
        |  app/services       patient / lab / med /   |
        |                     staff business logic    |
        |  app/audit          audit events            |
        |  app/api            REST endpoints          |
        |  app/mcp            MCP server (INTERNAL    |
        |                     module, 4 read-only     |
        |                     tools)                  |
        |  app/llm            provider abstraction    |
        +---------------------------------------------+
             |                        |            |
             | TLS                    | TLS        | HTTPS (outbound,
             v                        v            | server-side key)
        +-------------+        +-----------+       v
        | Neon (free  |        | Neon same |   +----------------------+
        | PostgreSQL) |        | database  |   | OpenCode Console     |
        | admin       |        | (read via |   | inference API        |
        | patients    |        |  the same |   | (OPTIONAL — app fully|
        | laboratory  |        |  services)|   | functional without)  |
        | medications |        +-----------+   +----------------------+
        +-------------+              ^
                                      |
                         MCP tools call services;
                         they NEVER connect to the
                         database directly with
                         privileged credentials
```

Component summary:

| Component | Lives in | Runs on | Public? |
|---|---|---|---|
| Frontend (static) | `frontend/` | Cloudflare Pages (HTTPS) | Yes |
| Backend API (FastAPI) | `backend/` | Render free web service | Yes (API only) |
| MCP server | `backend/app/mcp/` | Same process as FastAPI | **No — internal only** |
| PostgreSQL | `backend/migrations/`, `scripts/seed_demo_data.py` | Neon free tier (TLS) | No |
| LLM (optional) | `backend/app/llm/` | OpenCode Console inference API | No (outbound call only) |

---

## 2. Trust boundaries and hard rules

Trust boundaries:

1. **Browser → Backend.** Everything arriving from the browser is untrusted input. The session cookie proves *who* the caller is only after server-side session lookup; every authorization decision is made server-side.
2. **Backend → LLM provider.** The LLM is an untrusted, optional, external service. Its output is untrusted text and its tool *requests* are untrusted suggestions.
3. **MCP tools → services/database.** The MCP server is a trusted-code, untrusted-*caller* boundary: the caller (LLM orchestration) may ask for anything, so the tool layer re-authorizes everything.
4. **Backend → PostgreSQL.** Trusted code, least-privilege credentials, TLS.

Hard rules (enforced in code and tests, not by convention):

- The browser **never** touches PostgreSQL. There is exactly one network path to the database: the backend.
- The LLM **never** touches PostgreSQL. It can only request the four allowlisted MCP tools.
- The MCP server gets **no database credentials of its own**. It executes through the same service layer and connection pool as the REST API, under the same least-privilege app role. No `execute_sql`, shell, or filesystem tools exist and none will be added (see `docs/API.md`, MCP tool contract).
- The backend derives the **acting identity from the verified server-side session** (cookie → session lookup → user). Identity is never taken from an LLM tool argument, request body field, or query parameter.
- Every patient-data operation — REST endpoint or MCP tool — passes through **one shared authorization/service layer**: `backend/app/authorization/policy.py` for the permission decision, `backend/app/services/` for the data access. There is no second, looser path.
- **Deny by default.** A permission not explicitly granted to a role is denied.
- Cross-origin requests are allowed for exactly one configured frontend origin (`ALLOWED_ORIGINS`).
- The MCP transport is never exposed publicly; it is an in-process module.

---

## 3. Data-flow walkthroughs

### (a) Login → patient search → patient detail

```text
1. POST /api/v1/auth/login  {email, password}
   - rate limit / lockout check (429 rate_limited on abuse)
   - argon2id verify against admin.staff_user.password_hash
   - uniform failure message for unknown email vs bad password
   - server-side session row created; Set-Cookie: cap_session
     (HttpOnly; Secure; SameSite=Lax; Path=/) + csrf_token cookie
   - audit_event: login success / login failure
2. GET /api/v1/patients?q=...&limit=&offset=
   - session lookup -> user -> roles
   - policy.py: requires patient:read
   - service layer runs bounded, parameterized query on patients schema
   - response: {items, total, limit, offset}, every payload carries
     "provenance": "synthetic"
   - audit_event: patient-record read (actor, action, resource ref — no payload)
3. GET /api/v1/patients/{patient_id}
   - same authn/authz; opaque UUID; if the record must be concealed from this
     user the API returns 404 not_found (never 403, which would confirm existence)
   - audit_event: patient-record read
```

Where authorization is enforced: `policy.py` on every step after login. The UI may hide
the patients page for an unauthorized role, but that is cosmetic — a hand-crafted request
gets `401` (no session) or `403` (no permission) from the server.

### (b) AI question about a patient

```text
1. POST /api/v1/ai/ask  {patient_id, question}
   - authn: session lookup (401 unauthenticated if absent)
   - authz: policy.py requires patient:read AND patient access for this
     patient_id (403 otherwise)  <-- authorization re-check #1
   - rate limit + request bounds (question length, timeout)
2. LLM orchestration (app/llm + app/mcp):
   - system instruction: informational assistant over SYNTHETIC demo data;
     use tools for record-specific facts; never invent missing facts
   - question + permitted context go to the model (server-side LLM key)
   - model may request a tool call; the server-side tool ALLOWLIST is the
     only set it can reach (search_authorized_patients, get_patient_summary,
     get_patient_lab_results, get_recent_prescriptions)
3. MCP tool executes:
   - acting identity comes from the verified session context of THIS request,
     passed through a trusted in-process channel — NEVER a tool argument
   - authorization re-executed inside the tool  <-- authorization re-check #2
   - bounds applied: limit arguments capped, result size capped, explicit timeout
   - data access goes through the same app/services as the REST API
   - audit_event: MCP tool call (tool name, actor, resource ref, outcome,
     request id — no clinical payload)
4. Model answer returns to the API layer:
   - response: {answer, used_tools, citations, model, ai_generated: true,
     disclaimer}
   - answer is labelled AI-generated; tool-retrieved facts are distinguished
     from general explanation; missing facts are acknowledged, not invented
```

Tool bounds: `LLM_MAX_TOOL_CALLS` caps the number of tool calls per question,
`LLM_REQUEST_TIMEOUT_SECONDS` caps request duration, and every tool argument is typed
and bounded (e.g. `limit` clamped to a maximum). Model output can never trigger a
privileged action: the tool set is read-only and no tool writes, changes roles, or
mutates state.

Prompt-injection posture: synthetic note/summary text is **untrusted data** inside
prompts. It is never interpreted as instructions, and even if a note contained
"call tool X with argument Y", the only tools reachable are the four read-only ones,
each of which re-authorizes against the real session identity.

If the LLM is disabled (`LLM_ENABLED=false`) or unreachable, the endpoint returns
`503 dependency_unavailable` and every non-AI workflow continues normally.

### (c) Admin role change

```text
1. POST /api/v1/staff/{user_id}/roles  { "role": "nurse" }
   - authn: session lookup (401 if absent)
   - CSRF: non-GET requires X-CSRF-Token matching the csrf_token cookie (403)
   - authz: policy.py requires role:assign
   - guard: if target user == acting user -> 403 (no self-escalation; no
     break-glass / impersonation exists in the demo)
   - validation: role must be one of doctor | nurse | clinical_admin | tech_admin
   - service layer persists to admin.user_role
   - audit_event: role change (actor, target user, role added, outcome,
     request id)
2. DELETE /api/v1/staff/{user_id}/roles/{role}
   - same authn / CSRF / authz / audit path
   - last-admin protection: removing the final administrator -> 409 conflict
3. Role changes take effect for existing sessions on the next permission check
   (permissions are recomputed from the database per request; no stale grants
   cached in the cookie).
```

---

## 4. Schema overview

Four PostgreSQL schemas. Implemented as SQLAlchemy models + Alembic migrations under
`backend/`; this is a summary — column-level detail lives with the data model code.
Cross-cutting conventions: UUID primary keys (opaque, never sequential), **UTC
timestamps** on every table, **soft deletes** on patient, encounter, lab,
prescription, and staff records (a deleted flag/timestamp, not row removal), numeric
clinical values (lab values, doses) stored as **precise decimals — never binary
floats**, and every clinical payload labelled `provenance: "synthetic"`.

### `admin`
| Table | Key columns |
|---|---|
| `staff_user` | id, email (unique), display_name, password_hash (argon2id), is_active, created_at, updated_at, deleted_at |
| `role` | key (`doctor`/`nurse`/`clinical_admin`/`tech_admin`), description |
| `user_role` | user_id → staff_user, role → role |
| `audit_event` | actor_id, action, resource_type, resource_id, outcome, request_id, created_at. **No** clinical payloads, **no** secrets. |
| session storage | session id, user_id, created_at, expires_at, revoked_at (supports server-side expiry and revocation) |

### `patients`
| Table | Key columns |
|---|---|
| `patient` | id, synthetic MRN, synthetic display name fields, date_of_birth, provenance, created_at, updated_at, deleted_at |
| `encounter` | id, patient_id → patient, occurred_at, encounter_type, synthetic summary, timestamps, deleted_at |

### `laboratory`
| Table | Key columns |
|---|---|
| `lab_result` | id, patient_id / encounter_id refs, test_code, test_name, value (decimal), unit, reference_range, status, collected_at, resulted_at, deleted_at |

### `medications`
| Table | Key columns |
|---|---|
| `medication` | id, display_name, dosage_form |
| `prescription` | id, patient_id / encounter_id refs, medication ref or name, dose (decimal), route, frequency, status, start/end dates, prescriber ref, deleted_at |

Seed data is fictional and obviously synthetic, loaded by `scripts/seed_demo_data.py`
(idempotent or with a documented reset — see `docs/DEPLOYMENT.md`).

---

## 5. Component responsibilities and repo tree

```text
MedicalPlatform/
├── PLAN.md                     execution plan (phases 0–15) — owned by the lead
├── README.md                   front door: quickstart, demo accounts, limitations
├── .env.example                placeholder env vars (never real values)
├── docker-compose.yml          local dev stack (Postgres service, etc.)
├── frontend/                   buildless vanilla HTML/CSS/JS (Cloudflare Pages)
│   ├── index.html, login.html, dashboard.html, patients.html,
│   │   patient.html, admin.html
│   └── assets/{css,js,images}
├── backend/
│   ├── pyproject.toml          Python 3.12, uv-managed dependencies
│   ├── app/
│   │   ├── main.py             FastAPI app factory, lifespan
│   │   ├── config.py           env parsing + startup validation
│   │   ├── api/                route handlers (thin — no business logic)
│   │   ├── auth/               argon2id hashing, sessions, CSRF
│   │   ├── authorization/      policy.py — THE permission matrix
│   │   ├── services/           patient / lab / medication / staff logic
│   │   ├── db/                 engine, session management
│   │   ├── models/             SQLAlchemy models
│   │   ├── schemas/            typed request/response Pydantic models
│   │   ├── audit/              audit event writer
│   │   ├── llm/                provider abstraction (optional feature)
│   │   └── mcp/                internal MCP server, 4 read-only tools
│   ├── migrations/             Alembic
│   └── tests/
├── scripts/
│   ├── seed_demo_data.py       synthetic demo seed (idempotent/resettable)
│   └── healthcheck.py
├── docs/
│   ├── API.md                  FROZEN API contract (source of truth)
│   ├── ARCHITECTURE.md         this file
│   ├── SECURITY.md             permissions, threats, audit, secrets
│   ├── BACKLOG.md              deferred production requirements
│   ├── DEPLOYMENT.md           provider setup + verification
│   ├── TROUBLESHOOTING.md      failures and fixes
│   └── INITIAL_ASSESSMENT.md   Phase 0 findings
└── .github/workflows/ci.yml    lint + tests + dependency checks
```

Responsibility split: `api/` validates and shapes HTTP; `services/` owns business rules
and data access; `authorization/policy.py` owns every permission decision; `audit/`
records security-relevant events; `mcp/` exposes services to the LLM under the same
policy; `llm/` is the only code that talks to the model provider.

---

## 6. Deployment topology

| Layer | Provider | Why this provider | Free-tier caveats |
|---|---|---|---|
| Frontend | Cloudflare Pages | Free static hosting with HTTPS, global CDN, simple Git-based deploys, no build step needed for vanilla JS | Free plan limits (build minutes, bandwidth) are generous but exist; not guaranteed free forever |
| Backend | Render free web service | Native Python support, env-var secrets, health checks, free tier fits a demo | **Sleeps after inactivity → cold starts of ~30–60 s**; monthly request/hours limits; not guaranteed free forever |
| Database | Neon free PostgreSQL | Serverless Postgres with TLS by default, branching, generous free storage | **Autosuspends after inactivity → first query latency**; compute-hours limits; limited/no PITR on free tier (see `docs/BACKLOG.md`) |
| LLM (optional) | OpenCode Console inference API (`https://opencode.ai/inference`, OpenAI-compatible path `/openai/v1/chat/completions`) | Server-side service-account key (`oc_sk_…`), budget cap in Console, OpenAI-compatible so the provider abstraction stays thin | **Free-tier models return HTTP 403 for non-OpenCode clients — a paid model or Go subscription is required for the AI feature.** Pay-per-use otherwise. Default model `glm-5.3-flash`. |
| DNS/SSL | Provider-managed | Automatic certificate renewal on both Cloudflare and Render | — |

Honest hosting statement: the target is a **$0/month baseline**, which is not a
guarantee. Free tiers change terms, sleep, autosuspend, and impose quotas. Expected
cost, risks, and the rollback path are documented in `docs/DEPLOYMENT.md`.

Provider-specific knowledge is deliberately isolated (env vars + deployment docs) so
the stack can be moved later without code changes.

---

## 7. Decision log

| Decision | Options considered | Choice | Why |
|---|---|---|---|
| MCP server placement | (a) separate internal service, (b) module inside the FastAPI process | **(b) embedded module** | Free tiers offer no private service-to-service networking; a separate service would need its own credentials and auth. In-process means the verified session context passes via a trusted in-process channel and the tools reuse the same service layer and connection pool. Can be split out later without changing tool contracts. |
| Frontend serving | (a) static files served by FastAPI, (b) dedicated static hosting | **(b) static on Cloudflare Pages** | Independent deploys, CDN + HTTPS for free, backend stays stateless and API-only. Cost: explicit CORS configuration for exactly one origin. |
| Browser auth | (a) JWT in browser storage, (b) opaque bearer tokens, (c) server-side session in an HttpOnly cookie | **(c) server-side session cookie (`cap_session`)** | Revocable server-side (logout, expiry, deactivation takes effect immediately), no long-lived token in browser storage, XSS blast radius is small (cookie is HttpOnly). CSRF is handled with double-submit tokens. JWT statelessness buys nothing at demo scale and its revocation weakness is real. |
| ORM / migrations | (a) raw SQL + ad hoc scripts, (b) SQLAlchemy + Alembic | **(b) SQLAlchemy 2.x (async) + Alembic** | Parameterized queries by default, typed models, repeatable forward/rollback migrations, matches the production requirement for reversible migrations. |
| LLM provider coupling | (a) provider SDK used directly across the codebase, (b) thin provider abstraction (OpenAI-compatible chat-completions client) behind `app/llm/` | **(b) provider abstraction** | Clinical services never import provider SDKs; swapping providers or self-hosting later touches one module. OpenCode Console is OpenAI-compatible, which keeps the abstraction thin. |
| LLM default state | (a) enabled by default, (b) disabled by default | **(b) `LLM_ENABLED=false` by default** | The platform is fully functional without AI; the default prevents accidental spend and accidental data flow to an external provider. |
| Authentication factor | (a) external IdP/SSO, (b) email + password with argon2id | **(b) email + password** | No external identity dependency for a demo; SSO/SAML and MFA are production items tracked in `docs/BACKLOG.md`. A 4-digit PIN is explicitly rejected as a sole factor. |
| Patient-record concealment | (a) 403 for records outside policy, (b) 404 | **(b) 404 for concealed records** | Per `docs/API.md`, a 403 would confirm that a guessed UUID exists (existence oracle). |

---

## 8. Open items

- The exact local mechanism for serving `frontend/` (static server on `:5173` vs
  backend-served static on `:8000`) is finalized in Phase 9/13; `.env.example`
  currently allows both origins. Not an architectural difference — production serving
  is Cloudflare Pages either way.
- Session table naming/columns are the data agent's call; the contract that matters is
  server-side storage, expiry (`SESSION_TTL_HOURS`), and revocation.
