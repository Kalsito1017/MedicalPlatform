# MediCore Clinical Platform — Security Audit

**Report date:** 2026-11-14  
**Auditor:** DeepSeek V4.1 Flash (OpenCode)  
**Audit type:** Read-only application and infrastructure security review  
**Target:** MediCore Clinical Platform (`/mnt/c/Users/Kaloyan/MedicalPlatform`)  
**Git commit at audit start/end:** `5a79445f34fa6fc8a89d59ae80311e57b2c9d473`  
**Environment reviewed:** local Docker Compose stack (`app`, `db`, `speech`), static code, Kubernetes manifests, and deployment documentation  
**Data classification during audit:** synthetic demo data only  
**Mode:** READ-ONLY — no application code, configuration, database record, permission, secret, Docker, or dependency changes were made

> **Scope and honesty note.** This is an engineering security assessment, not a compliance certification, legal opinion, or clinical validation. The repository is explicitly a synthetic-data demo. Findings describe the current local deployment and the code as it exists at the commit above. Production HTTPS, external-provider contracts, live Kubernetes behavior, backup restores, and real-browser behavior could not be verified and are listed in Section J.

---

## Section A — Executive Summary

### A.1 Overall assessment

The application layer has strong, unusually well-documented security foundations for a demo: argon2id password hashing, opaque server-side sessions with hashed tokens, server-enforced RBAC, CSRF double-submit protection, strict CSP, safe DOM rendering, parameterized SQL, bounded uploads, advisory-lock appointment scheduling, and a read-only MCP tool layer that re-authorizes every AI tool call.

The **running deployment**, however, is not safe to expose beyond an isolated development machine:

1. **PostgreSQL is published on all host interfaces** and the application connects as a **superuser** using the documented compose default password. TLS is off. Anyone who can reach `host:5432` can read, modify, or destroy the entire database.
2. **All three active demo accounts — including the Admin — authenticate with the documented placeholder password.** The Admin account holds every permission. There is no MFA.
3. **Patient access is clinic-wide by design**: any principal with `patient:read` (Doctor, Nurse, or Admin) can read every patient. This is documented as a demo policy, but it is a production blocker for real patient data.
4. **Clinical record data is sent to an external LLM provider** when the assistant is used. The provider’s retention, training, residency, and contractual terms are not established in the repository.
5. The local stack runs with `APP_ENV=development`, serves plain HTTP, exposes `/docs` and `/openapi.json`, and does not set `Secure` cookies over HTTP.

The codebase is suitable for an **isolated, synthetic-data demonstration**. It is **not production-ready** and must not receive real patient data in its current configuration.

### A.2 Finding counts

| Severity | Count | Finding IDs |
|---|---:|---|
| Critical | 0 | — |
| High | 2 | F-01, F-02 |
| Medium | 3 | F-03, F-04, F-05 |
| Low | 6 | F-06, F-07, F-08, F-09, F-10, F-11 |
| Informational | 6 | F-12, F-13, F-14, F-15, F-16, F-17 |
| **Total** | **17** | |

**Confirmed vulnerabilities:** F-01, F-02 (High); F-06 (Low, with the stated conditions).  
**Confirmed control gaps / exposures:** F-08, F-10, F-11 (Low); F-03, F-05, F-07 (Medium/Low design and control gaps).  
**Potential risks requiring external verification:** F-04 (external LLM provider safeguards); F-09 (TLS/proxy deployment conditions).  
**Informational observations / hardening:** F-12–F-17.

### A.3 Most important risks

| Rank | Risk | Why it matters |
|---:|---|---|
| 1 | **Documented default credentials for all demo roles, including Admin** (F-02) | An attacker who can reach the app can log in as Admin with a known password and access every patient, staff account, role, audit event, and analytics view. No MFA. |
| 2 | **Public PostgreSQL with default superuser credentials and no TLS** (F-01) | Complete database compromise, including all clinical, message, attachment, audit, and AI-history data. The application also connects to PostgreSQL as that superuser role. |
| 3 | **Clinic-wide patient access** (F-03) | Any Doctor, Nurse, or Admin can read any patient; there is no care-relationship or tenant scope. Unacceptable for real PHI. |
| 4 | **Clinical data sent to an external LLM** (F-04) | PHI leaves the deployment; provider retention/training/DPA terms are unverified. |
| 5 | **No MFA / step-up authentication** (F-05) | Password compromise is full account compromise, especially for Admin. |
| 6 | **Plain HTTP and development-mode exposure** (F-09, F-10) | Session cookies and credentials travel without TLS on the local network; API docs and a relaxed docs CSP are exposed. |

### A.4 Overall risk rating

**HIGH for any shared, networked, or real-data deployment.**  
**Acceptable only as an isolated synthetic-data demo** with the ports bound to loopback, demo credentials rotated, and no real patient data.

### A.5 What is working well

- Argon2id hashing with constant-shape verification and a dummy hash for unknown accounts.
- Opaque 32-byte session tokens; only SHA-256 hashes are stored; expiry, revocation, inactive-user rejection, and anti-fixation rotation are implemented.
- Server-side permission checks on every protected route; frontend hiding is not treated as a control.
- Self-role-assignment and last-active-Admin protections.
- Message and attachment ownership checks; unrelated staff (including Admin) get `404`.
- Doctor-own-calendar appointment scoping; advisory locks and overlap re-checks prevent double booking.
- Seven read-only MCP tools; server-injected identity; per-call re-authorization; bounded tool loop; output validation.
- Voice upload bounds, fixed FFmpeg arguments, private speech service, and temporary-directory cleanup.
- Strict CSP, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `no-referrer`, COOP/CORP, and no third-party frontend scripts.
- Safe Markdown renderer that never parses HTML; user and AI content becomes text nodes.
- Parameterized ORM queries; no raw SQL except `SELECT 1`.
- Secret redaction in structured logs; tracked-file secret scan passes; `.env` and `k8s/secret.yaml` are gitignored and excluded from the Docker build context.

---

## Section B — Architecture and Attack Surface

### B.1 Deployment topology (current local stack)

```text
Browser (11 static HTML pages, 29 JS modules)
   │  HTTPS/HTTP, cookie session + CSRF header
   ▼
FastAPI / Uvicorn :8000  ──────────────► PostgreSQL 16 :5432
   │  static frontend + /api/v1 + /health        (published 0.0.0.0:5432)
   │
   ├──────────────► whisper.cpp speech :8080 (private compose network only)
   │
   └──────────────► External LLM provider (OpenCode inference; optional)
```

| Component | Current implementation | Exposure |
|---|---|---|
| `app` | FastAPI + Uvicorn, single-origin static frontend, API `/api/v1`, health endpoints | Published `0.0.0.0:8000` |
| `db` | PostgreSQL 16 Alpine, named volume `postgres_data` | Published `0.0.0.0:5432` |
| `speech` | whisper.cpp Base multilingual, `expose: 8080` only | Not published; compose network only |
| External LLM | OpenCode-compatible chat-completions endpoint | Outbound HTTPS from app |

**Production documentation** describes Cloudflare Pages (frontend), Render (backend), and Neon (PostgreSQL), with TLS at the edge and a same-origin `/api/*` proxy. That topology was **not deployed or verified** in this audit.

### B.2 Frontend

- **11 HTML pages:** `index`, `dashboard`, `patients`, `patient`, `appointments`, `messages`, `ai`, `analytics`, `admin`, `system-logs`, `404`.
- **29 JavaScript modules** under `frontend/assets/js/`, no build step, no `node_modules`, no source maps.
- All scripts, styles, fonts, and images are same-origin or `data:`; no third-party CDN in the application pages.
- CSP uses a SHA-256 hash for the single inline `window.__APP_CONFIG__` bootstrap; the same hash is mirrored in `frontend/_headers` for Cloudflare Pages.
- `frontend/assets/js/api.js` sends `credentials: 'include'`, adds `X-CSRF-Token` for non-GET requests, and uses `cache: 'no-store'`.
- Session tokens are never placed in `localStorage`/`sessionStorage`; only the theme preference is stored locally.

### B.3 Backend API surface

**51 route decorators** across the versioned API:

| Router | Routes | Main responsibility |
|---|---:|---|
| `auth.py` | 3 | login, logout, current user |
| `patients.py` | 10 | patients, encounters, labs, trends, prescriptions |
| `appointments.py` | 15 | availability, slots, appointments, history, CSV export, AI interpretation |
| `messages.py` | 7 | inbox/sent, counts, recipients, send, attachments, read state |
| `staff.py` | 5 | staff list/create/update/deactivate, role grant/revoke |
| `ai.py` | 4 | AI ask, conversation history list/clear/delete |
| `analytics.py` | 1 | clinic analytics summary |
| `audit.py` | 1 | audit event read |
| `system_logs.py` | 2 | operational log read and stream |
| `voice.py` | 1 | voice transcription |

Additional endpoints: `/health/live`, `/health/ready`, `/docs`, `/redoc`, `/openapi.json` (docs disabled in production by default), and static frontend serving.

### B.4 Authentication and session architecture

1. `POST /api/v1/auth/login` accepts email + password.
2. Email is normalized (strip/lowercase) by the schema.
3. Password is verified with argon2id (`PasswordHasher` defaults: time_cost=3, memory_cost=65536 KiB, parallelism=4).
4. Unknown email and wrong password use the same dummy-hash verification path and the same 401 body.
5. A new 32-byte URL-safe session token is generated; only its SHA-256 hash is stored in `admin.user_session`.
6. The raw token is set in the `cap_session` cookie: `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` when `APP_ENV=production`, the request is HTTPS, or `SESSION_COOKIE_SECURE=true`.
7. TTL defaults to 8 hours, fixed at creation; `last_seen_at` is refreshed at most once per minute.
8. Login revokes any pre-existing session cookie (anti-fixation); logout revokes the presented session; deactivating a user revokes all sessions.
9. Every request resolves the session from the database and recomputes roles/permissions; no authorization data is cached in the cookie.
10. CSRF uses a double-submit cookie (`csrf_token`, readable by JS) plus `X-CSRF-Token` on non-GET `/api/v1` requests.

### B.5 RBAC structure

Three roles and 17 permission keys are defined in `backend/app/authorization/policy.py`:

- **Doctor:** `patient:read`, `patient:write`, `message:read`, `message:write`, `lab:read`, `prescription:read`, `appointment:read`, `appointment:write`, `appointment:manage_availability`, `appointment:export`.
- **Nurse:** `patient:read`, `message:read`, `message:write`, `lab:read`, `prescription:read`, `appointment:read`.
- **Clinical Admin:** all 17 permissions, including `patient:delete`, `staff:read`, `staff:manage`, `role:assign`, `audit:read`, `logs:read`, `analytics:read`.

Every protected route uses `require_permission(...)`; `has_permission` denies unknown permissions; a 403 denial writes an `authz.denied` audit event. Patient-level access goes through the single seam `check_patient_access(principal, patient_id)` in `backend/app/services/access.py`. In this demo that seam returns true for every patient when the principal has `patient:read` — the documented demo policy, not a code defect.

### B.6 Database

- Schemas: `admin`, `patients`, `laboratory`, `medications`, `scheduling`, `public`.
- Key tables: `admin.staff_user`, `admin.role`, `admin.user_role`, `admin.user_session`, `admin.audit_event`, `admin.message`, `admin.message_attachment`, `admin.idempotency_record`; `patients.patient`; `scheduling.appointment`.
- Live synthetic dataset at audit time: **77 patients**, **3 active staff accounts**, **8,944 audit events**, **52 active session rows**.
- The application DB role `app` is a **superuser** (`rolsuper=t`, `rolcreaterole=t`, `rolcreatedb=t`), created by the official PostgreSQL image’s `POSTGRES_USER` mechanism.
- `listen_addresses='*'`, `ssl=off`; remote `pg_hba.conf` uses `scram-sha-256`, but the compose default password is in use.
- ORM/parameterized queries only; the only raw SQL observed is `SELECT 1` in `/health/ready`.

### B.7 AI and MCP

- `POST /api/v1/ai/ask` supports patient-scoped and general-lookup questions.
- The model receives only the seven registered MCP tool schemas; it has no database credentials or transport.
- Tools are read-only: `search_authorized_patients`, `get_patient_summary`, `get_patient_lab_results`, `get_recent_prescriptions`, `find_patients_by_lab`, `find_patients_by_medication`, `get_my_recent_patients`.
- The verified caller identity is injected via `ToolContext` and excluded from the LLM-facing schema; every tool re-checks permissions and patient access.
- Tool calls are bounded by `LLM_MAX_TOOL_CALLS` (default 4) or `LLM_MAX_TOOL_CALLS_GENERAL` (default 10, cap 20), a wall-clock budget, and per-call timeouts.
- AI conversation history is stored per user (`admin.ai_conversation`) and is readable/deletable only by its owner.
- The LLM provider is external and optional; `LLM_ENABLED=true` in the current local stack.

### B.8 Voice

- `POST /api/v1/voice/transcriptions` requires `appointment:write` (Doctor/Admin; Nurse denied).
- Upload cap: 4 MiB default, hard cap 8 MiB; duration cap: 60 seconds default (max 300 in config); rate limit: 12 requests/minute per user.
- Audio is written to a `TemporaryDirectory` with mode `0700` and fixed filenames; FFmpeg/FFprobe are invoked with argument lists (no shell) and timeouts; the directory is removed in `finally`.
- The speech service is private (`expose: 8080`, no published port) and runs whisper.cpp Base multilingual.
- Audio and transcripts are not persisted; operational events record language, provider, duration, status, and safe error codes, not transcript text.

