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
        |                     module, 7 read-only     |
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
| Speech recognition (optional) | `speech/` + `backend/app/services/voice.py` | Private whisper.cpp container (local compose `speech` profile; Cloud Run documented, not deployed) | **No — private network only** |

---

## 2. Trust boundaries and hard rules

Trust boundaries:

1. **Browser → Backend.** Everything arriving from the browser is untrusted input. The session cookie proves *who* the caller is only after server-side session lookup; every authorization decision is made server-side.
2. **Backend → LLM provider.** The LLM is an untrusted, optional, external service. Its output is untrusted text and its tool *requests* are untrusted suggestions.
3. **MCP tools → services/database.** The MCP server is a trusted-code, untrusted-*caller* boundary: the caller (LLM orchestration) may ask for anything, so the tool layer re-authorizes everything.
4. **Backend → PostgreSQL.** Trusted code, least-privilege credentials, TLS.

Hard rules (enforced in code and tests, not by convention):

- The browser **never** touches PostgreSQL. There is exactly one network path to the database: the backend.
- The LLM **never** touches PostgreSQL. It can only request the seven allowlisted MCP tools.
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
   - response: {items, total, limit, offset}; provenance stays database-internal
   - audit_event: patient-record read (actor, action, resource ref — no payload)
3. GET /api/v1/patients/{patient_id}
   - same authn/authz; opaque UUID; if the record must be concealed from this
     user the API returns 404 not_found (never 403, which would confirm existence)
   - audit_event: patient-record read
4. POST /api/v1/patients  (Doctor/Admin; patient:write)
   - synthetic patient creation only; name fields + optional DOB/sex/blood type
   - server assigns the MRN and `provenance='synthetic'`; DB constraints are
     the final guard (unique MRN, closed vocabularies)
   - audit_event: patient.create (new patient id only, no clinical payload)
5. DELETE /api/v1/patients/{patient_id}  (Admin only; patient:delete)
   - soft delete: `deleted_at` is set; the row and child clinical/audit history
     are retained in the database
   - patient list/search/detail, encounter access and appointment joins hide it
   - audit_event: patient.delete (metadata only; no clinical payload)
```

Where authorization is enforced: `policy.py` on every step after login. The UI may hide
the patients page for an unauthorized role, but that is cosmetic — a hand-crafted request
gets `401` (no session) or `403` (no permission) from the server.

### (b) AI question about a patient

```text
1. POST /api/v1/ai/ask  {patient_id?, question}
   - authn: session lookup (401 unauthenticated if absent)
   - authz: policy.py requires patient:read; patient mode also requires patient
     access for this patient_id (404 not_found otherwise, concealment) <-- #1
   - deterministic input guardrail + rate limit + request bounds
2. LLM orchestration (app/llm + app/mcp):
   - system instruction: clinical-information assistant; use tools for
     record-specific facts; never invent missing facts
   - question + minimized, authorized tool results go to the model (server-side
     LLM key)
   - model may request a tool call; the server-side tool ALLOWLIST is the
     only set it can reach. Patient mode permits only get_patient_summary,
     get_patient_lab_results, and get_recent_prescriptions with the exact
     verified patient_id; general mode may use all seven read-only tools.
3. MCP tool executes:
   - acting identity comes from the verified session context of THIS request,
     passed through a trusted in-process channel — NEVER a tool argument
   - authorization re-executed inside the tool  <-- authorization re-check #2
   - bounds applied: limit arguments capped, result size capped, explicit timeout
   - data access goes through the same app/services as the REST API
   - model-facing payload minimization drops MRNs, raw dates of birth, and
     unnecessary foreign keys while retaining clinical facts and citation IDs
   - audit_event: MCP tool call (tool name, actor, resource ref, outcome,
     request id — no clinical payload)
4. Model answer returns to the API layer:
   - deterministic output guardrail blocks secrets/DSNs/external URLs/raw HTML,
     internal prompt/config text, and unauthorized citation IDs
   - response: {answer, used_tools, citations, model, ai_generated: true}
   - answer is labelled AI-generated; tool-retrieved facts are distinguished
     from general explanation; missing facts are acknowledged, not invented