### B.9 File storage

- The only user file upload is one optional message attachment per message.
- Bytes are stored in PostgreSQL (`admin.message_attachment.data`), not on a public filesystem or object store.
- Allowlist: PDF, PNG, JPEG, WebP, TXT, CSV, DOCX, XLSX. SVG/HTML/executables are rejected.
- Filename is reduced to a safe basename; magic-signature checks reject mismatched content; 5 MiB default cap; one attachment per message.
- Download is sender/recipient-only, served with `Content-Disposition: attachment`, `Cache-Control: no-store`, and global `nosniff`.
- No patient-document or medical-report upload feature exists.

### B.10 Docker and network

- `app` image runs as non-root `app` (uid 10001); `speech` runs as non-root `speech` (uid 10001); the PostgreSQL server process runs as `postgres` (uid 70).
- `speech` is not published; `app` and `db` are published on all host interfaces.
- Compose services share the default bridge network; no explicit `read_only`, `cap_drop`, or user namespace hardening in compose.
- Kubernetes: `db` Service is `ClusterIP`; `app` Service is `LoadBalancer` on 8081; app pods use `runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, and dropped capabilities. No `NetworkPolicy` manifests exist. The example ingress has no TLS block.

### B.11 Logging, audit, and backups

- Structured JSON logs with a redaction filter for password/token/secret/api_key/authorization/cookie/session fields; DSN credentials are scrubbed.
- Audit events store actor, action, resource type/id, outcome, request id, IP, and timestamp — no clinical payloads or request bodies.
- Audit retention is demo-lifetime; no automated deletion/erasure job.
- Backup procedure is documented (`pg_dump` + `age` encryption + `pg_restore` verification) but not automated; the compose volume is not encrypted at rest; no restore drill was performed.

---

## Section C — Vulnerability Findings

### C.1 Severity and confidence conventions

| Label | Meaning |
|---|---|
| **Critical** | Realistic authentication bypass, admin escalation, RCE, complete database compromise, or unauthorized patient data at scale. |
| **High** | Unauthorized patient/staff resource access, significant privilege escalation, sensitive disclosure, or authentication abuse. |
| **Medium** | Meaningful impact with additional conditions or limitations. |
| **Low** | Limited impact, defense-in-depth gap, or hardening opportunity. |
| **Informational** | Observation, configuration detail, or recommendation, not a vulnerability. |

**Confidence** reflects how strongly the evidence supports the finding. “Confirmed” means the behavior was observed in code or the live stack; “potential” means exploitation depends on conditions not fully verifiable here.

### C.2 Finding summary

| ID | Title | Severity | Confidence | Status |
|---|---|---|---|---|
| F-01 | PostgreSQL published on all interfaces with default superuser credentials and TLS off | High | High | Confirmed vulnerability |
| F-02 | All active demo accounts use the documented placeholder password; Admin has full permissions | High | High | Confirmed vulnerability |
| F-03 | Clinic-wide patient access; no care-relationship or tenant scope | Medium | High | Confirmed design limitation |
| F-04 | Clinical data sent to external LLM; provider safeguards unverified | Medium | High (data flow) / unverified (provider) | Potential risk |
| F-05 | No MFA or step-up authentication for staff/Admin | Medium | High | Confirmed control gap |
| F-06 | Login CSRF bootstrap exception allows first-request login forgery | Low | Medium | Confirmed weakness |
| F-07 | In-process rate limiting; credential-stuffing resistance is partial | Low | High | Confirmed limitation |
| F-08 | No malware/content-disarm scanning for message attachments | Low | High | Confirmed gap |
| F-09 | HSTS trusts client `X-Forwarded-Proto`; local stack is plain HTTP | Low | High (behavior) | Potential risk |
| F-10 | API docs/OpenAPI exposed in development; k8s config uses development | Low | High | Confirmed exposure |
| F-11 | Sensitive API responses lack `Cache-Control: no-store` | Low | High | Confirmed gap |
| F-12 | AI history, idempotency records, and audit logs have retention/PII gaps | Informational | High | Observation |
| F-13 | `APP_SECRET_KEY` is required but unused; no JWT | Informational | High | Observation |
| F-14 | Container/network hardening gaps | Informational | High | Observation |
| F-15 | Backup/restore is manual and unverified | Informational | High | Observation |
| F-16 | Local secret files exist untracked with broad WSL permissions | Informational | High | Observation |
| F-17 | No self-service password reset/change or registration/invites | Informational | High | Observation |

---

### F-01 — PostgreSQL published on all interfaces with default superuser credentials and TLS off

- **Severity:** High (Critical if the host is reachable from an untrusted network or if real data is loaded).
- **Confidence:** High.
- **Status:** Confirmed vulnerability.
- **Affected component:** PostgreSQL container, Docker Compose network exposure, application DB role.
- **Affected files/lines:** `docker-compose.yml:25-30` (default `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, published `5432:5432`), `docker-compose.yml:58-59` (app and migration URLs use the same role), `backend/app/config.py` (database URL is env-only).
- **Affected endpoint:** PostgreSQL `host:5432` (not an HTTP endpoint).

**Description.** The `db` service publishes port 5432 on `0.0.0.0` and IPv6, uses the documented compose default password, and creates the `app` role as a PostgreSQL superuser. The app and migration URLs both use that role. `SHOW ssl` returns `off`; `listen_addresses` is `*`. Remote `pg_hba.conf` uses `scram-sha-256`, but the default password is in use, so the authentication method does not help.

**Evidence.**

- `docker compose ps` showed `db 0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp`.
- `ss -ltn` showed `LISTEN *:5432`.
- A read-only `asyncpg` connection to `127.0.0.1:5432` using the documented compose default succeeded.
- `select current_user, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin from pg_roles where rolname=current_user` returned `app|t|t|t|t`.
- `show listen_addresses` returned `*`; `show ssl` returned `off`.
- `pg_hba.conf` remote rule: `host all all all scram-sha-256` (local/loopback rules use `trust` inside the container).
- The password value is **redacted**; it is the literal default at `docker-compose.yml:26`.

**Why it is dangerous.** A superuser can read, insert, update, delete, truncate, or drop every table; create roles; read server files where permitted; and install/execute server-side functions. The database holds patients, encounters, labs, prescriptions, messages, attachments, audit events, sessions, and AI history. If the host is reachable from a LAN or the internet, this is complete database compromise. The application also connects as that superuser role, so any future SQL-injection or application compromise has maximum blast radius.

**Required attack conditions.** Network reachability to the host’s port 5432; the default password unchanged (confirmed); no host firewall blocking the port (not verified). The current host is a WSL environment (`172.17.253.50`); external reachability depends on Windows port forwarding/firewall and was not tested.

**Safe reproduction / proof.** The checks above were read-only `SELECT`/`SHOW` queries and a connection test. No writes, schema changes, or destructive SQL were performed. The password is not recorded in this report.

**Expected secure behavior.** PostgreSQL should listen only on a private interface or loopback; the port should not be published in shared environments; the app should use a least-privilege role (no superuser, no CREATEROLE/CREATEDB); migrations should use a separate elevated role; TLS should be enabled; credentials should be unique and stored in a secret manager.

**Recommended remediation.**

1. Change the compose port mapping to `127.0.0.1:${POSTGRES_PORT:-5432}:5432` or remove it; use `docker compose exec`/a private network for local tooling.
2. Rotate the DB password and any credentials derived from it.
3. Create `app_rw` (LOGIN, no superuser, no CREATEROLE/CREATEDB) with grants only on the application schemas/sequences; create `migrator` for DDL; update `DATABASE_URL`/`MIGRATION_DATABASE_URL`.
4. Enable TLS (`ssl=on` with a server certificate) and require `hostssl` for remote connections; keep `scram-sha-256`.
5. Add a Kubernetes `NetworkPolicy`/compose network rule so only the app can reach the DB.
6. Add a startup check that refuses `rolsuper` for the runtime role in production.

**Independently verified:** Yes — live read-only checks.

---

### F-02 — All active demo accounts use the documented placeholder password; Admin has full permissions

- **Severity:** High (Critical if the app is reachable beyond an isolated demo or if real data is loaded).
- **Confidence:** High.
- **Status:** Confirmed vulnerability (intentional demo design, but a production blocker).
- **Affected component:** Seeded staff accounts and login.
- **Affected files/lines:** `.env.example:72-77` (demo emails and placeholder passwords), `scripts/seed_demo_data.py:80-82` and `802-816` (configured password used as-is when non-empty; random generation only when empty), `README.md:171-173` (role mapping), `docs/SECURITY.md:403-408` (public demo accounts are shared), `backend/app/api/v1/auth.py:38-125` (login).
- **Affected endpoint:** `POST /api/v1/auth/login`.

**Description.** The live database contains three active accounts: `admin@example.com` (`clinical_admin`), `doctor@example.com` (`doctor`), and `nurse@example.com` (`nurse`). All three argon2id hashes verify against the literal placeholder value documented in `.env.example` for `DEMO_*_PASSWORD`. The seed script does not reject that placeholder; it uses any non-empty configured password as-is. The Admin account holds all 17 permissions. There is no MFA.

**Evidence.**

- Read-only query of `admin.staff_user` joined to `admin.user_role`/`admin.role` returned exactly the three active accounts above with the expected roles.
- A local argon2id verification against the three active password hashes matched the documented placeholder for all three. Hashes and the password value are **redacted**; the value is the literal placeholder at `.env.example:73`, `:75`, and `:77`.
- No login request was sent during the audit, so no session or audit rows were created. Hash verification is sufficient to confirm the credential.
- `docs/SECURITY.md` states that public demo accounts are shared and provide no per-user accountability beyond audit records. The app is published on `0.0.0.0:8000`.

**Why it is dangerous.** Anyone who can reach the app and has read the repository can authenticate as Admin with a known password. They can read every patient, create/deactivate staff, assign roles, read audit/system logs, view analytics, and send messages. Because the accounts are shared, audit attribution is weak. If real data were present, this would be a critical patient-data and administrative compromise.

**Required attack conditions.** Network access to the app; knowledge of the documented emails and placeholder (public in the repository); no MFA (confirmed). One login attempt succeeds; no brute force is needed.

**Safe reproduction / proof.** Read `.env.example:72-77` and `README.md:171-173`; query the three active accounts and verify their hashes locally with argon2id. Do **not** send a login request in a production-like environment. No credentials are printed here.

**Expected secure behavior.** Each deployment should generate unique strong passwords even when the env var is the documented placeholder; the seed script should refuse placeholder values outside test; demo accounts should be disabled or isolated; Admin should require MFA; real deployments should use named accounts, not shared demo accounts.

**Recommended remediation.**

1. Rotate all three demo passwords immediately; do not reuse the documented placeholder.
2. Make `seed_demo_data.py` reject known placeholders (`replace-me…`, `change-me…`, empty) unless an explicit test flag is set.
3. Disable or rename the shared demo accounts in any non-demo environment; require per-user accounts.
4. Add MFA for Admin and clinical users (see F-05).
5. Restrict the demo to an isolated network and bind the app to loopback.
6. Add a deployment check that fails if a demo account password matches a documented placeholder.

**Independently verified:** Yes — read-only hash verification against the live database.

---

### F-03 — Clinic-wide patient access; no care-relationship or tenant scope

- **Severity:** Medium (High/Critical before real patient data).
- **Confidence:** High.
- **Status:** Confirmed design limitation (documented demo policy).
- **Affected component:** Patient-access seam, REST patient/lab/prescription reads, AI general lookup, MCP tools.
- **Affected files/lines:** `backend/app/services/access.py:3-4` and `35-50`; `backend/app/authorization/policy.py:9-23` and `100-128`; `backend/app/api/v1/patients.py:137-205` (list/detail), `256-442` (encounters/labs/prescriptions); `backend/app/mcp/tools.py:310-340` (shared authorization).
- **Affected endpoints:** `GET /api/v1/patients`, `GET /api/v1/patients/{id}`, `GET /api/v1/patients/{id}/summary`, `/encounters`, `/labs`, `/labs/trends`, `/prescriptions`, `GET /api/v1/encounters/{id}`, and AI/MCP reads.

**Description.** `check_patient_access` returns true for every patient when the principal holds `patient:read`. All three roles hold `patient:read`. There is no care-team, assignment, ward, consent, purpose, organization, or tenant check; there is no PostgreSQL row-level security. The docs explicitly describe this as a simplified synthetic-data policy and a production seam.

**Why it is dangerous.** In a real clinic, any Nurse, Doctor, or Admin could read any patient’s demographics, encounters, labs, and prescriptions, including patients outside their care. The AI general-lookup mode can span the entire authorized set, and the MCP tools use the same seam. This violates minimum-necessary/need-to-know expectations and would be a serious privacy issue with real PHI.

**Required attack conditions.** Any authenticated account with `patient:read` (all three roles). In the current demo, the shared Admin/Doctor/Nurse credentials make this trivial. No object-ID guessing is needed because the list endpoint returns all patients.

**Safe reproduction / proof.** Static review of the single access seam and route usage. A live `GET /patients/{id}` would write an audit event, so it was not executed during this read-only audit. The code path is unambiguous.

**Expected secure behavior.** Replace the demo body of `check_patient_access` with a query against a care-relationship/assignment table; keep the `False → 404` concealment contract. Add RLS or an equivalent tenant filter for production. Restrict AI general lookup to the same authorized set. Add tests proving a clinician cannot read a patient outside their care team.

**Recommended remediation.**

1. Add a `care_team`/`assignment`/`episode` relationship table with (user_id, patient_id) and effective dates.
2. Implement the check in `access.py`; keep the signature and 404 semantics.
3. Add tenant/organization keys if multi-tenant use is planned; consider PostgreSQL RLS.
4. Restrict `GET /patients` and AI/MCP search results to the authorized set.
5. Add negative tests for cross-patient access and AI tool scope.

**Independently verified:** Yes — code review; not actively exploited.

---

### F-04 — Clinical data sent to external LLM; provider safeguards unverified

- **Severity:** Medium (High with real PHI).
- **Confidence:** High that data is sent; provider retention/contract terms unverified.
- **Status:** Potential risk (conditional on real data and provider terms).
- **Affected component:** AI assistant, LLM provider, MCP tool results.
- **Affected files/lines:** `backend/app/llm/provider.py:1-20` and `171-178` (external base URL, `SecretStr` key), `backend/app/llm/loop.py:155-180` (tool results appended to model messages), `backend/app/llm/guardrails.py:177-230` (minimization), `docs/SECURITY.md:255-257` and `379-398`, `docs/AI_GUARDRAILS.md:78`.
- **Affected endpoint:** `POST /api/v1/ai/ask`.

**Description.** When the assistant is used, MCP tool results — patient summaries, lab results, prescriptions, encounter text, and names — are sent to the configured external provider as model context. The guardrails minimize payloads (drop MRN, date of birth, and several foreign keys) and validate output, but clinical content remains. The live local stack has `LLM_ENABLED=true`; the provider base URL is external (`https://opencode.ai/inference/go` by default). The repository states there are no BAAs/DPAs and that real patient data must not be sent; provider training, retention, residency, subprocessors, and incident terms are not established.

**Why it is dangerous.** PHI leaves the deployment trust boundary. If the provider retains or trains on the data, or if a subprocessor/region is unsuitable, this is a privacy/regulatory breach. The API key is server-side and redacted, but that does not address data processing.

**Required attack conditions.** Real patient data present; LLM enabled; a user asks a question that retrieves patient records. In the current demo, data is synthetic, so impact is limited.

**Safe reproduction / proof.** Static code review and configuration inspection. No real patient data or production secrets were sent to the external provider during this audit; no live AI calls were made.

**Expected secure behavior.** Use an approved provider with a DPA/BAA (as applicable), no-training/zero-retention terms, region controls, and audited subprocessors; minimize/redact data before sending; or run a local/on-prem model. Disable the LLM for real data until the data-protection review is complete.

**Recommended remediation.**

1. Complete a provider data-protection review (contract, retention, training, residency, subprocessors, breach notification).
2. Add a configuration gate that prevents `LLM_ENABLED=true` with real-data mode unless the provider is approved.
3. Strengthen minimization/redaction (names, free-text notes) or use a local model.
4. Log egress metadata (not payloads) and add a kill switch.
5. Document the data flow in the privacy notice.

**Independently verified:** Code path yes; provider terms **unable to verify**.

---

### F-05 — No MFA or step-up authentication for staff/Admin

- **Severity:** Medium.
- **Confidence:** High.
- **Status:** Confirmed control gap.
- **Affected component:** Authentication.
- **Affected files/lines:** `backend/app/api/v1/auth.py:38-125`; `backend/app/schemas/auth.py:15-26`; `docs/SECURITY.md:418` (“No MFA; password compromise is full account compromise”); no MFA/TOTP/WebAuthn/SSO implementation found.
- **Affected endpoint:** `POST /api/v1/auth/login`.

**Description.** Login is password-only. There is no TOTP, WebAuthn, SSO/OIDC, recovery-code, or step-up flow. Admin accounts can manage staff, roles, audit, and analytics with a single password.

**Why it is dangerous.** Phishing, password reuse, credential stuffing, or the default demo credentials (F-02) give full account access. Admin compromise is complete administrative compromise. MFA is a baseline control for clinical and administrative accounts.

**Required attack conditions.** Knowledge/guess of a valid password; no MFA. The shared demo credentials make this immediate.

**Safe reproduction / proof.** Static review of the login schema/handler and a repository-wide search for MFA/TOTP/WebAuthn/SSO found no implementation. No login attempts were made.

**Expected secure behavior.** Require MFA for Admin and clinical users; support WebAuthn/TOTP; add step-up for staff/role/audit/export operations; use SSO where available.

**Recommended remediation.**

1. Add TOTP or WebAuthn enrollment and verification after password login.
2. Require MFA for `clinical_admin` and offer it to all staff.
3. Add step-up re-authentication for role changes, staff deactivation, audit export, and bulk patient export.
4. Add recovery codes and an admin-assisted recovery process.
5. Consider SSO/OIDC for production.

**Independently verified:** Yes — absence confirmed.

---

### F-06 — Login CSRF bootstrap exception allows first-request login forgery

- **Severity:** Low.
- **Confidence:** Medium (logic confirmed; browser exploitation not executed).
- **Status:** Confirmed weakness / potential risk.
- **Affected component:** CSRF middleware, login.
- **Affected files/lines:** `backend/app/auth/csrf.py:6-14` (policy and bootstrap rationale), `79-90` (bootstrap branch), `backend/app/api/v1/auth.py:38-125` (login sets a session cookie).
- **Affected endpoint:** `POST /api/v1/auth/login`.

**Description.** When no `csrf_token` cookie exists at all, the middleware allows any non-GET request under `/api/v1` and issues a token. The comment argues that “a direct POST without any cookie cannot be a CSRF attack yet because the attacker cannot read or set the cookie.” That rationale covers forgery of an existing session, but not **login CSRF**: an attacker can submit a cross-site form POST to the real login endpoint with the attacker’s own credentials. The victim’s browser processes the `Set-Cookie` response and becomes logged in as the attacker. `SameSite=Lax` limits cookie sending on cross-site requests, but it does not prevent the login POST itself or the response’s `Set-Cookie`.

**Why it is dangerous.** A victim (e.g., a clinician) may unknowingly operate in the attacker’s account; data they view or enter can be visible to the attacker, and actions are misattributed. The impact is limited by the need to trick the victim into submitting the form and by the visible account identity, but it is a real CSRF gap.

**Required attack conditions.** Victim has no `csrf_token` cookie (first visit or cleared cookies); attacker has a valid account; victim is induced to submit a cross-site form POST to `/api/v1/auth/login` with the attacker’s credentials; the browser accepts the session cookie. No MFA would complicate this if added.

**Safe reproduction / proof.** Static review of the bootstrap branch and login response. No cross-site login forgery was executed.

**Expected secure behavior.** Require a CSRF token or a strict `Origin`/`Referer` check on login, even during bootstrap. Issue the CSRF cookie on `GET /api/v1/auth/me`/the login page and require the header on the login POST. Alternatively, exempt only a dedicated bootstrap endpoint that does not create a session.

**Recommended remediation.**

1. Add `Origin`/`Referer` validation for all non-GET `/api/v1` requests, including the no-cookie bootstrap case; reject mismatches.
2. Have the frontend bootstrap with `GET /api/v1/auth/me` (it already does) and require `X-CSRF-Token` on login.
3. Keep the anti-fixation session rotation (already implemented).
4. Add a regression test for cross-origin login POST without a token.

**Independently verified:** Logic confirmed; browser exploit not executed.

---

### F-07 — In-process rate limiting; credential-stuffing resistance is partial

- **Severity:** Low.
- **Confidence:** High.
- **Status:** Confirmed limitation.
- **Affected component:** Login, AI, voice, appointment interpretation rate limiters.
- **Affected files/lines:** `backend/app/auth/ratelimit.py:5-15` (in-memory per process), `24-25` (5 failures per email+IP, 50 per IP, 15-minute window), `backend/app/llm/limiter.py:21` (10 AI requests/min), `backend/app/services/appointment_ai.py:44` (10/min), `backend/app/services/voice.py:45` and `61` (12/min), `docs/SECURITY.md:422-425`.
- **Affected endpoints:** `POST /api/v1/auth/login`, `/ai/ask`, `/appointments/interpret`, `/voice/transcriptions`.

**Description.** Login allows 5 failures per (email, IP) and 50 per IP per 15 minutes. The counters are in-memory, per process, reset on restart, and not shared across workers/instances. There is no account-level lockout, CAPTCHA, or edge WAF in the repository. The current deployment is single-worker/single-replica, so the limiter is effective today; a multi-worker or scaled deployment bypasses it. Distributed credential stuffing across IPs is not stopped by per-IP limits, and no MFA is present.

**Why it is dangerous.** Automated password guessing and credential stuffing can proceed across IPs/instances; a restart clears counters. Argon2id raises the cost per attempt, but does not replace MFA or centralized limiting.

**Required attack conditions.** Distributed attack or multi-instance deployment; weak/reused passwords; no MFA. No brute-force test was performed.

**Safe reproduction / proof.** Static review of the limiter and configuration; docs explicitly call the limits in-process. No login attempts were made.

**Expected secure behavior.** Use a shared store (Redis/DB) for rate limits and lockouts; add account-level anomaly detection, MFA, CAPTCHA, and edge WAF/DDoS protection; monitor failed-login patterns.

**Recommended remediation.**

1. Move counters to Redis or a DB table with atomic increments and TTLs.
2. Add progressive account/IP backoff and alerting.
3. Require MFA (F-05).
4. Add CAPTCHA or proof-of-work after repeated failures.
5. Verify client-IP attribution behind the production proxy.

**Independently verified:** Yes — code review.

---

### F-08 — No malware/content-disarm scanning for message attachments

- **Severity:** Low.
- **Confidence:** High.
- **Status:** Confirmed gap.
- **Affected component:** Message attachments.
- **Affected files/lines:** `backend/app/services/message_attachments.py:1-10` (explicitly “not antivirus scanning”), `35-45` (allowlist), `52-120` (filename/magic checks), `backend/app/api/v1/messages.py:343-400` (upload), `403-445` (download); `docs/SECURITY.md:59`.
- **Affected endpoint:** `POST /api/v1/messages/attachments`.

**Description.** Attachments are allowlisted and magic-checked, but not scanned for malware or disarmed. An authenticated staff member can send a malicious PDF/DOCX/XLSX to a colleague. Files are served as attachments with `nosniff`/`no-store` and are never rendered inline, which reduces browser exploitation but does not protect the recipient who opens the file.

**Why it is dangerous.** Internal phishing/malware delivery within a clinical environment; a compromised staff account could target administrators. The attack surface is limited to authenticated staff and allowlisted types.

**Required attack conditions.** Authenticated staff account; recipient downloads and opens the file; endpoint is not public.

**Safe reproduction / proof.** Static review. No malicious file was uploaded.

**Expected secure behavior.** Scan uploads with an antivirus/CDR service, quarantine failures, block macro-enabled documents, and warn users. Keep the allowlist and attachment-only serving.

**Recommended remediation.**

1. Integrate ClamAV or a cloud malware-scanning service in `prepare_attachment`.
2. Add content-disarm for PDFs/Office files or block macro-enabled types.
3. Quarantine and audit scan failures.
4. Display a user warning for external/untrusted attachments.

**Independently verified:** Yes — absence confirmed.

---

### F-09 — HSTS trusts client `X-Forwarded-Proto`; local stack is plain HTTP

- **Severity:** Low (deployment-dependent).
- **Confidence:** High for header behavior.
- **Status:** Potential risk.
- **Affected component:** Security headers, cookie `Secure` decision, TLS termination.
- **Affected files/lines:** `backend/app/main.py:307-317` (`request_is_https` reads `X-Forwarded-Proto` directly), `338-339` (HSTS only when `https`), `backend/app/auth/cookies.py:26-31` (Secure when production/HTTPS), `docker-compose.yml:64-65` (app published over HTTP), `k8s/configmap.yaml:21-25` (`APP_ENV=development`), `frontend/_headers` (edge HSTS).
- **Affected endpoints:** All HTTP responses.

**Description.** HSTS is emitted only when `request_is_https` is true. That function returns true for `X-Forwarded-Proto: https` without checking whether the request came from a trusted proxy. A client sending that header over plain HTTP receives `Strict-Transport-Security` (verified with `curl`); a client sending `X-Forwarded-Proto: http` over HTTPS can suppress it if the proxy forwards the header. HSTS over plain HTTP is ignored by browsers, but the trust behavior is still incorrect. The local compose stack serves the app and DB over plain HTTP/TCP; `APP_ENV=development` means cookies are not `Secure` on HTTP.

**Why it is dangerous.** If the app is exposed beyond localhost without TLS, session cookies and credentials travel in cleartext. If a proxy forwards client-supplied `X-Forwarded-Proto`, an attacker can suppress HSTS and increase SSL-stripping risk. The current local stack is explicitly development, but the same code is used in shared environments.

**Required attack conditions.** Non-local deployment without TLS, or a proxy that trusts client-supplied forwarded headers; network position to observe traffic or manipulate headers.

**Safe reproduction / proof.** `curl -H 'X-Forwarded-Proto: https' http://localhost:8000/` returned `Strict-Transport-Security`; without the header, no HSTS. `docker compose ps` shows HTTP app and DB ports. No traffic interception was performed.