```

Tool bounds: `LLM_MAX_TOOL_CALLS` caps patient-scoped tool calls (default 4),
`LLM_MAX_TOOL_CALLS_GENERAL` caps general-lookup tool calls (default 10,
clamped 1–20), `LLM_REQUEST_TIMEOUT_SECONDS` caps request duration, and every
tool argument is typed and bounded (e.g. `limit` clamped to a maximum). Model
output can never trigger a privileged action: the tool set is read-only and no
tool writes, changes roles, or mutates state.

Prompt-injection posture: record text is **untrusted data** inside prompts. It is
never interpreted as instructions, and even if a note contained "call tool X
with argument Y", the only tools reachable are the seven read-only ones, each
of which re-authorizes against the real session identity. Deterministic
guardrails additionally reject explicit credential/database/shell requests and
bind patient-scoped tool calls to the verified patient.

If the LLM is disabled (`LLM_ENABLED=false`) or unreachable, the endpoint returns
`503 dependency_unavailable` and every non-AI workflow continues normally.

### (c) Appointment scheduling and voice proposal

```text
A. Browser recording (explicit user action only)
1. getUserMedia({audio}) -> MediaRecorder (feature-detected MIME candidates)
2. Stop: recorder.stop(), microphone tracks stopped immediately; audio stays in
   memory. No background listening, no upload until the user asks to transcribe.
3. POST /api/v1/voice/transcriptions (multipart file + language en|bg)
   - authn session -> authz appointment:write (Doctor/Admin; nurse 403)
   - CSRF + per-user voice rate limit
   - BodySizeLimitMiddleware: exact-route 4 MiB allowance (hard clamp 8 MiB);
     all other routes stay at 1 MiB
   - stream to 0700 temp dir (0600 file) -> FFprobe duration bound (60 s)
     -> FFmpeg 16 kHz mono PCM WAV (fixed args, timeout)
   - private whisper.cpp `/inference` (optional bearer / Cloud Run ID token)
   - temp dir deleted in finally; audio/transcript never logged or persisted
   - response {text, language, model, duration_seconds}; text is editable

B. AI proposal (never a write)
4. POST /api/v1/appointments/interpret {text}
   - authz appointment:write; 10/min limiter
   - provider.complete(system=anchor+strict JSON, messages=[text], tools=[])
     (the model receives NO patient id, doctor id, or UTC timestamp to choose)
   - model returns a patient search phrase + clinic-local date/time + duration
   - server resolves the patient via the existing read-only MCP tool
     search_authorized_patients (still one of exactly seven tools)
   - server converts Europe/Sofia wall time -> UTC (DST holes/folds rejected)
     and runs the same working-hours/time-off/overlap checks
   - response is a proposal: patient/candidates, start_at, warnings,
     can_confirm, ai_generated=true. Nothing is persisted.

C. Explicit confirmation
5. Doctor reviews/edits the proposal and presses Create.
6. POST /api/v1/appointments (or PATCH for reschedule)
   - authz again; doctor-own-calendar scoping; Admin must name doctor_id
   - patient access checked; future/working-hours/time-off/overlap revalidated
   - PostgreSQL advisory locks on doctor + patient, overlap rechecked inside
     the transaction, then insert/update
   - audit appointment.create/update/reschedule/cancel (no clinical payload)
```

The AI cannot mutate scheduling state. If the LLM is disabled, the proposal
endpoint returns `503` and the manual calendar/voice transcript editing path
still works.

### (d) Admin role change

```text
1. POST /api/v1/staff/{user_id}/roles  { "role": "nurse" }
   - authn: session lookup (401 if absent)
   - CSRF: non-GET requires X-CSRF-Token matching the csrf_token cookie (403)
   - authz: policy.py requires role:assign
   - guard: if target user == acting user -> 403 (no self-escalation; no
     break-glass / impersonation exists in the demo)
   - validation: role must be one of doctor | nurse | clinical_admin
     (any other key — including the removed tech_admin — is a 400)
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

### (e) Admin analytics read model