**Expected secure behavior.** Terminate TLS at a trusted proxy/load balancer; only honor forwarded headers from known proxy IPs; strip/overwrite client-supplied `X-Forwarded-Proto`; set `APP_ENV=production` and `SESSION_COOKIE_SECURE=true` behind TLS; use `__Host-` cookies where possible.

**Recommended remediation.**

1. Configure TLS termination and redirect HTTP to HTTPS.
2. Add trusted-proxy configuration; ignore forwarded headers from untrusted peers.
3. Set `SESSION_COOKIE_SECURE=true` in production and verify `Secure`/`HttpOnly`/`SameSite`.
4. Bind the local app/DB to loopback.
5. Add an integration test for spoofed forwarded headers.

**Independently verified:** Header behavior yes; deployment impact conditional.

---

### F-10 — API docs/OpenAPI exposed in development; k8s config uses development

- **Severity:** Low.
- **Confidence:** High.
- **Status:** Confirmed exposure.
- **Affected component:** FastAPI docs/OpenAPI, CSP docs variant, Kubernetes config.
- **Affected files/lines:** `backend/app/config.py:244-247` (`docs_enabled` defaults to true outside production), `backend/app/main.py:245` and `327` (docs path prefixes and relaxed CSP), `k8s/configmap.yaml:21-25` (`APP_ENV=development`, `/docs` intentionally open for the demo), live `GET /openapi.json` and `/docs`.
- **Affected endpoints:** `/docs`, `/redoc`, `/openapi.json`.

**Description.** The current local stack runs with `APP_ENV=development`; `/docs` and `/openapi.json` return 200 without authentication. The docs CSP relaxes `script-src`/`style-src` to allow `https://cdn.jsdelivr.net` and `unsafe-inline` for those paths. The k8s configmap also sets `APP_ENV=development` so the local demo keeps docs open.

**Why it is dangerous.** API schema disclosure helps attackers map endpoints, parameters, and permissions. The docs page loads a third-party CDN script in development; if docs are enabled in a shared environment, that expands the supply-chain surface. No patient data is exposed directly by the schema.

**Required attack conditions.** A non-production/shared deployment with docs enabled; network access to the app.

**Safe reproduction / proof.** `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/openapi.json` → `200`; `/docs` → `200`. `docker compose exec app printenv APP_ENV` → `development`.

**Expected secure behavior.** Disable docs/OpenAPI in shared and production environments (`APP_ENV=production` or `API_DOCS_ENABLED=false`); self-host Swagger UI assets if docs are needed; restrict docs by network/auth.

**Recommended remediation.**

1. Set `API_DOCS_ENABLED=false` in any shared deployment.
2. Keep `APP_ENV=production` behind TLS.
3. Self-host docs assets or remove the CDN allowance.
4. Add a deployment check that docs return 404 in production.

**Independently verified:** Yes — live HTTP checks.

---

### F-11 — Sensitive API responses lack `Cache-Control: no-store`

- **Severity:** Low.
- **Confidence:** High.
- **Status:** Confirmed gap.
- **Affected component:** API response headers.
- **Affected files/lines:** `backend/app/main.py:321-339` (security headers do not include `Cache-Control` for API responses); `backend/app/main.py:455-466` (static assets get cache headers); `backend/app/api/v1/messages.py:435-443` (attachment download explicitly sets `no-store`); `frontend/assets/js/api.js:158` (client fetch uses `cache: 'no-store'`).
- **Affected endpoints:** All authenticated API GETs, including `/patients`, `/patients/{id}`, `/patients/{id}/summary`, `/appointments`, `/messages`, `/ai/conversations`, `/audit`, `/system-logs`.

**Description.** Authenticated API responses do not send `Cache-Control: no-store` (or `Pragma: no-cache`). The frontend’s `fetch(..., { cache: 'no-store' })` reduces browser caching, and the attachment endpoint explicitly sets `no-store`, but the server-side contract does not protect other clients, proxies, or browser implementations. Sensitive patient/AI/message data could be retained by an intermediary or client cache.

**Why it is dangerous.** Patient data, messages, audit rows, and AI answers are sensitive; caching can extend their lifetime and expose them on shared devices/proxies. The risk is lower because responses are authenticated and the app is single-origin/HTTPS in production, but the header gap is a defense-in-depth failure.

**Required attack conditions.** A cache/proxy that stores responses without `no-store`; access to the cache or a shared device; authenticated API GET. No cache was tested.

**Safe reproduction / proof.** `curl -D -` on `/api/v1/auth/me`, `/health/ready`, and `/api/v1/patients` showed no `Cache-Control` header (401/200 as applicable). The attachment endpoint sets `no-store` in code. No authenticated patient response was fetched.

**Expected secure behavior.** Add `Cache-Control: no-store` (and `Pragma: no-cache` for legacy clients) to all `/api/v1` responses that carry user/patient data; keep static assets cacheable; keep `no-store` on downloads and auth responses.

**Recommended remediation.**

1. Add a middleware/header rule: `Cache-Control: no-store` for `/api/v1/*`, except explicitly cacheable public metadata if any.
2. Keep `no-store` on login, `/auth/me`, patient, message, AI, audit, and system-log responses.
3. Verify with `curl -D -` in CI or an integration test.
4. Keep the frontend `cache: 'no-store'`.

**Independently verified:** Yes — live header check on unauthenticated endpoints + code review.

---

### F-12 — AI history, idempotency records, and audit logs have retention/PII gaps

- **Severity:** Informational.
- **Confidence:** High.
- **Status:** Observation (privacy/data-lifecycle).
- **Affected component:** AI conversation history, idempotency records, audit/operational logs.
- **Affected files/lines:** `backend/app/models/ai.py:25-49` (question/answer stored per user), `backend/app/api/idempotency.py:1-25` and `218-224` (24-hour response body retention; AI answers may be stored), `backend/app/api/v1/auth.py:50-80` (failed-login email/IP logged), `backend/app/audit/logger.py:22-23` (attempted email as resource reference), `docs/SECURITY.md:313-315` and `419` (demo-lifetime audit retention; no automated erasure).
- **Affected endpoints:** `POST /api/v1/ai/ask`, `GET /api/v1/ai/conversations`, `POST /api/v1/auth/login`, `GET /api/v1/audit`.

**Description.** AI questions and answers are stored in `admin.ai_conversation` per user; idempotency records can store the AI response body for 24 hours; failed-login logs and audit rows can contain the attempted email and IP. Audit retention is demo-lifetime, with no automated deletion/erasure. The live database had 8,944 audit events at audit time. These are expected for a demo but require a retention/erasure policy before real data.

**Why it matters.** Patient-linked AI content and PII persist beyond the request; retention/erasure obligations and log-access controls become important. The audit endpoint is Admin-only, but there is no tamper-evident storage.

**Required attack conditions.** Access to the database or Admin audit/log APIs; real data; retention obligations. Not a direct exploit.

**Safe reproduction / proof.** Static review and read-only counts. No data was deleted or modified.

**Expected secure behavior.** Define retention periods and automated deletion/erasure; minimize PII in logs (pseudonymize attempted emails); make audit storage append-only/tamper-evident; document AI-history retention and user deletion.

**Recommended remediation.**

1. Add retention jobs for audit, AI history, idempotency, and operational logs.
2. Pseudonymize/hash attempted emails in logs; keep only what is needed for security monitoring.
3. Add tamper-evident audit storage (append-only/WORM or hash chain).
4. Document retention/erasure in the privacy notice.
5. Add user-facing deletion for AI history (already available per conversation; add retention).

**Independently verified:** Yes — code and read-only DB counts.

---

### F-13 — `APP_SECRET_KEY` is required but unused; no JWT

- **Severity:** Informational.
- **Confidence:** High.
- **Status:** Observation (configuration/documentation hygiene).
- **Affected component:** Configuration and session architecture.
- **Affected files/lines:** `backend/app/config.py:50` and `63` (required `app_secret_key`), `backend/app/auth/sessions.py:1-20` and `40-50` (opaque 32-byte tokens, SHA-256 at rest), repository-wide search found no application use of `app_secret_key`.
- **Affected endpoint:** N/A.

**Description.** `APP_SECRET_KEY` is a required setting, and docs describe it as signing/secret material, but the current application does not use it. Sessions are opaque random tokens stored as SHA-256 hashes; there is no JWT. The “weak JWT signing secret” attack vector is therefore not applicable. There is also no minimum-length validation for `APP_SECRET_KEY` in code.

**Why it matters.** A required-but-unused secret creates operational confusion and a false sense of cryptographic protection; a future feature could start using a weak value. It is not a vulnerability today.

**Safe reproduction / proof.** Repository-wide grep for `app_secret_key`/`APP_SECRET_KEY` shows config, tests, docs, and CI only; no runtime use. Session code uses `secrets.token_urlsafe` and `hashlib.sha256`.

**Expected secure behavior.** Either use the key for a defined purpose (with a documented rotation and length policy) or remove it from required configuration; update docs to match.

**Recommended remediation.**

1. Remove `APP_SECRET_KEY` from required settings if unused, or document its exact future purpose.
2. If used later, enforce ≥32 random bytes and rotation.
3. Correct the docs’ “signing key” wording.
4. Keep the opaque-session design.

**Independently verified:** Yes — code search.

---

### F-14 — Container/network hardening gaps

- **Severity:** Informational.
- **Confidence:** High.
- **Status:** Observation (defense in depth).
- **Affected component:** Docker Compose, Kubernetes, speech service.
- **Affected files/lines:** `backend/Dockerfile:66` (`USER app`), `speech/Dockerfile:70` (`USER speech`), `docker-compose.yml` (no `read_only`, `cap_drop`, `security_opt`, or explicit network), `k8s/app-deployment.yaml:34-36` and `72-76` (app hardening), `k8s/postgres.yaml:23` (ClusterIP), `115-121` (allowPrivilegeEscalation false; no runAsNonRoot/cap drop for Postgres), no `NetworkPolicy` manifests.
- **Affected endpoints:** Infrastructure.

**Description.** The app and speech processes run as non-root, and speech is not published. The PostgreSQL server process runs as `postgres`. However, compose does not drop capabilities, set a read-only root filesystem, or define network segmentation; Kubernetes has no NetworkPolicy; the Postgres pod has only modest hardening because of first-time initialization needs; and the speech service uses `WHISPER_AUTH_MODE=none` (private network only).

**Why it matters.** If the app is compromised, it can reach the DB and speech service on the shared network; container escape/privilege escalation impact is higher without capability drops/read-only filesystems. These are defense-in-depth gaps, not direct vulnerabilities in the current demo.

**Safe reproduction / proof.** `docker top`/`docker inspect` showed app uid 10001, speech uid 10001, DB process uid 70; compose/K8s manifests reviewed. No container escape or network scan was performed.

**Expected secure behavior.** Drop all capabilities, use read-only root filesystems where possible, add seccomp/AppArmor, segment networks, add Kubernetes NetworkPolicies, require auth/TLS for speech, and run the DB as a non-root user with a restricted service account.

**Recommended remediation.**

1. Add `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, and read-only root filesystems (with tmpfs mounts) to compose.
2. Add NetworkPolicies: app → db/speech only; no ingress to db/speech from other pods.
3. Add speech service authentication or mTLS even on the private network.
4. Review Postgres pod securityContext after initialization.
5. Add image scanning/SBOM in CI.

**Independently verified:** Yes — container/manifest inspection.

---

### F-15 — Backup/restore is manual and unverified

- **Severity:** Informational.
- **Confidence:** High.
- **Status:** Observation (operational resilience).
- **Affected component:** PostgreSQL backup/restore.
- **Affected files/lines:** `docs/DEPLOYMENT.md:269-304` (manual `pg_dump` + `age` + `pg_restore` procedure), `docs/DEPLOYMENT.md:320` (no PITR on free tier), `docker-compose.yml:31-32` (named volume), `docs/SECURITY.md:405` (backups/PITR listed as production hardening).
- **Affected endpoints:** N/A.

**Description.** The repository documents an encrypted backup procedure, but there is no automation, schedule, retention, or restore drill. The compose volume is not encrypted at rest; the production docs use a free-tier managed database with limited backups and no PITR. Attachment bytes are in PostgreSQL, so a DB backup covers them, but a lost volume without a backup loses everything.

**Why it matters.** Data loss/availability and compliance risk; a backup that has never been restored is not proven. Not a direct security vulnerability.

**Safe reproduction / proof.** Documentation review; no backup or restore was executed (would create artifacts/load).

**Expected secure behavior.** Automated encrypted backups with retention, off-host storage, separate keys, PITR where available, and regular restore drills with documented RTO/RPO.

**Recommended remediation.**

1. Automate `pg_dump`/WAL archiving and encryption.
2. Store backups off-host with a separate key/account.
3. Run scheduled restore drills into an isolated environment.
4. Document retention and deletion.
5. Add monitoring/alerts for backup failures.

**Independently verified:** Documentation only; restore **unverified**.

---

### F-16 — Local secret files exist untracked with broad WSL permissions

- **Severity:** Informational.
- **Confidence:** High.
- **Status:** Observation (local environment hygiene).
- **Affected component:** Local env/secret files.
- **Affected files/lines:** `backend/.env` (gitignored; contains a configured LLM key and placeholder demo passwords; values redacted), `k8s/secret.yaml` (gitignored via `k8s/.gitignore:2`; generated values; redacted), `.gitignore:2`, `.dockerignore` (env files excluded from the image), `scripts/k8s/gen-secret.sh` (writes mode 600).
- **Affected endpoints:** N/A.

**Description.** Local secret files exist and are correctly untracked/gitignored; the tracked-file secret scan passed with 0 findings. On this WSL mount, both files display mode `0777` (a DrvFs artifact); `gen-secret.sh` intends mode 600. The values were not printed in this report.

**Why it matters.** If the host is shared or the files are copied to a less restrictive location, local secrets (LLM key, DB/app secrets, demo passwords) could be exposed. Not a repository vulnerability; a workstation hygiene issue.

**Safe reproduction / proof.** `git check-ignore` confirmed both files are ignored; `git ls-files` does not include them; `python3 scripts/security_check.py` covered 242 tracked files in scope (229 files scanned) and reported 0 findings. File modes were inspected without reading values.

**Expected secure behavior.** Keep secrets in a secret manager or mode-600 files outside synced/shared folders; rotate if exposed; never commit; verify permissions on a native Linux filesystem.

**Recommended remediation.**

1. Verify `chmod 600 backend/.env k8s/secret.yaml` on a native Linux filesystem.
2. Move secrets to a manager/CI secret store for shared environments.
3. Rotate the LLM/DB/app secrets if the workstation was shared.
4. Keep the tracked-file scan and `.dockerignore` rules.

**Independently verified:** Yes — ignore rules/scan; values redacted.

---

### F-17 — No self-service password reset/change or registration/invites

- **Severity:** Informational.
- **Confidence:** High.
- **Status:** Observation (operational gap; reduces some attack surface).
- **Affected component:** Account lifecycle.
- **Affected files/lines:** `backend/app/api/v1/staff.py:122-189` (Admin-only staff creation; optional password or one-time generated password), `backend/app/schemas/staff.py:35-45`, repository-wide search found no registration, invitation, email verification, account linking, password reset, or self-service password change endpoints.
- **Affected endpoints:** `POST /api/v1/staff` (Admin only); no public registration/reset.

**Description.** Accounts are created by an Admin; a missing password generates a random one returned once. There is no self-service password reset/change, registration, invitation, email verification, or account linking. This eliminates the classic account-pre-hijacking and password-reset-poisoning attack surface, but it is an operational gap: users cannot recover or rotate their own passwords, and Admin-mediated recovery may be manual/insecure.

**Why it matters.** Operationally, account recovery depends on an Admin and may lead to weak shared processes. If a reset flow is added later, it must be designed securely (single-use tokens, expiry, Host/forwarded-header validation, no user enumeration).

**Safe reproduction / proof.** Repository-wide search; route inventory. No account changes were made.

**Expected secure behavior.** If self-service is added: cryptographically random single-use reset tokens, short expiry, hashed at rest, fixed canonical URL from configuration (never Host header), uniform responses, rate limiting, and audit. Alternatively, use SSO.

**Recommended remediation.**

1. Decide whether self-service password change/reset is required; if not, document the Admin-assisted process and its identity verification.
2. If added, use a fixed `PUBLIC_ORIGIN`, not the request Host header, for reset links.
3. Add rate limiting, token expiry/single-use, and uniform responses.
4. Add MFA/SSO (F-05).

**Independently verified:** Yes — absence confirmed.

---

### C.3 Verified security controls

| Control | Evidence | Assessment |
|---|---|---|
| Password hashing | `backend/app/auth/passwords.py:1-40`; argon2id defaults; dummy hash for unknown accounts | Verified |
| Uniform login failures | `backend/app/api/v1/auth.py:60-90`; same 401 body; constant-shape verification | Verified |
| Session token storage | `backend/app/auth/sessions.py:40-50`; 32 random bytes; SHA-256 at rest | Verified |
| Session expiry/revocation | `sessions.py:85-125` (expiry/inactive/soft-delete), `140-162` (revoke); logout/deactivation | Verified |
| Anti-fixation | `auth.py:97-105` revokes the pre-login session before creating a new one | Verified |
| Cookie flags | `backend/app/auth/cookies.py:39-76`; `HttpOnly`, `SameSite=Lax`, `Secure` in production/HTTPS | Verified (dev HTTP omits Secure by design) |
| CSRF | `backend/app/auth/csrf.py`; double-submit on `/api/v1`; bootstrap caveat F-06 | Verified with caveat |
| Server-side RBAC | `backend/app/authorization/policy.py:100-210`; every route uses `require_permission` | Verified |
| Self-role guard | `backend/app/api/v1/staff.py:277-288`; 403 on self role assignment | Verified |
| Last-admin guard | `staff.py:203-205`; `ensure_not_last_active_admin`; 409 | Verified |
| Patient-access seam | `backend/app/services/access.py:35-50`; single function; 404 concealment | Verified (demo policy F-03) |
| Message ownership | `backend/app/api/v1/messages.py:245-300` (caller-scoped list), `448-470` (recipient-only read) | Verified |
| Attachment ownership | `messages.py:403-445`; sender/recipient-only; 404 otherwise | Verified |
| Attachment validation | `backend/app/services/message_attachments.py:35-120`; allowlist, magic, 5 MiB, safe basename | Verified |
| Appointment doctor scoping | `backend/app/api/v1/appointments.py:76-100`, `778-900`; doctor own calendar, admin any, nurse read-only | Verified |
| Appointment concurrency | `backend/app/services/appointments.py:295` advisory locks; `321-331` overlap re-check | Verified |
| AI tool allowlist | `backend/app/mcp/server.py:20-40`, `362-400`; seven read-only tools | Verified |
| AI re-authorization | `backend/app/mcp/tools.py:310-340`, `890-916`; per-call permission + patient access | Verified |
| AI output validation | `backend/app/llm/guardrails.py:276-330`; rejects secrets/HTML/URLs/unapproved UUIDs | Verified |
| AI data minimization | `guardrails.py:177-230`; drops MRN/DOB/foreign keys | Verified |
| AI history ownership | `backend/app/api/v1/ai.py:323-440`; per-user queries | Verified |
| Voice authorization/bounds | `backend/app/api/v1/voice.py:28-50`; `backend/app/services/voice.py:45-61`, `88-106`, `435-476` | Verified |
| SQL injection prevention | ORM/parameterized; only `SELECT 1` raw SQL in `backend/app/api/health.py:31` | Verified |
| XSS prevention | `frontend/assets/js/markdown.js:1-10`; text nodes; `ui.js:34` trusted constants; `messages.js`/`patient.js` textContent | Verified |
| Clickjacking | `backend/app/main.py:329-336`; `X-Frame-Options: DENY`, `frame-ancestors 'none'`; live headers | Verified |
| CORS | `backend/app/main.py:682-688`; explicit origins; evil-origin preflight denied | Verified |
| Path traversal | `backend/app/main.py:408-450`; resolve + `relative_to`; traversal probes 404 | Verified |
| Secret redaction | `backend/app/config.py:23-32`, `288-347`; structured log filter | Verified |
| Tracked-file secret scan | `python3 scripts/security_check.py`; 242 tracked files in scope (229 scanned); 0 findings | Verified |
| Container non-root | `backend/Dockerfile:66`; `speech/Dockerfile:70`; live uid 10001 | Verified |
| Private speech service | `docker-compose.yml:100-102`; `expose` only; no published port | Verified |
| CI dependency audit | `.github/workflows/ci.yml:101-134`; `pip-audit` high/critical gate | Configured; not run in this audit |

---

## Section D — Reference Attack Vector Checklist

| # | Attack vector | Status | Evidence / notes |
|---:|---|---|---|
| 1 | Next.js header disclosure | **NOT APPLICABLE** | No Next.js. FastAPI + static HTML/JS; no Next.js headers or server components. |
| 2 | Exposed `.git` directory | **NOT VULNERABLE — VERIFIED** | Static root is `frontend/`; `/.git/config`, `/.env`, `/backend/.env`, and encoded traversal probes returned 404; `frontend_response` rejects `..` and resolves under the frontend root (`backend/app/main.py:408-450`). |
| 3 | Supabase storage exposure | **NOT APPLICABLE** | No Supabase. Attachments are PostgreSQL `bytea`; no public object URLs. |
| 4 | Client-side price/value manipulation | **NOT APPLICABLE** | No payments/prices. Security-sensitive IDs/roles/status are validated server-side; client-supplied identity is ignored (session-derived `Principal`). |
| 5 | Credential stuffing | **POTENTIAL RISK** | Argon2id + uniform errors + rate limits, but no MFA and in-process limits (F-05, F-07); shared demo credentials (F-02) make it moot in the demo. |
| 6 | Publicly exposed database | **CONFIRMED VULNERABILITY** | F-01: `0.0.0.0:5432`, default superuser password, TLS off. |
| 7 | Third-party JavaScript supply-chain / Polyfill.io-style | **NOT VULNERABLE — VERIFIED** | No third-party scripts in app pages; buildless, self-hosted; CSP `script-src 'self'` + hash; `scripts/security_check.py` frontend hygiene passes. Docs page uses `cdn.jsdelivr.net` only in development (F-10). |
| 8 | SMS pumping | **NOT APPLICABLE** | No SMS functionality. |
| 9 | AI prompt injection | **POTENTIAL RISK** | Strong boundary: data/instruction separation, seven read-only tools, server-injected identity, per-call re-authorization, output validation (Section F). Residual: model can be influenced to produce misleading content; no live red-team. |
| 10 | Weak JWT signing secrets | **NOT APPLICABLE** | No JWT. Opaque 32-byte session tokens; SHA-256 at rest. `APP_SECRET_KEY` is unused (F-13). |
| 11 | Race conditions | **NOT VULNERABLE — VERIFIED (appointments)** | Advisory xact locks for doctor/patient + overlap re-check (`services/appointments.py:295`, `321-331`); sorted lock keys. Other workflows were not stress-tested. |
| 12 | Account pre-hijacking | **NOT APPLICABLE** | No self-registration, invitations, email verification, account linking, or password reset. Admin-only staff creation (F-17). |
| 13 | Subdomain takeover | **UNABLE TO VERIFY** | No DNS/hosting configuration in the repository; production domains are placeholders. No subdomains are referenced beyond `example.com` mailto links. |
| 14 | SSRF | **NOT VULNERABLE — VERIFIED** | No user-supplied URL fetch. Outbound calls go only to env-configured LLM/Whisper base URLs; no redirect-following or arbitrary URL parameters. |
| 15 | Path traversal | **NOT VULNERABLE — VERIFIED** | Static path resolve/`relative_to`; attachment bytes in DB; filename sanitized; live traversal probes 404. |
| 16 | Password reset poisoning | **NOT APPLICABLE** | No password reset flow. If added, use a fixed `PUBLIC_ORIGIN`, never the Host header (F-17). |
| 17 | Exposed source maps | **NOT VULNERABLE — VERIFIED** | Buildless frontend; no `.map` files; static server serves only `frontend/`. |
| 18 | Clickjacking | **NOT VULNERABLE — VERIFIED** | `X-Frame-Options: DENY` and `frame-ancestors 'none'`; live headers confirmed. |
| 19 | Open redirects | **NOT VULNERABLE — VERIFIED** | No redirect endpoints or `next`/`return_to` parameters; frontend redirects to fixed `index.html` (`auth.js:96,109`). |
| 20 | User enumeration | **NOT VULNERABLE — VERIFIED (public)** | Login uses uniform 401 + dummy hash; no registration/reset. `/messages/recipients` requires auth and returns only active staff display names/roles (no emails), by design for internal messaging. |

### D.1 Additional vectors

| Vector | Status | Evidence / notes |
|---|---|---|
| SQL injection | **NOT VULNERABLE — VERIFIED** | SQLAlchemy ORM/parameterized; only `SELECT 1` raw SQL. |
| Command injection | **NOT VULNERABLE — VERIFIED** | FFmpeg/FFprobe via `create_subprocess_exec` with fixed args, no shell, timeouts; no user-controlled binary/path. |
| Template injection | **NOT APPLICABLE** | No server-side template engine; static HTML. |
| Header injection | **NOT VULNERABLE — VERIFIED** | Request-ID pattern; attachment filename sanitized and RFC 6266 encoded; no user input in response headers. |
| CSRF | **PARTIAL** | Double-submit + CORS on `/api/v1`; bootstrap login CSRF caveat F-06. |
| XSS | **NOT VULNERABLE — VERIFIED** | Safe Markdown renderer, textContent, CSP, no third-party scripts; AI output validated. |
| CORS | **NOT VULNERABLE — VERIFIED** | Explicit origins, credentials true, no wildcard; evil-origin preflight denied. |
| IDOR / BOLA | **PARTIAL** | Role-level checks enforced; messages/appointments/attachments/AI history scoped. Patient-level access is clinic-wide by design (F-03). No other IDOR found. |
| Broken authentication | **PARTIAL** | Strong sessions/argon2; default demo credentials (F-02), no MFA (F-05), login CSRF (F-06), partial rate limits (F-07). |
| Sensitive data exposure | **PARTIAL** | Secrets redacted/tracked scan clean; local untracked secrets (F-16); PII/retention (F-12); external LLM (F-04); public DB (F-01). |
| Insecure deserialization | **NOT APPLICABLE** | JSON only; no pickle/YAML/object deserialization in request paths. |
| DoS / resource consumption | **PARTIAL** | Body limits (1 MiB global, 4/5/8 MiB route caps), AI/voice/appointment limits, argon2 cost; in-process limits, no WAF (F-07). |
| Operational security | **PARTIAL** | Structured logs, audit events, redaction; no tamper-evident audit, retention, monitoring/on-call, backup automation (F-12, F-15). |
| Environment separation | **PARTIAL** | `APP_ENV` + production docs; k8s config uses development (F-10); local secrets (F-16). |
| Debug mode | **VERIFIED (not enabled in Docker)** | Uvicorn command has no `--reload`; FastAPI debug not enabled; docs enabled only in development (F-10). |
| Security monitoring | **PARTIAL** | Audit + system-log UI; no SIEM/alerting/on-call in repo. |

---

## Section E — RBAC Security Matrix

### E.1 Resource/operation matrix

Legend: ✅ allowed; ❌ denied; ⚠️ allowed with a documented scope/condition. “Backend enforcement” names the server-side check. Frontend visibility is **not** a control.

| Resource / operation | Doctor | Nurse | Admin | Backend enforcement |
|---|:---:|:---:|:---:|---|
| Patients list / read / summary / encounters | ✅ | ✅ | ✅ | `patient:read` + `check_patient_access` (demo: all patients, F-03) |
| Patient create | ✅ | ❌ | ✅ | `patient:write` |
| Patient delete | ❌ | ❌ | ✅ | `patient:delete` |
| Encounter detail | ✅ | ✅ | ✅ | `patient:read` + `check_patient_access(encounter.patient_id)` |
| Labs / trends | ✅ | ✅ | ✅ | `lab:read` + `check_patient_access` |
| Prescriptions | ✅ | ✅ | ✅ | `prescription:read` + `check_patient_access` |
| Messages inbox/sent/counts | ✅ | ✅ | ✅ | `message:read` + caller-scoped queries |
| Message recipients | ✅ | ✅ | ✅ | `message:read`; active staff with `message:read`; display name/roles only |
| Send message | ✅ | ✅ | ✅ | `message:write`; recipient must hold `message:read` |
| Send/download attachment | ✅ | ✅ | ✅ | `message:write`/`message:read`; sender/recipient-only; 404 otherwise |
| Mark message read | ✅ | ✅ | ✅ | `message:read`; recipient-only |
| Appointments config/list/slots/history/detail | ⚠️ own calendar | ⚠️ clinic-wide read | ⚠️ clinic-wide read | `appointment:read` + `_resolve_read_doctor_id` (doctor own; nurse/admin may name a doctor) |
| Appointment create/update/cancel | ⚠️ own calendar | ❌ | ✅ | `appointment:write` + `_resolve_doctor_id` + patient access |
| Availability read | ⚠️ own | ⚠️ requires doctor_id | ⚠️ requires doctor_id | `appointment:read` + `_resolve_read_doctor_id` |
| Availability write | ⚠️ own | ❌ | ✅ | `appointment:manage_availability` |
| Appointment AI interpretation (`/appointments/interpret`) | ✅ | ❌ | ✅ | `appointment:write`; proposal only; no write until explicit `POST /appointments` with `appointment:write` |
| Patient name/MRN suggestions (`/appointments/patient-suggestions`) | ✅ | ✅ | ✅ | `patient:read`; deterministic read-only suggestions; no transcript logging |
| Appointment CSV export | ⚠️ own calendar | ❌ | ✅ | `appointment:export` + scoping + patient access |
| Voice transcription | ✅ | ❌ | ✅ | `appointment:write`; 12/min; 4 MiB/60 s |
| AI ask | ✅ | ✅ | ✅ | `patient:read`; patient-scoped access check; general lookup limited to authorized tool results |
| AI history list/delete | ✅ own | ✅ own | ✅ own | `patient:read`; `user_id` scoped server-side |
| Analytics summary | ❌ | ❌ | ✅ | `analytics:read` |
| Staff list | ❌ | ❌ | ✅ | `staff:read` |
| Staff create/update/deactivate | ❌ | ❌ | ✅ | `staff:manage`; self/last-admin guards |
| Role grant/revoke | ❌ | ❌ | ✅ | `role:assign`; self-assignment 403 |
| Audit events | ❌ | ❌ | ✅ | `audit:read` |
| System logs / stream | ❌ | ❌ | ✅ | `logs:read` |
| Health | public | public | public | No auth; no patient data |
| Login | public | public | public | Rate-limited; uniform 401; no MFA |
| Logout / current user | ✅ | ✅ | ✅ | Authenticated principal |
| Docs/OpenAPI | public in development | public in development | public in development | Disabled in production by default (F-10) |
| Static frontend | public | public | public | Public HTML/JS/CSS; data fetched via authenticated API |

### E.2 Notes on authorization correctness

- **Every protected route** declares a permission via `require_permission`; there is no route that relies on frontend hiding. The policy module is the single source of truth.
- **Resource-level checks** exist for patients (`check_patient_access`), messages (`sender_id`/`recipient_id`), attachments (join to message + sender/recipient), appointments (doctor scoping + patient access), encounters (patient access), and AI history (`user_id`).
- **The only broad object-level gap is F-03**: `patient:read` is clinic-wide by design. This is not an IDOR bug in the demo, but it is the most important authorization change required before real data.
- **Nurse restrictions** are enforced server-side: no `patient:write`, `patient:delete`, `appointment:write`, `appointment:manage_availability`, `appointment:export`, `staff:*`, `role:assign`, `audit:read`, `logs:read`, `analytics:read`, or voice. Nurse has read-only appointments and messaging.
- **Doctor restrictions** are enforced server-side: own calendar for appointment writes and reads; no staff/role/audit/log/analytics; no patient delete.
- **Admin** has all permissions but is still subject to self-role and last-admin guards; Admin has no implicit access to another user’s private messages (sender/recipient check returns 404).
- **AI/MCP** re-checks the same policy per tool call; the model cannot grant itself permissions or write data.

---

## Section F — AI and MCP Security Assessment

### F.1 Trust boundaries

1. **Human identity:** server-side session cookie → `Principal`; never from request body/tool arguments.
2. **Model:** untrusted external process; only sees prompts and tool schemas/results.
3. **Tools:** in-process MCP server; seven read-only functions; no DB credentials or transport.
4. **Database:** accessed only by trusted service code, with parameterized queries.
5. **Output:** treated as untrusted text; validated and rendered as text nodes.

### F.2 Prompt injection

**Controls verified.**

- The system prompt explicitly labels record text and tool results as **DATA, not instructions** (`backend/app/llm/prompt.py:1-30`, rules 3-4).
- User questions are wrapped in an untrusted-input block; tool results are wrapped in a retrieved-record-data block.
- Deterministic input guardrails reject explicit attempts to bypass authorization, extract secrets, or access other patients (`backend/app/llm/guardrails.py:84-130`).
- Patient-scoped mode restricts tool calls to three single-patient tools and the exact verified patient UUID; nested `patient_id` mismatches are rejected (`guardrails.py:139-175`).
- Tool results are minimized before being sent back to the model (`guardrails.py:177-230`).
- Output validation rejects private keys, DSNs, external URLs, HTML/event handlers, `javascript:`, internal config names, prompt fragments, secret-like strings, and UUIDs not present in tool-result citations (`guardrails.py:276-330`).
- The tool loop is bounded by rounds, tool-call count, wall-clock budget, and per-call timeouts (`backend/app/llm/loop.py:100-180`).

**Residual risk.** An LLM can still be influenced by malicious record text to produce misleading or incomplete answers, or to omit/reframe authorized data. The authorization boundary prevents cross-patient access and writes, but **content integrity** is not guaranteed. No live adversarial red-team was performed (would send synthetic data to an external provider).

**Status:** **POTENTIAL RISK** for content integrity/indirect injection; **NOT VULNERABLE — VERIFIED** for privilege escalation, cross-patient data access, and state-changing actions.

### F.3 MCP tool authorization

| Tool | Permission / scope | Read-only? | Notes |
|---|---|:---:|---|
| `search_authorized_patients` | `patient:read`; returns only accessible patients | ✅ | Bounded query/limit; no writes. |
| `get_patient_summary` | `patient:read` + `check_patient_access` | ✅ | Patient-scoped tool; exact verified UUID. |
| `get_patient_lab_results` | `patient:read` + `lab:read` + patient access | ✅ | Patient-scoped; bounded results. |
| `get_recent_prescriptions` | `patient:read` + `prescription:read` + patient access | ✅ | Patient-scoped; bounded results. |
| `find_patients_by_lab` | `patient:read` + `lab:read`; per-row patient access | ✅ | Cross-patient lookup; drops non-visible rows. |
| `find_patients_by_medication` | `patient:read` + `prescription:read`; per-row patient access | ✅ | Cross-patient lookup; drops non-visible rows. |
| `get_my_recent_patients` | `patient:read`; scoped to caller’s practitioner/care assignment | ✅ | “You” is the verified caller, never an argument. |

**Verified guarantees.**

- Exactly seven tools; no `execute_sql`, shell, filesystem, HTTP, or write tool (`backend/app/mcp/server.py:20-40`, `362-400`).
- The verified `ToolContext` is injected via the SDK’s `context_kwarg` and excluded from the LLM-facing schema; identity cannot be supplied as a tool argument.
- Unknown tool names are rejected and audited; invalid/missing context is denied; tool arguments are JSON-Schema validated and bounded.
- Every call is audited exactly once (`mcp.<tool_name>`, outcome, resource, request id); safe error envelopes; no stack traces/payloads.
- The model cannot grant itself permissions; all writes go through explicit REST endpoints requiring user confirmation.

### F.4 Patient data exposure

- **AI history:** `admin.ai_conversation` rows are scoped by `user_id`; list/delete queries filter by the verified caller. Admin has no implicit access to another user’s history.
- **Patient scope:** every patient-specific tool calls `check_patient_access`; general lookup tools drop non-visible rows. In the demo, `check_patient_access` is clinic-wide (F-03), so “authorized” means all patients.
- **Minimization:** MRN, date of birth, and several foreign keys are dropped before tool results go to the model; text fields/results are truncated/bounded.
- **Output:** no secrets/DSNs/URLs/HTML; citations must come from tool results; unapproved UUIDs are rejected.
- **Residual:** clinical content (names, notes, labs, prescriptions) is still sent to the external provider (F-04); AI history/idempotency records persist (F-12).

### F.5 AI output handling

- `frontend/assets/js/markdown.js` never parses HTML; every answer fragment becomes a text node; attributes are app-authored constants only.
- `frontend/assets/js/ai-assistant.js:262-263` uses `markdown.answerBody(answer)`; no `innerHTML`.
- CSP `script-src 'self'` + hash; no third-party scripts; `object-src 'none'`; `frame-ancestors 'none'`.
- AI answers are labelled AI-generated and are informational, not clinical advice.

### F.6 Provider security

- API key is held in `SecretStr`, read from `LLM_API_KEY`, never logged/returned/sent to the browser (`backend/app/llm/provider.py:1-20`, `171-178`).
- Provider errors are mapped to safe messages; no response bodies/headers/keys in client errors.
- The provider is external by default (`https://opencode.ai/inference/go`); data transfer is inherent to the feature (F-04).
- No DPA/BAA/training/retention/residency evidence exists in the repository; docs explicitly say no BAAs and no real patient data.