```text
1. GET /api/v1/analytics/summary?start=YYYY-MM-DD&end=YYYY-MM-DD
   - authn: session lookup (401 if absent)
   - authz: policy.py requires analytics:read (Admin only; Doctor/Nurse 403)
   - start/end optional but both-or-neither; inclusive UTC dates, default last
     30 days, maximum 366 days; unknown/partial/reversed ranges are 400
2. app/services/analytics.build_summary (one bounded read model)
   - counts: scalar subqueries over the bounded tables
   - activity: scalar subqueries for the selected window AND the equally long
     preceding window (comparisons; null change when the previous is zero)
   - patients/encounters: distinct-patient and grouped encounter queries
   - appointments: status/visit-type/weekday/hour/trend aggregates using the
     clinic timezone, over retained records (calendar visibility does not
     delete history); booked minutes clipped to the UTC window and restricted to doctors
     with recorded working_hours; available minutes generated only from those
     records minus merged time_off
   - labs: one SQL abnormal expression (_ABNORMAL) and one evaluable predicate
     (_EVALUABLE) feed classification, rate, top tests and trend together
   - prescriptions: period start_date mix + lifetime mix + daily trend
   - security: grouped audit actions by UTC day (no raw events serialized)
   - AI: ai.ask outcome partition + mcp.<tool> call/failure counts
   - data quality/system health: recorded gaps plus configuration facts; no
     synthetic probes
3. Response is aggregate-only and typed (app/schemas/analytics.py), writes one
   analytics.read audit event (metadata only, no range payload) and commits.
```

The frontend makes exactly **one** summary request per date-filter change and
renders every section from the payload. Clinical/audit daily buckets are UTC;
appointment weekday/hour/trend buckets use `CLINIC_TIMEZONE` because schedules
are local wall-clock values. Missing reference ranges are unclassified, never
normal. Utilization is clinic-wide booked / recorded-available minutes, not a
per-doctor ranking. Per-request AI latency and voice transcription metrics are
not persisted, so the API returns explicit `available: false` objects instead
of numbers. CSV/print analytics is deferred: a future export must be its own
authorized, audited, aggregate-only path (the existing print feature is
patient-record-specific).

### (f) Admin system logs and live stream

```text
1. GET /api/v1/admin/logs[?filters] or GET /api/v1/admin/logs/stream
   - authn: session lookup (401 if absent)
   - authz: policy.py requires logs:read (Admin only; Doctor/Nurse 403)
2. Historical read model (app/logs/service.py)
   - audit rows stay in admin.audit_event; operational rows in admin.system_event
   - one bounded query per source for the selected <=7-day window, merged in
     memory and ordered by (timestamp, source-prefixed event id)
   - counts are grouped SQL over both sources (never a client-side count)
3. Live delivery
   - SSE subscribes to the bounded in-process bus BEFORE the short replay query
   - replay is bounded (<=200) and resumes after last_event_id/Last-Event-ID
   - exact-id dedup across replay/live; heartbeat every 15 s; the session is
     revalidated periodically even on a busy stream
   - no database session stays open for the lifetime of the connection
4. Audit events are published to the bus only after their transaction commits
   (SQLAlchemy after_commit); rollbacks discard the pending payload. The bus is
   a delivery optimization, never the source of truth: reconnect always reads
   persisted rows.
```

The deployment is one application process, so an in-process `asyncio.Queue`
per subscriber (bounded at 500, oldest dropped on overflow) is sufficient and
needs no Redis. A multi-worker or multi-instance deployment would miss live
events published by another worker; it would need PostgreSQL `LISTEN/NOTIFY` or
another bounded broker. See `docs/SYSTEM_LOGS.md`.

---

## 4. Schema overview

Five PostgreSQL schemas. Implemented as SQLAlchemy models + Alembic migrations under
`backend/`; this is a summary — column-level detail lives with the data model code.
Cross-cutting conventions: UUID primary keys (opaque, never sequential), **UTC
timestamps** on every table, **soft deletes** on patient, encounter, lab,
prescription, and staff records (a deleted flag/timestamp, not row removal), numeric
clinical values (lab values, doses) stored as **precise decimals — never binary
floats**, and provenance remains database-internal rather than serializing into
payloads.

### `admin`
| Table | Key columns |
|---|---|
| `staff_user` | id, email (unique), display_name, password_hash (argon2id), is_active, created_at, updated_at, deleted_at |
| `role` | key (`doctor`/`nurse`/`clinical_admin` — DB CHECK `ck_role_key_valid`; `tech_admin` removed by owner decision 2026-10-26), description |
| `user_role` | user_id → staff_user, role → role |
| `audit_event` | actor_id, action, resource_type, resource_id, outcome, request_id, created_at. **No** clinical payloads, **no** secrets. Durable accountability record. |
| `system_event` | severity, category, event_type, actor_id, resource_type, resource_id, request_id, outcome, safe JSONB metadata, created_at. Operational monitoring only; 7-day bounded retention, allowlisted flat metadata, **no** clinical payloads, bodies, transcripts, audio or secrets. |
| session storage | session id, user_id, created_at, expires_at, revoked_at (supports server-side expiry and revocation) |

### `patients`
| Table | Key columns |
|---|---|
| `patient` | id, synthetic MRN, synthetic display name fields, date_of_birth, provenance, created_at, updated_at, deleted_at |
| `encounter` | id, patient_id → patient, occurred_at, encounter_type, synthetic summary, practitioner_id, weight_kg, height_cm, systolic_bp, diastolic_bp, heart_rate, temperature_c, oxygen_saturation, timestamps, deleted_at |

### `laboratory`
| Table | Key columns |
|---|---|
| `lab_result` | id, patient_id / encounter_id refs, test_code, test_name, value (decimal), unit, reference_range, status, collected_at, resulted_at, deleted_at |

### `medications`
| Table | Key columns |
|---|---|
| `medication` | id, display_name, dosage_form |
| `prescription` | id, patient_id / encounter_id refs, medication ref or name, dose (decimal), route, frequency, status, start/end dates, prescriber ref, deleted_at |

### `scheduling`
| Table | Key columns |
|---|---|
| `doctor_schedule` | doctor_id → staff_user (PK), default_duration_minutes, slot_increment_minutes, timestamps |
| `working_hours` | id, doctor_id → doctor_schedule, weekday (0=Mon..6=Sun), start_time/end_time (clinic-local TIME), timestamps |
| `time_off` | id, doctor_id, start_at/end_at (UTC), reason, created_by, timestamps |
| `appointment` | id, patient_id → patient (RESTRICT), doctor_id → staff_user (RESTRICT), start_at/end_at (UTC), appointment_type, status (`scheduled`/`confirmed`/`completed`/`cancelled`/`no_show`), notes, created_by, timestamps, `calendar_removed_at` (calendar visibility only; record retained) |
| `admin.message` | id, sender_id/recipient_id → staff_user (RESTRICT), subject, body, read_at, timestamps; own-inbox/own-sent only, no attachments |

Appointments are scheduling metadata, not clinical records. Cancellation is a
status transition; rows are retained. There is no soft-delete column on
appointments. Working hours are wall-clock clinic time; every API timestamp is
UTC. The seed adds Monday–Friday 09:00–12:00 / 13:00–17:00 plus three upcoming
demo appointments; re-running it is idempotent.

Seed data is fictional and obviously synthetic, loaded by `scripts/seed_demo_data.py`
(idempotent or with a documented reset — see `docs/DEPLOYMENT.md`). Since
2026-11-11 the seed adds a longitudinal expansion per patient: 8–12 visits over
~24 months, repeated key-lab panels, baseline/follow-up points around mapped
prescriptions, and per-visit BP/HR/temperature/SpO2. The response view is
derived at read time; no separate outcome table duplicates clinical data.

---

## 5. Component responsibilities and repo tree