### F.7 AI/MCP findings

- **F-03** (clinic-wide patient scope) affects AI general lookup and every patient tool.
- **F-04** (external LLM data transfer) is the primary AI-specific risk.
- **F-05** (no MFA) affects who can invoke the assistant and approve actions.
- **F-07** (in-process AI rate limit) limits cost/DoS only per process.
- **F-12** (AI history/idempotency retention) is a privacy/data-lifecycle gap.

### F.8 Unverified AI areas

- Live adversarial prompt-injection testing (direct/indirect) was **not executed**; it would send synthetic records to an external provider and was outside the safe read-only scope.
- Provider retention/training/subprocessor/residency terms were **not verified**.
- Model behavior under malformed/tool-error conditions was reviewed in code/tests but not live-tested in this audit.
- No formal AI red-team, evaluation dataset, or monitoring for automation bias exists.

---

## Section G — Infrastructure Security

### G.1 Network exposure

| Service | Bind / publish | Protocol | Authentication | TLS | Finding |
|---|---|---|---|---|---|
| `app` | `0.0.0.0:8000` / `[::]:8000` | HTTP | Session cookie + CSRF | No (local) | F-09, F-10 |
| `db` | `0.0.0.0:5432` / `[::]:5432` | PostgreSQL | Password (default, superuser) | No (`ssl=off`) | **F-01** |
| `speech` | `8080/tcp` internal only | HTTP | `WHISPER_AUTH_MODE=none` | No | Private; F-14 (defense in depth) |
| External LLM | Outbound HTTPS | HTTPS | API key | Yes (provider) | F-04 |

**External reachability note.** The host is a WSL environment (`172.17.253.50`). Whether Windows forwards these ports beyond the machine depends on the host firewall/NAT; that was **not tested**. The bind/publish evidence is sufficient to require loopback binding.

### G.2 Database security

- **Exposure:** published on all interfaces; `listen_addresses='*'`.
- **Privilege:** `app` is superuser (`rolsuper=t`, `rolcreaterole=t`, `rolcreatedb=t`); the app and migrations both use it.
- **Authentication:** remote `scram-sha-256`; the default password is in use (F-01).
- **TLS:** `ssl=off`.
- **Extensions:** only `plpgsql` (low risk).
- **Queries:** ORM/parameterized; no SQL injection found.
- **Backups:** manual only (F-15).
- **Sensitive fields:** password hashes (argon2id), session token hashes (SHA-256), message bodies/attachments, AI history, audit events. No application-level field encryption; relies on DB/volume/host controls. With F-01, this is critical.

### G.3 Docker and Compose

- **Images:** `postgres:16-alpine`, Python 3.12 slim app (dependencies from `uv.lock`), speech image with the checksum-verified whisper.cpp Base model; no `.env` baked into any image.
- **Users:** app uid 10001, speech uid 10001, Postgres server uid 70. `docker exec` defaults to root but the server process is not root.
- **Ports:** app and db published; speech `expose` only.
- **Volumes:** `postgres_data` named volume; no encryption.
- **Hardening:** no `read_only`, `cap_drop`, `security_opt`, or explicit network segmentation in compose; healthchecks present.
- **Secrets:** env-file runtime only; `.dockerignore` excludes `.env`/`**/.env`; no secrets in the image.
- **Healthchecks:** DB `pg_isready`; app `/health/ready`; speech `/health`.

### G.4 Kubernetes

- **DB:** StatefulSet + `ClusterIP` Service (not published) — positive.
- **App:** Deployment with `runAsNonRoot`, `runAsUser: 10001`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, dropped capabilities; `LoadBalancer` Service on 8081.
- **Config:** `APP_ENV=development`, docs open, localhost origins; intended for local Docker Desktop, not production.
- **Ingress example:** `clinical.local`, no TLS block; production TLS is documented but not implemented in the example.
- **NetworkPolicy:** none in the repository.
- **Postgres pod:** `allowPrivilegeEscalation: false`; no `runAsNonRoot`/cap drop due to first-time init; documented trade-off.
- **Secrets:** `secretKeyRef` from `app-secret`; `k8s/secret.yaml` gitignored/untracked.

### G.5 Secrets

- **Tracked files:** `python3 scripts/security_check.py` covered 242 tracked files in scope (229 scanned), 0 findings/0 warnings across secret scan, forbidden files, gitignore, frontend hygiene, DB credentials, and edge headers.
- **Local files:** `backend/.env` and `k8s/secret.yaml` exist, are gitignored/untracked, and contain real/generated values (redacted). WSL shows mode `0777`; verify `0600` on native Linux (F-16).
- **Redaction:** `SecretStr` fields; structured-log filter for password/token/secret/api_key/authorization/cookie/session; DSN credential scrub.
- **Build:** `.dockerignore` excludes env files; Dockerfile copies only backend/frontend/scripts; no secret in image layers.
- **`APP_SECRET_KEY`:** required but unused; no JWT (F-13).
- **Demo passwords:** documented placeholder in use (F-02).
- **DB password:** compose default in use (F-01).

### G.6 Logging, audit, monitoring

- Structured JSON logs; request path only (no query strings); no request bodies; sensitive fields redacted.
- Failed-login logs include attempted email/IP (PII; F-12); successful login logs user id.
- Audit events cover login success/failure, logout, authz denials, patient reads, staff/role changes, messages, AI, voice, appointments; no payloads.
- Audit is read-only via API and not tamper-evident; retention is demo-lifetime.
- System logs UI/stream is Admin-only (`logs:read`).
- No SIEM/alerting/on-call/monitoring integration in the repository; no WAF/DDoS beyond hosting providers.

### G.7 Backups and resilience

- Manual `pg_dump` + `age` + `pg_restore` procedure in `docs/DEPLOYMENT.md:269-304`.
- No automation, schedule, retention, or restore drill; compose volume unencrypted.
- Production docs use a free-tier managed DB with limited backups/no PITR.
- Attachments and AI history are in PostgreSQL; a DB backup covers them; losing the volume without a backup loses everything.

---

## Section H — Recommended Remediation Plan

> **Remediation was not applied.** The following is a prioritized plan for a separate, explicitly authorized phase.

### H.1 Priority 1 — Critical vulnerabilities

**None confirmed in the current synthetic demo.** If the deployment is reachable beyond an isolated machine or receives real patient data, F-01 and F-02 become Critical and must be treated as P1.

### H.2 Priority 2 — High-risk vulnerabilities

| Order | Finding | Action | Dependencies | Verification test |
|---:|---|---|---|---|
| 1 | F-02 | Rotate all demo passwords; reject documented placeholders in seed; disable/rename shared demo accounts; restrict app to loopback. | Secret rotation; seed script change; deployment config. | Old placeholder login fails; new unique passwords work; Admin requires MFA (after F-05). |
| 2 | F-01 | Bind PostgreSQL to `127.0.0.1`/private network; remove published port; rotate DB password; create least-privilege `app_rw` + `migrator`; enable TLS; add network policy. | DB role migration; secret manager; TLS certs; app config. | Remote `host:5432` connection fails; `app` is not superuser; `ssl=on`; app still passes readiness. |
| 3 | F-01/F-02 | Add a startup/deployment guard that fails if the runtime DB role is superuser or a demo password matches a placeholder. | Config validation; CI/deploy check. | Guard fails on a deliberately bad config. |
| 4 | F-01/F-09 | Put the app behind TLS; redirect HTTP→HTTPS; set `APP_ENV=production`; `SESSION_COOKIE_SECURE=true`; trust forwarded headers only from known proxies. | Reverse proxy/load balancer; DNS/certs. | `curl -I https://...` shows HSTS; HTTP redirects; cookies have `Secure`/`HttpOnly`/`SameSite`. |

### H.3 Priority 3 — Medium-risk improvements

| Order | Finding | Action | Dependencies | Verification test |
|---:|---|---|---|---|
| 1 | F-03 | Implement care-team/assignment policy in `check_patient_access`; add RLS/tenant filters; restrict patient lists and AI/MCP search to the authorized set. | Relationship table/migration; policy tests; possibly RLS. | Doctor/Nurse cannot read a patient outside their care team; AI tools return only authorized rows; 404 concealment preserved. |
| 2 | F-04 | Complete provider DPA/BAA/retention/training/residency review; minimize/redact PHI; gate LLM on approved provider; consider local model; document data flow. | Legal/privacy review; provider contract; config gate. | `LLM_ENABLED` refused in real-data mode without approval; egress audit; data-flow doc approved. |
| 3 | F-05 | Add TOTP/WebAuthn MFA for Admin and clinical users; step-up for staff/role/audit/export; recovery codes. | MFA library/service; enrollment UX; recovery process. | Admin login requires MFA; step-up enforced on sensitive routes; recovery tested. |
| 4 | F-07 | Move rate limits to Redis/DB; add account-level backoff/alerting; CAPTCHA/WAF; verify client IP behind proxy. | Redis/DB table; monitoring; proxy config. | Multi-instance limit holds; distributed attempts throttled; alerts fire. |
| 5 | F-08 | Add AV/CDR scanning/quarantine for attachments; block macro-enabled types; user warning. | Scanning service; storage/quarantine; UX. | EICAR-like test file quarantined (in a test environment); clean files pass. |
| 6 | F-10 | Disable docs/OpenAPI in shared/production; self-host docs assets; remove CDN allowance. | Config change; docs hosting. | `/docs` and `/openapi.json` return 404 in production. |
| 7 | F-11 | Add `Cache-Control: no-store` to authenticated API responses; keep static caching. | Middleware/header test. | `curl -D -` shows `no-store` on patient/message/AI endpoints. |
| 8 | F-09 | Strip/overwrite forwarded headers at the proxy; use trusted-proxy config; add `__Host-` cookies. | Proxy config. | Spoofed `X-Forwarded-Proto` does not change HSTS/cookie behavior. |

### H.4 Priority 4 — Low-risk hardening and informational items

| Finding | Action | Verification |
|---|---|---|
| F-06 | Add Origin/Referer validation or CSRF token requirement on login; regression test. | Cross-origin login POST without token rejected. |
| F-12 | Add retention/erasure jobs; pseudonymize attempted emails; tamper-evident audit; document AI-history retention. | Retention job deletes expired rows; audit hash chain verifies; logs contain no raw emails. |
| F-13 | Remove `APP_SECRET_KEY` if unused or document/use it with ≥32-byte validation; fix docs. | Config tests; docs match code. |
| F-14 | Add compose `cap_drop: ALL`, `no-new-privileges`, read-only rootfs/tmpfs; K8s NetworkPolicies; speech auth/mTLS; image scanning/SBOM. | `docker inspect` shows hardening; NetworkPolicy denies cross-pod traffic; speech rejects unauthenticated calls. |
| F-15 | Automate encrypted backups, off-host storage, retention, PITR where available, restore drills. | Restore drill succeeds; RTO/RPO documented; backup alerts. |
| F-16 | Verify `0600` on native Linux; move secrets to a manager; rotate if exposed. | `stat -c %a` shows 600; no secrets in tracked files; scan passes. |
| F-17 | Decide on self-service reset/change; if added, use fixed `PUBLIC_ORIGIN`, single-use hashed tokens, expiry, rate limits, uniform responses; or use SSO. | Reset link never uses Host header; token single-use/expiry; no enumeration. |

### H.5 Recommended verification suite after remediation

1. **DB exposure:** from another host, `nc -vz <host> 5432` fails; `docker compose ps` shows `127.0.0.1:5432->5432`.
2. **DB privilege:** `select rolsuper, rolcreaterole, rolcreatedb from pg_roles where rolname=current_user` returns all false for the runtime role.
3. **DB TLS:** `show ssl` returns `on`; remote `hostssl` required.
4. **Demo credentials:** login with the old documented placeholder fails for all three accounts; new unique passwords work; no placeholder in seed output.
5. **MFA:** Admin login without a second factor fails; with TOTP/WebAuthn succeeds; step-up enforced on role/staff/audit/export.
6. **Patient scope:** Doctor A cannot read Doctor B’s patient (404); Nurse cannot read outside care team; Admin scope is explicit; AI tools return only authorized rows.
7. **CSRF:** cross-origin login POST without a token is rejected; normal same-origin login works.
8. **Headers:** `curl -I https://...` shows HSTS, CSP without `unsafe-inline`/CDN, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `no-referrer`; cookies `Secure`/`HttpOnly`/`SameSite`.
9. **Docs:** `/docs`, `/redoc`, `/openapi.json` return 404 in production.
10. **Cache:** authenticated API responses show `Cache-Control: no-store`.
11. **Attachments:** malicious test file quarantined; clean allowlisted file downloads only to sender/recipient; unrelated staff 404.
12. **Rate limits:** multi-instance login/AI/voice limits hold; distributed attempts throttled; alerts fire.
13. **Backup:** restore drill into an isolated DB succeeds; attachment/AI history present; encryption key separation verified.
14. **Secret scan:** `python3 scripts/security_check.py` passes; `pip-audit` high/critical gate passes.
15. **Containers:** non-root, cap-drop, no-new-privileges, read-only rootfs, NetworkPolicy; speech private/auth.

---

## Section I — Testing Summary

### I.1 Tests performed