```text
MedicalPlatform/
├── PLAN.md                     execution plan (phases 0–15) — owned by the lead
├── README.md                   front door: quickstart, demo accounts, limitations
├── .env.example                placeholder env vars (never real values)
├── docker-compose.yml          local dev stack (Postgres service, etc.)
├── frontend/                   buildless vanilla HTML/CSS/JS (Cloudflare Pages)
│   ├── index.html, dashboard.html, patients.html, patient.html,
│   │   appointments.html, admin.html, ai.html, analytics.html,
│   │   messages.html, system-logs.html
│   └── assets/{css,js,images}
├── backend/
│   ├── pyproject.toml          Python 3.12, uv-managed dependencies
│   ├── app/
│   │   ├── main.py             FastAPI app factory, lifespan
│   │   ├── config.py           env parsing + startup validation
│   │   ├── api/                route handlers (thin — no business logic)
│   │   ├── auth/               argon2id hashing, sessions, CSRF
│   │   ├── authorization/      policy.py — THE permission matrix
│   │   ├── services/           patient / lab / medication / staff logic,
│   │   │                       appointments.py, appointment_ai.py, voice.py
│   │   ├── db/                 engine, session management
│   │   ├── models/             SQLAlchemy models
│   │   ├── schemas/            typed request/response Pydantic models
│   │   ├── audit/              audit event writer
│   │   ├── logs/               event registry, bounded bus, unified read model
│   │   ├── llm/                provider abstraction (optional feature)
│   │   └── mcp/                internal MCP server, 7 read-only tools
│   ├── migrations/             Alembic
│   └── tests/
├── speech/                     private whisper.cpp image (pinned release,
│                               checksum-verified Tiny multilingual model)
├── scripts/
│   ├── seed_demo_data.py       synthetic demo seed (idempotent/resettable)
│   └── healthcheck.py
├── docs/
│   ├── API.md                  FROZEN API contract (source of truth)
│   ├── ARCHITECTURE.md         this file
│   ├── SECURITY.md             permissions, threats, audit, secrets
│   ├── SPEECH_RECOGNITION.md   voice/whisper.cpp design, limits, setup
│   ├── BACKLOG.md              deferred production requirements
│   ├── DEPLOYMENT.md           provider setup + verification
│   ├── TROUBLESHOOTING.md      failures and fixes
│   ├── SYSTEM_LOGS.md          event catalog, SSE and retention details
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
| LLM (optional) | OpenCode inference API: standard `https://opencode.ai/inference` (funded key/free quota) or Go `https://opencode.ai/inference/go` (active subscription); OpenAI-compatible `.../openai/v1/chat/completions` | Server-side service-account key (`oc_sk_…`), budget cap in Console, OpenAI-compatible so the provider abstraction stays thin. Key-only setup auto-enables; `LLM_ENABLED=false` is the kill switch. | **The key must have model access:** standard keys need credit or available free quota; `/go` needs an active Go subscription. Default Go model `deepseek-v4.1-flash`; standard alternative `gpt-6-luna`. |
| Speech (optional) | Self-hosted whisper.cpp container (`speech/`) | CPU-only, private network, Tiny multilingual model baked in; local compose `speech` profile | Cloud Run deployment is documented but **not verified**; requires private service-to-service IAM. The demo works with voice disabled. |
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
| LLM default state | (a) enabled by default, (b) disabled by default | **Auto: disabled when no key, enabled when `LLM_API_KEY` is present; `LLM_ENABLED=false` is an explicit kill switch** | Key-only setup works without a second flag, while deployments can still force-disable AI. Placeholder keys are treated as missing. |
| Authentication factor | (a) external IdP/SSO, (b) email + password with argon2id | **(b) email + password** | No external identity dependency for a demo; SSO/SAML and MFA are production items tracked in `docs/BACKLOG.md`. A 4-digit PIN is explicitly rejected as a sole factor. |
| Patient-record concealment | (a) 403 for records outside policy, (b) 404 | **(b) 404 for concealed records** | Per `docs/API.md`, a 403 would confirm that a guessed UUID exists (existence oracle). |
| System-log live transport | (a) polling, (b) SSE with persisted replay, (c) Redis/pub-sub | **(b) SSE + bounded in-process bus + DB replay** | One-way admin monitoring, current deployment is a single process, and the database remains the source of truth. No extra broker; reconnect and historical search still work after a restart. Multi-worker fan-out is a documented follow-up. |

---

## 8. Open items

- The exact local mechanism for serving `frontend/` (static server on `:5173` vs
  backend-served static on `:8000`) is finalized in Phase 9/13; `.env.example`
  currently allows both origins. Not an architectural difference — production serving
  is Cloudflare Pages either way.
- Session table naming/columns are the data agent's call; the contract that matters is
  server-side storage, expiry (`SESSION_TTL_HOURS`), and revocation.