| # | Test | What was inspected | Executed? | Result | Evidence | Limitations |
|---:|---|---|:---:|---|---|---|
| 1 | Git baseline integrity | `git status`, HEAD, tracked files | ✅ | Baseline saved; tree unchanged during audit | `/tmp/opencode/audit-git-before.txt`; 65 status lines; HEAD `5a79445…` | Report itself added after this check (verified again at end) |
| 2 | Architecture/file inventory | Repo tree, routers, models, frontend pages/modules | ✅ | 51 API routes, 11 pages, 29 JS modules | `grep @router`, `ls` | Static only |
| 3 | Password hashing review | `passwords.py`, login flow | ✅ | Argon2id, dummy hash, uniform 401 | `passwords.py:1-40`; `auth.py:60-90` | No timing benchmark |
| 4 | Session/cookie review | `sessions.py`, `cookies.py`, `identity.py` | ✅ | Hashed tokens, expiry/revocation, HttpOnly/SameSite/Secure-in-prod | Code + live `Set-Cookie` for CSRF | No live login (avoid DB writes) |
| 5 | CSRF review | `csrf.py`, `api.js` | ✅ | Double-submit on `/api/v1`; bootstrap caveat F-06 | Code | No browser exploit |
| 6 | Rate-limit review | `ratelimit.py`, AI/voice/interpret limiters | ✅ | 5/email+IP, 50/IP, 10 AI/min, 12 voice/min; in-process | Code/docs | No brute-force test |
| 7 | RBAC policy review | `policy.py` | ✅ | 3 roles, 17 permissions; deny by default; 401/403; audit denials | Code | No live role-matrix login |
| 8 | Route permission inventory | All `api/v1` routers | ✅ | Every protected route declares a permission | Route grep + code | Static only |
| 9 | IDOR/ownership review | Patients, messages, attachments, appointments, AI history | ✅ | Ownership/scoping checks present; F-03 is clinic-wide by design | Code | No live object-ID probes (would write audit rows) |
| 10 | Patient-access policy review | `services/access.py` | ✅ | Clinic-wide demo policy; 404 concealment | Code/docs | Not exploited |
| 11 | Appointment concurrency review | `services/appointments.py` | ✅ | Advisory xact locks + overlap re-check | Code | No destructive concurrency test |
| 12 | Message/attachment review | `messages.py`, `message_attachments.py` | ✅ | Allowlist, magic, 5 MiB, sender/recipient, attachment-only, no AV | Code | No malicious upload |
| 13 | Voice review | `voice.py`, `services/voice.py` | ✅ | `appointment:write`, 4 MiB/60 s/12 min, fixed FFmpeg args, temp cleanup, private speech | Code + container ports | No real microphone/browser test |
| 14 | AI/MCP review | `llm/`, `mcp/`, `ai.py` | ✅ | Seven read-only tools, identity injection, re-auth, output validation, bounded loop | Code | No live adversarial LLM test |
| 15 | Frontend XSS/rendering review | `markdown.js`, `ui.js`, pages/modules | ✅ | Text nodes; no data `innerHTML`; CSP; no third-party scripts | Code + `security_check.py` | No browser fuzzing |
| 16 | Security headers/CSP | Live `curl -D -` + code | ✅ | CSP, XFO DENY, nosniff, no-referrer, COOP/CORP, Permissions-Policy; HSTS only HTTPS | Live headers | HSTS/proxy behavior conditional |
| 17 | CORS preflight | `OPTIONS` with evil origin | ✅ | No `Access-Control-Allow-Origin` for evil origin; explicit origins | Live response | No browser test |
| 18 | Static traversal/`.git`/`.env` probes | `curl` `/.git/config`, `/.env`, `/backend/.env`, encoded traversal | ✅ | All 404 | Live responses | Only a sample of encodings |
| 19 | DB exposure/privileges/TLS | `docker compose ps`, `ss`, `pg_roles`, `show ssl`, `pg_hba` | ✅ | Published `0.0.0.0:5432`; `app` superuser; `ssl=off`; default password connects | Live read-only queries | Password redacted; external reachability not tested |
| 20 | Demo credential hash verification | Active staff hashes + argon2id candidates | ✅ | All three active accounts match the documented placeholder | Read-only DB + local argon2 | No login sent |
| 21 | Container inspection | `docker inspect`, `docker top`, `id` | ✅ | App/speech uid 10001; DB process postgres; no privileged/read-only/cap-drop in compose | Live inspection | No escape test |
| 22 | Repository secret scan | `scripts/security_check.py` | ✅ | 242 tracked files in scope (229 scanned); 6 sections; 0 findings/0 warnings | Script output | Does not scan untracked local env/secret files |
| 23 | Docs/OpenAPI exposure | `curl /docs`, `/openapi.json`; `APP_ENV` | ✅ | Both 200; `APP_ENV=development` | Live responses | Local dev only |
| 24 | HSTS forwarded-proto probe | `curl` with `X-Forwarded-Proto: https/http` | ✅ | HSTS emitted over HTTP with spoofed `https`; absent with `http` | Live headers | No proxy/TLS setup |
| 25 | Backup/retention review | `docs/DEPLOYMENT.md`, `docs/SECURITY.md` | ✅ | Manual encrypted backup procedure; no automation/PITR; no restore drill | Docs | Not executed |
| 26 | Dependency audit | CI workflow, `pyproject.toml`/`uv.lock` | ⚠️ | CI has `pip-audit` high/critical gate; not run in this audit | `.github/workflows/ci.yml:101-134` | No local SCA output |
| 27 | Existing test suites | Backend/frontend tests | ❌ | Not re-run in this audit; prior results out of scope | — | Read-only audit scope |

### I.2 Safety boundaries observed

- No application code, CSS, API implementation, schema, DB record, permission, auth config, Docker config, dependency, or secret was modified.
- No login request was sent to the live stack (avoiding session/audit writes); credential findings were verified by reading argon2id hashes and verifying candidates locally.
- No brute-force, destructive SQL, malicious upload, exploit, port scan, external scan, or production test was performed.
- No real patient data or production secrets were sent to external services.
- The only new file is this report.

### I.3 Git integrity verification

- Baseline captured before the report was created: `/tmp/opencode/audit-git-before.txt` (65 status lines).
- HEAD at baseline and end: `5a79445f34fa6fc8a89d59ae80311e57b2c9d473`.
- Final `git status --porcelain=v1` after writing the report: **66 status lines**; the unified diff against the baseline shows exactly one addition, `?? MEDICORE_SECURITY_AUDIT.md`.
- No tracked file was modified by the audit; `git diff --name-only` still lists only the pre-existing uncommitted feature work. All pre-existing work remains untouched.

> Final verification result: **PASS** — the only new path is this report.

---

## Section J — Unverified Areas

The following could not be verified with the available code, local stack, and access. They are **not** claims of security or insecurity.

| Area | Why unverified | What would be needed |
|---|---|---|
| Production HTTPS/HSTS/TLS | Local stack is HTTP; production topology is documentation only; ingress example has no TLS block | Live production/staging URL, TLS certs, proxy config, `curl -I` |
| External LLM provider DPA/BAA/retention/training/residency | No contract/provider console access; docs say no BAAs | Provider contract, data-processing terms, retention/training settings, subprocessor list |
| Real browser microphone/MediaRecorder | No browser automation/device in the audit; prior feature tests exist but were not re-run | Browser E2E with permissions, codec matrix, screen-reader checks |
| Live Kubernetes deployment | Manifests only; cluster not applied/verified | `kubectl` access to the target cluster, NetworkPolicy/TLS/secret checks |
| Backup restore/PITR | No backup infrastructure; restore not executed | Isolated restore environment, backup artifacts, RPO/RTO drill |
| Dependency vulnerabilities | CI `pip-audit` configured but not run locally; no SCA output reviewed | `uvx pip-audit`/CI run, SBOM, image scan |
| Subdomain takeover/DNS | No DNS/hosting config in repo; production domains are placeholders | DNS zone/provider access, subdomain inventory |
| Host firewall/NAT external reachability | Only bind/publish confirmed; WSL/Windows forwarding not tested | Host firewall/NAT config, external port check from a separate network |
| Malware-scanning efficacy | No AV/CDR installed; no malicious files tested | Scanning service, EICAR test in an isolated environment |
| Prompt-injection red-team | No live adversarial LLM calls (would send data externally) | Approved synthetic red-team environment/provider |
| Audit tamper-evidence/retention | Not implemented; demo-lifetime retention | Append-only/WORM store, hash chain, retention job |
| MFA/SSO/SCIM | Not implemented | MFA/IdP integration and tests |
| Real patient data / legal compliance | Synthetic demo only; no HIPAA/GDPR certification | Legal/privacy review, BAAs/DPAs, DPIA, clinical validation |
| Local secret file permissions | WSL DrvFs shows `0777`; native Linux permissions not verified | Native Linux host, `stat`, secret manager |
| Session table cleanup/monitoring | 52 active session rows observed; no cleanup job reviewed | Retention/cleanup job, monitoring |
| Any environment-specific WAF/DDoS | Not in repo; hosting-provider controls unknown | Provider configuration/console |

---

## Appendix A — Severity definitions

| Severity | Definition used in this report |
|---|---|
| Critical | Realistic authentication bypass, admin escalation, RCE, complete DB compromise, or unauthorized patient data at scale. |
| High | Unauthorized patient/staff resource access, significant privilege escalation, sensitive disclosure, or authentication abuse. |
| Medium | Meaningful security impact with additional conditions/limitations. |
| Low | Limited impact, defense-in-depth gap, or hardening opportunity. |
| Informational | Observation, configuration detail, or recommendation; not a vulnerability. |

## Appendix B — Finding-to-vector mapping

| Vector | Finding(s) |
|---|---|
| Public DB (#6) | F-01 |
| Credential stuffing (#5) | F-02, F-05, F-07 |
| AI prompt injection (#9) | F-03, F-04, F-05 |
| Weak JWT secret (#10) | F-13 (N/A; no JWT) |
| Race conditions (#11) | Verified for appointments; no finding |
| Account pre-hijacking (#12) | F-17 (N/A; no registration/reset) |
| SSRF (#14) | Verified; no finding |
| Path traversal (#15) | Verified; no finding |
| Password reset poisoning (#16) | F-17 (N/A) |
| Clickjacking (#18) | Verified; no finding |
| Open redirects (#19) | Verified; no finding |
| User enumeration (#20) | Verified; no public finding |
| Sensitive data exposure | F-01, F-02, F-04, F-11, F-12, F-16 |
| CSRF | F-06 |
| XSS | Verified; no finding |
| SQLi/command injection | Verified; no finding |
| DoS/resource consumption | F-07 |
| Operational/backup/monitoring | F-12, F-14, F-15 |

## Appendix C — Key evidence commands (sanitized)

These are the safe, read-only checks used. Secret values are not shown.

```bash
# Git baseline
git status --porcelain=v1 > /tmp/opencode/audit-git-before.txt
git rev-parse HEAD

# Live exposure
docker compose ps
ss -ltn | grep -E ':5432|:8000|:8080'

# DB read-only checks (password redacted)
docker compose exec -T db psql -U app -d clinical_demo -Atc \
  "select current_user, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin from pg_roles where rolname=current_user;"
docker compose exec -T db psql -U app -d clinical_demo -Atc "show listen_addresses; show ssl;"
docker compose exec -T db sh -c 'cat "$(psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "show hba_file")"'

# Headers / CORS / traversal / docs
curl -sS -D - -o /dev/null http://localhost:8000/
curl -sS -D - -o /dev/null http://localhost:8000/api/v1/auth/me
curl -sS -D - -o /dev/null -X OPTIONS http://localhost:8000/api/v1/auth/login \
  -H 'Origin: https://evil.example' -H 'Access-Control-Request-Method: POST'
for p in /.git/config /.env /backend/.env /%2e%2e/backend/.env; do
  curl -sS -o /dev/null -w "$p -> %{http_code}\n" "http://localhost:8000$p"
done
curl -sS -o /dev/null -w 'openapi %{http_code}\n' http://localhost:8000/openapi.json
curl -sS -o /dev/null -w 'docs %{http_code}\n' http://localhost:8000/docs

# HSTS forwarded-proto behavior
curl -sS -D - -o /dev/null http://localhost:8000/ -H 'X-Forwarded-Proto: https' | grep -i strict-transport
curl -sS -D - -o /dev/null http://localhost:8000/ -H 'X-Forwarded-Proto: http' | grep -i strict-transport

# Repository scan
python3 scripts/security_check.py

# Container users / hardening
docker top medicalplatform-db-1 -o pid,user,comm,args
docker compose exec -T app id
docker compose exec -T speech id
docker inspect medicalplatform-app-1 --format 'User={{.Config.User}} Privileged={{.HostConfig.Privileged}} Readonly={{.HostConfig.ReadonlyRootfs}}'
```

## Appendix D — Key file index

| Area | Files |
|---|---|
| Auth/session | `backend/app/auth/passwords.py`, `sessions.py`, `cookies.py`, `csrf.py`, `ratelimit.py`, `identity.py`, `backend/app/api/v1/auth.py` |
| RBAC | `backend/app/authorization/policy.py`, `backend/app/services/access.py`, `backend/app/api/v1/staff.py` |
| Patients | `backend/app/api/v1/patients.py`, `backend/app/models/patient.py`, `backend/app/services/patients.py` |
| Appointments | `backend/app/api/v1/appointments.py`, `backend/app/services/appointments.py`, `backend/app/services/appointment_ai.py` |
| Messages/files | `backend/app/api/v1/messages.py`, `backend/app/services/message_attachments.py`, `backend/app/models/admin.py` |
| AI/MCP | `backend/app/api/v1/ai.py`, `backend/app/llm/`, `backend/app/mcp/`, `backend/app/models/ai.py` |
| Voice | `backend/app/api/v1/voice.py`, `backend/app/services/voice.py`, `speech/Dockerfile` |
| Web/security | `backend/app/main.py`, `backend/app/config.py`, `frontend/_headers`, `frontend/assets/js/api.js`, `markdown.js`, `ui.js` |
| Infra | `docker-compose.yml`, `backend/Dockerfile`, `k8s/*.yaml`, `.dockerignore`, `.gitignore`, `k8s/.gitignore` |
| Docs | `docs/SECURITY.md`, `docs/DEPLOYMENT.md`, `docs/AI_GUARDRAILS.md`, `docs/KUBERNETES.md`, `docs/API.md` |
| CI/scans | `.github/workflows/ci.yml`, `scripts/security_check.py` |

## Appendix E — Audit completion statement

- **Application code/config/DB records modified:** No.
- **Fixes applied:** None.
- **Commits made:** None.
- **Secrets printed:** No actual secret values are included in this report; redacted values are referenced by file/line only.
- **Report file:** `MEDICORE_SECURITY_AUDIT.md` (this file).
- **Git verification:** Baseline `65` status lines → final `66` status lines; the only diff is `?? MEDICORE_SECURITY_AUDIT.md`. HEAD is unchanged at `5a79445f34fa6fc8a89d59ae80311e57b2c9d473`; no tracked file was modified by the audit. All pre-existing uncommitted work remains untouched.

**Final note.** The current platform is a well-engineered synthetic-data demo with strong application-layer controls, but the local deployment is not safe to expose. The highest-value immediate actions are: rotate the demo passwords, bind PostgreSQL to loopback and remove its published port, replace the superuser app role, add MFA, implement care-relationship patient scoping, and complete the external-LLM data-protection review before any real patient data is considered.
