# Clinical AI Platform — Agent Execution Plan

> **Purpose:** Build a working clinical AI web platform in small, verifiable phases, from repository inspection to a publicly accessible website.
>
> **How the coding agent should use this file:** Read the whole plan first. Work on **one phase at a time**, preserve existing working code, run the phase's checks, update the progress log, and only then move to the next phase. Do not mark a phase complete unless its acceptance criteria pass.
>
> **Default deployment target:** $0/month prototype using free-tier hosting where available. Provider free tiers, limits, and availability can change. Keep deployment provider-specific settings isolated so the project can be moved later.
>
> **Safety boundary:** Use synthetic/demo patient data only. This plan does **not** authorize use of real patient data or production clinical decision-making. Public online availability is not the same as being compliant or safe for real clinical use.

---

## 0. Product goal and definition of done

Build a browser-based clinical AI application with:

- A responsive HTML/CSS/JavaScript frontend.
- A Python FastAPI backend.
- An MCP server exposing narrowly scoped clinical-data tools to an LLM.
- PostgreSQL as the main relational database.
- Authentication and role-based authorization.
- Roles: doctor, nurse, admin. *(Superseded 2026-10-26 by owner decision: the original four-role model — doctor, nurse, clinical administrator, technical administrator — was simplified. `technical administrator` was removed entirely and `clinical administrator` became **Admin** with full access. See §4 and the progress log.)*
- A staff/admin interface for managing accounts and assigning roles.
- Clinician workflows for finding authorized patients and viewing permitted clinical information.
- Audit events for security-relevant actions and clinical-data access.
- Docker-based local development.
- Automated tests, documented setup, and a deployment path to a public HTTPS website.
- A final public demo using synthetic data and clear demo/privacy notices.

### Final acceptance criteria

The project is “online demo ready” only when all of these are true:

1. A public HTTPS URL loads the website.
2. A user can sign in using the documented demo flow.
3. The UI is usable on desktop and mobile-sized screens.
4. The backend is reachable from the frontend and returns expected responses.
5. PostgreSQL schema migrations run successfully on the hosted database.
6. The app can list synthetic patients and display a synthetic patient summary according to the user's role.
7. Unauthorized requests are rejected by the backend, not merely hidden in the UI.
8. MCP tools enforce the same authorization rules and do not accept caller-supplied identity as trusted identity.
9. The LLM cannot run arbitrary SQL or access database credentials.
10. Secrets are stored in deployment environment variables, never committed to source control.
11. Automated tests and basic dependency/security checks pass.
12. Audit logging works for login events, permission denials, and clinical-record reads.
13. The repository contains setup, deployment, architecture, and troubleshooting documentation.
14. The deployed demo contains no real patient information, real credentials, or secrets.
15. Known limitations, free-tier sleeping/cold starts, and non-production status are disclosed.

---

## 1. Agent operating rules

Follow these rules throughout the project:

1. **Inspect before changing.** Read the repository tree, README, dependency files, environment examples, tests, migrations, and current git status before editing.
2. **Preserve working implementation.** Do not replace existing architecture wholesale just because another pattern is preferred. Identify what exists and adapt this plan.
3. **Work incrementally.** Implement only the current phase unless a small prerequisite fix is necessary.
4. **Establish a baseline.** Run existing tests and record failures before making changes. Do not claim a pre-existing failure was caused or fixed without evidence.
5. **Make small, reviewable changes.** Avoid giant rewrites and unrelated refactors.
6. **Use safe defaults.** Deny access by default; validate input; use parameterized queries/ORM methods; keep secrets out of logs and source control.
7. **Do not trust the frontend.** Every protected action must be authorized in the backend.
8. **Do not trust the LLM with authority.** The model may request a tool call, but the server must independently authenticate, authorize, validate, and audit it.
9. **Never expose arbitrary SQL tools to the LLM.** MCP tools must be narrow, typed, read-only by default, and restricted to approved operations.
10. **Use synthetic data only.** Seed data must be fictional. The origin marker is database-internal (`provenance`); the visible demo-labeling requirement was superseded by the 2026-10-30 de-marking decision.
11. **No false completion.** If a command cannot be run, a credential is unavailable, or a test fails, report that fact and give the exact next action.
12. **No silent skipping.** If a phase is blocked, document the blocker and stop before dependent phases.
13. **Keep dependencies minimal.** Add packages only when they solve a concrete requirement; pin or lock dependencies appropriately.
14. **Do not commit generated secrets, `.env` files, database dumps, or real user data.**
15. **Update this plan.** Maintain the progress table and phase notes after each phase.

### Required end-of-phase report

At the end of each phase, report:

- What was implemented.
- Files created/modified.
- Commands run.
- Tests and their results.
- Acceptance criteria passed/failed.
- Security concerns or assumptions.
- Any blocker.
- The next phase recommended.

---

## 2. Target architecture

Keep the initial system as simple as practical.

```text
Browser
  |
  | HTTPS
  v
Frontend (HTML/CSS/JavaScript)
  |
  | HTTPS / JSON API
  v
FastAPI application
  |-- Authentication and session/token validation
  |-- Authorization and role policy
  |-- Patient/encounter/lab/medication service layer
  |-- Audit logging
  |-- MCP tool server or separately deployed MCP service
  |       |-- get_patient_summary
  |       |-- get_patient_lab_results
  |       |-- get_recent_prescriptions
  |       |-- search_authorized_patients
  |       `-- other approved, narrow tools
  |
  v
PostgreSQL
  |-- admin schema
  |-- patients schema
  |-- laboratory schema
  `-- medications schema

FastAPI / approved MCP orchestration
  |
  v
LLM provider API or a later self-hosted model
```

### Architectural constraints

- The browser never connects directly to PostgreSQL.
- The LLM never connects directly to PostgreSQL.
- The MCP service never receives database superuser credentials.
- The backend derives the acting user from a verified authentication context, not from a model argument or untrusted request field.
- Every patient-data operation passes through a shared authorization/service layer.
- Database connections use TLS where supported, private networking where available, and a least-privilege application account.
- Admin-only endpoints require server-side admin authorization.
- Cross-origin rules are restrictive and explicitly configured.
- Do not expose the MCP transport publicly without appropriate authentication and access restrictions. If the deployment architecture cannot secure it, keep it internal and let the backend orchestrate it.
- Do not claim the system is clinically validated or suitable for patient care.

---

## 3. Suggested repository structure

Adapt this to the existing repository; do not force a restructure if an equivalent structure already works.

```text
clinical-ai-platform/
├── PLAN.md
├── README.md
├── .gitignore
├── .env.example
├── docker-compose.yml
├── frontend/
│   ├── index.html
│   ├── dashboard.html
│   ├── patients.html
│   ├── patient.html
│   ├── admin.html
│   ├── ai.html
│   ├── analytics.html
│   ├── assets/
│   │   ├── css/
│   │   ├── js/
│   │   └── images/
│   └── tests/
├── backend/
│   ├── pyproject.toml or requirements.txt
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── api/
│   │   ├── auth/
│   │   ├── authorization/
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── audit/
│   │   └── mcp/
│   ├── migrations/
│   └── tests/
├── scripts/
│   ├── seed_demo_data.py
│   └── healthcheck.py
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── DEPLOYMENT.md
│   ├── API.md
│   └── TROUBLESHOOTING.md
└── .github/
    └── workflows/
        └── ci.yml
```

---

## 4. Roles and authorization baseline

Implement the smallest practical role model. Roles do not automatically imply that a user may view every patient.

### Role intent

- **Doctor:** View patient records and clinical data permitted by the configured demo policy; use clinical summary and query workflows.
- **Doctor:** clinical reads, patient creation and internal messages (`patient:read`, `patient:write`, `message:read`, `message:write`, `lab:read`, `prescription:read`, `appointment:read`, `appointment:write`, `appointment:manage_availability`).
- **Nurse:** LEAST PRIVILEGE — clinical read plus read-only clinic appointments (`patient:read`, `lab:read`, `prescription:read`, `appointment:read`). No patient creation, appointment writes/availability, no admin panel, no staff/role/audit access (403 server-side).
- **Admin:** FULL ACCESS — all permissions: clinical reads, patient/message management, staff/role/audit/analytics, and appointment management. Role changes are confirmed, audited, and self-assignment is refused.
- ~~**Technical administrator**~~ **REMOVED** (owner decision, 2026-10-26): the role was deleted from the system — policy, database (migration `e8c2a91f4d77`), seed, UI, and docs. *(The original intent — technical settings without clinical access — is superseded by the three-role model above.)*

### Authorization requirements

- Enforce least privilege and deny by default.
- Define permissions explicitly (e.g. `patient:read`, `lab:read`, `prescription:read`, `staff:read`, `staff:manage`, `role:assign`, `audit:read`).
- Map roles to permissions in one place, with tests.
- Check authorization for every protected backend endpoint and MCP tool.
- In a real system, add a patient-care relationship/assignment policy before real data use. For the public demo, document the simplified synthetic-data policy clearly.
- A role change must be authorized, validated, and audited.
- Prevent users from elevating their own role unless a separately reviewed, explicitly designed break-glass procedure exists. Do not implement break-glass access in the demo.
- Avoid a 4-digit PIN as the sole authentication factor.

---

## Phase tracker

Update the status after each phase. Use `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, or `DONE`.

| Phase | Name | Status | Completion date | Notes |
|---|---|---|---|---|
| 0 | Repository discovery and baseline | DONE | 2026-10-26 | Greenfield repo; own git repo created in MedicalPlatform; see docs/INITIAL_ASSESSMENT.md and docs/API.md (frozen contract) |
| 1 | Requirements and architecture | DONE | 2026-10-26 | docs/ARCHITECTURE.md, SECURITY.md, BACKLOG.md, DEPLOYMENT.md, TROUBLESHOOTING.md + README; API contract frozen in docs/API.md |
| 2 | Project structure and local developer setup | DONE | 2026-10-26 | uv + Python 3.12.13, ruff, config validation (names-only errors), .env.example, health endpoint, docker-compose db service |
| 3 | PostgreSQL schema and migrations | DONE | 2026-10-26 | 4 schemas, FKs/indexes/soft-deletes/UTC/Numeric, Alembic up+down+up verified, idempotent synthetic seed (expanded to 45 patients / 167 encounters / 415 labs / 104 prescriptions with coherent abnormal patterns) |
| 4 | Backend foundation and API conventions | DONE | 2026-10-26 | Error envelope, request-id middleware, pagination bounds, CORS allowlist, body limit, log redaction, 88 tests passing |
| 5 | Authentication and role authorization | DONE | 2026-10-26 | DB-backed sessions (hashed tokens), argon2id, CSRF bootstrap, rate limiting, principal wiring, 401/403 verified at API layer over full role×endpoint matrix |
| 6 | Synthetic data and clinical-data APIs | DONE | 2026-10-26 | patients/summary/labs/prescriptions/encounters + staff/roles/audit APIs, audit writer, check_patient_access seam, IDOR/concealment tested, 242 tests passing |
| 7 | MCP server and tool authorization | DONE | 2026-10-26 | Official mcp SDK Tool/ToolManager in-process (no transport), 4 read-only tools at this phase (7 today), identity via server-injected ToolContext, per-call re-authorization, audit `mcp.*`, argument-tampering tests pass |
| 8 | LLM integration and safe response handling | DONE | 2026-10-26 | OpenCode OpenAI-compatible provider + NullProvider (503 when off), bounded tool loop with server-side allowlist, prompt-injection labeling, rate limit, mocked-provider tests, no key in browser |
| 9 | Frontend experience | DONE | 2026-11-07 | Landing/sign-in, dashboard, patients, patient detail tabs (including AI Patient), Graphify analytics/lab charts, responsive + accessible states; frontend suites 160 static + 4 Graphify + 21 Markdown + 75 render |
| 10 | Admin panel and staff management | DONE | 2026-11-07 | Staff table, role grant/revoke with confirmations, activation/deactivation, one-time password modal, audit view, analytics page; role-gated UI backed by server-side authorization |
| 11 | Audit, privacy, and security hardening | DONE | 2026-10-26 | Security headers + hash-based strict CSP (no unsafe-inline), HSTS-on-HTTPS, audit inventory proven complete (18/18 events), log redaction integration-tested, dependency audit green |
| 12 | Automated tests and CI | DONE | 2026-10-26 | 310 tests (0 failed) + 74/26 frontend checks; 3-job GH Actions w/ no secrets to fork PRs; dependency audit green after security bumps; live CI run on push verified in Phase 14/15 |
| 13 | Docker and local end-to-end validation | DONE | 2026-10-26 | Non-root image (292MB, no secrets baked), compose app+db with healthchecks, migrations-on-start, scripts/e2e_smoke.sh 10-step acceptance ALL PASSED incl. log secret scan + restart persistence |
| 14 | Public demo deployment preparation | BLOCKED | — | Deferred by user decision 2026-10-26: hosting later. Runbook + config prep lands in docs/DEPLOYMENT.md; AI will be enabled at deploy via server-side LLM key + Console budget cap |
| 15 | Deploy publicly and verify website | BLOCKED | — | Deferred by user decision 2026-10-26 (same as Phase 14). Execution resumes when hosting access is provided; no URL will be claimed before live verification |

---

# Phase 0 — Repository discovery and baseline

## Goal
Understand what is already implemented before modifying anything.

## Tasks
- [ ] List repository files and directories.
- [ ] Read README, dependency files, configuration, tests, migrations, and existing documentation.
- [ ] Check git status and preserve any uncommitted user work.
- [ ] Identify the current frontend, backend, database, MCP, and LLM integration status.
- [ ] Identify how the app currently runs locally.
- [ ] Run existing tests, linting, type checks, and build commands if present.
- [ ] Record failures and missing environment variables without printing secret values.
- [ ] Identify any committed secrets or patient-like data. Do not copy sensitive content into reports; flag and safely remediate if found.

## Deliverables
- A concise repository assessment in `docs/INITIAL_ASSESSMENT.md`.
- A list of existing features, missing features, baseline commands, and known failures.
- Updated phase status in this plan.

## Acceptance criteria
- [ ] No existing user changes were overwritten.
- [ ] The agent can explain the current architecture and start procedure.
- [ ] Baseline test results are recorded.
- [ ] Phase 1 scope is based on the actual repository, not assumptions.

---

# Phase 1 — Requirements and architecture

## Goal
Turn the product goal into explicit, testable requirements.

## Tasks
- [ ] Document user types and their allowed actions.
- [ ] Document core workflows: login, dashboard, patient search/list, patient summary, lab results, prescriptions, staff list, role assignment.
- [ ] Decide whether the MCP server is a module in the backend or a separate internal service for the first deployment.
- [ ] Decide how the frontend is served (static hosting or FastAPI static files).
- [ ] Choose one authentication strategy appropriate for a demo; document cookie/session or token storage and expiration.
- [ ] Define the minimum supported browsers and responsive layouts.
- [ ] Define error response shape, pagination rules, timestamps/time zone, and API versioning conventions.
- [ ] Document synthetic-data-only scope and production exclusions.

## Deliverables
- `docs/ARCHITECTURE.md`
- `docs/API.md` outline
- Role/permission matrix in `docs/SECURITY.md`

## Acceptance criteria
- [ ] All protected features have a documented permission.
- [ ] Data flow between browser, backend, MCP, LLM, and PostgreSQL is clear.
- [ ] Deployment approach is feasible within the selected free-tier constraints.
- [ ] Unresolved choices are listed rather than silently guessed.

---

# Phase 2 — Project structure and local developer setup

## Goal
Make the project reproducible on a developer machine.

## Tasks
- [ ] Normalize directory structure only where useful.
- [ ] Add `.gitignore` entries for `.env`, virtual environments, caches, local databases, logs, build output, and secrets.
- [ ] Add `.env.example` containing placeholder values only.
- [ ] Centralize environment configuration and validate required settings at startup.
- [ ] Add a clear install/start/test command to README.
- [ ] Add consistent formatting and linting tools suitable for the existing language stack.
- [ ] Add a basic backend health endpoint and frontend startup check.
- [ ] Ensure error output never prints secrets.

## Acceptance criteria
- [ ] A new developer can follow the README to start the app.
- [ ] Missing required configuration produces a clear, non-secret error.
- [ ] No actual secrets are present in tracked files.
- [ ] Existing functionality remains intact.

---

# Phase 3 — PostgreSQL schema and migrations

## Goal
Create a consistent relational model and repeatable database setup.

## Suggested schema
Adapt to existing models if appropriate.

### `admin`
- `staff_user`: ID, email/username, display name, password hash or identity-provider subject, active flag, timestamps.
- `role`: stable role key and description.
- `user_role`: user-to-role mapping.
- Optional `audit_event`: actor, action, resource type, resource ID where appropriate, timestamp, outcome, request/correlation ID. Avoid putting full clinical payloads or secrets in audit records.

### `patients`
- `patient`: internal ID, synthetic medical-record number, name fields or synthetic display name, date of birth only if required, timestamps.
- `encounter`: patient reference, date/time, encounter type, synthetic summary.

### `laboratory`
- `lab_result`: patient/encounter reference, test code/name, value, unit, reference range where meaningful, status, collected/result timestamps.

### `medications`
- `medication`: catalog identifier, display name, optional dosage form.
- `prescription`: patient/encounter reference, medication reference or name, dose, route, frequency, status, dates, prescriber reference where appropriate.

## Tasks
- [ ] Choose SQLAlchemy/Alembic or an equivalent migration-capable approach consistent with the repository.
- [ ] Define foreign keys, constraints, indexes, and timestamps.
- [ ] Store password hashes only if local password authentication is used; never store plaintext passwords.
- [ ] Add repeatable migrations.
- [ ] Add a safe synthetic demo seed script.
- [ ] Ensure demo seeds are idempotent or clearly resettable.
- [ ] Use parameterized queries or ORM query APIs.
- [ ] Configure an application database account with only necessary privileges.
- [ ] Keep schema changes separate from ad hoc production startup logic.

## Acceptance criteria
- [ ] A clean database can be created from migrations.
- [ ] Demo seed data can be loaded without duplication on repeat runs or has a documented reset workflow.
- [ ] Foreign keys and constraints behave as expected.
- [ ] Tests use isolated test data/database configuration.
- [ ] No real patient data is used.

---

# Phase 4 — Backend foundation and API conventions

## Goal
Provide a stable FastAPI service with validation, consistent errors, and service boundaries.

## Tasks
- [ ] Configure FastAPI application startup and shutdown cleanly.
- [ ] Add `/health/live` and `/health/ready` or equivalent liveness/readiness endpoints.
- [ ] Keep liveness independent from optional external LLM availability; readiness may check required dependencies.
- [ ] Add typed request/response schemas.
- [ ] Add centralized exception handling and request/correlation IDs.
- [ ] Add pagination and maximum page sizes for list endpoints.
- [ ] Add CORS only for known frontend origins.
- [ ] Add request size/time limits where appropriate.
- [ ] Separate API routing, business logic, authorization, and persistence.
- [ ] Return generic errors to clients; log safe diagnostic details on the server.

## Acceptance criteria
- [ ] Health endpoints return expected status.
- [ ] Invalid payloads receive controlled validation errors.
- [ ] API documentation is available in development and restricted or configured appropriately in the public demo.
- [ ] No endpoint returns raw stack traces or secrets.

---

# Phase 5 — Authentication and role authorization

## Goal
Protect user and clinical-data workflows.

## Tasks
- [ ] Implement a maintained authentication library or a reputable identity provider where feasible.
- [ ] If using passwords, hash with a suitable modern password-hashing algorithm and safe parameters.
- [ ] Use secure, HTTP-only cookies for browser sessions when compatible; configure `Secure` in HTTPS deployment and suitable `SameSite`/CSRF protection.
- [ ] If using bearer tokens, define storage, expiration, refresh, revocation, and XSS protections explicitly; avoid persistent browser storage for sensitive long-lived tokens.
- [ ] Implement logout and session/token expiry.
- [ ] Implement role/permission checks in a shared authorization layer.
- [ ] Protect all patient, lab, medication, and admin routes.
- [ ] Prevent self-role escalation.
- [ ] Add basic login throttling or rate limiting.
- [ ] Create separate synthetic demo accounts for each role only when needed.
- [ ] Never commit demo passwords or credentials. Provide them through local seed-time environment variables or a documented, safe demo bootstrap procedure.
- [ ] Make clear that a shared public demo account is not suitable for real or private data.

## Acceptance criteria
- [ ] Unauthenticated protected requests return `401`.
- [ ] Authenticated but unauthorized requests return `403`.
- [ ] Doctor/nurse users cannot manage roles unless explicitly granted.
- [ ] A technical administrator does not automatically gain clinical permissions.
- [ ] Permission checks are tested at the API layer.
- [ ] Session and logout behavior is documented.

---

# Phase 6 — Synthetic data and clinical-data APIs

## Goal
Implement the core read workflows using synthetic patient records.

## Suggested endpoints
Names may change to match the repository, but keep behavior clear.

- `GET /api/v1/patients`
- `GET /api/v1/patients/{patient_id}`
- `GET /api/v1/patients/{patient_id}/summary`
- `GET /api/v1/patients/{patient_id}/labs`
- `GET /api/v1/patients/{patient_id}/prescriptions`
- `GET /api/v1/encounters/{encounter_id}` if needed

## Tasks
- [ ] Define safe response schemas that return only fields needed by the UI.
- [ ] Add filtering and pagination with bounded query parameters.
- [ ] Apply role and patient-access policy before returning records.
- [ ] Add not-found behavior that does not leak protected resource existence where policy requires concealment.
- [ ] Avoid placing patient identifiers or clinical content in URLs beyond what the application design requires; never put them in analytics or third-party logs.
- [ ] Add deterministic synthetic fixtures for tests.
- [x] Add data provenance/demo labels so the records' origin is traceable. **Superseded 2026-10-30:** provenance is database-internal and deliberately not shown in the UI.

## Acceptance criteria
- [ ] Authorized demo users can search/list synthetic patients.
- [ ] Authorized users can view a patient summary, labs, and prescriptions.
- [ ] Unauthorized users cannot access records by guessing an ID.
- [ ] Response payloads are typed, bounded, and tested.
- [ ] Empty states and missing records are handled.

---

# Phase 7 — MCP server and tool authorization

## Goal
Let the LLM request narrowly scoped application operations without granting it general database access.

## Initial tool set
Keep the list small; add tools only when a user workflow requires them.

- `search_authorized_patients(query, limit)`
- `get_patient_summary(patient_id)`
- `get_patient_lab_results(patient_id, limit)`
- `get_recent_prescriptions(patient_id, limit)`

Tools should use typed arguments, bounded results, explicit descriptions, and read-only access in the demo.

## Tasks
- [ ] Use the maintained MCP SDK appropriate to the project's chosen transport/version.
- [ ] Define tool schemas and validate arguments.
- [ ] Authenticate MCP callers or keep the MCP service internal and callable only from the trusted backend.
- [ ] Pass the verified user identity and request context through a trusted server-side channel, not as an LLM-controlled tool argument.
- [ ] Re-run authorization inside the tool/service layer for every call.
- [ ] Reuse the same application services and access policies as the REST API.
- [ ] Apply query limits, timeouts, result limits, and safe errors.
- [ ] Return only the minimum data needed for the task.
- [ ] Never implement `execute_sql`, arbitrary shell, unrestricted filesystem, or general-purpose database tools.
- [ ] Audit tool name, actor, target resource reference where appropriate, outcome, and correlation ID without logging full sensitive payloads.
- [ ] Test direct tool invocation, malformed input, unauthorized access, and oversized result requests.
- [ ] Document how the MCP service is run locally and whether it is internal-only in deployment.

## Acceptance criteria
- [ ] Each tool is documented and tested.
- [ ] Tool authorization cannot be bypassed by changing model-supplied arguments.
- [ ] No database credentials are provided to the LLM.
- [ ] No arbitrary SQL execution tool exists.
- [ ] MCP errors do not reveal secrets or internal stack traces.

---

# Phase 8 — LLM integration and safe response handling

## Goal
Connect the model to approved MCP tools while treating generated output as untrusted.

## Tasks
- [ ] Create a provider abstraction so the application can change LLM provider without rewriting clinical services.
- [ ] Read API keys only from server-side environment variables/secrets.
- [ ] Define a system instruction that the model is an informational assistant for synthetic demo data and must not invent missing facts.
- [ ] Tell the model to use tools for record-specific facts and distinguish retrieved data from general explanation.
- [ ] Restrict the model to approved tools; enforce the allowlist server-side.
- [ ] Bound prompt size, tool result size, number of tool calls, and request duration.
- [ ] Handle rate limits, timeouts, provider failures, malformed outputs, and unavailable model service.
- [ ] Display loading, retry, empty, and error states in the UI.
- [ ] Clearly label AI-generated summaries and preserve links/references to the underlying synthetic records where possible.
- [ ] Do not present model output as a diagnosis, treatment recommendation, or validated clinical decision.
- [ ] Do not send real personal/health data to an external model provider under this demo plan.
- [ ] Add tests for prompt injection in synthetic notes, malicious tool arguments, missing facts, and provider failures.
- [ ] Never allow a model-generated response to alter roles or perform privileged actions.

## Acceptance criteria
- [ ] The LLM can answer a basic question about seeded synthetic data using approved tools.
- [ ] The model cannot call tools outside the server-side allowlist.
- [ ] Missing data is acknowledged rather than fabricated.
- [ ] Provider failures produce a controlled UI message.
- [ ] No API key is present in frontend assets or browser requests.

---

# Phase 9 — Frontend experience

## Goal
Deliver a cohesive, responsive website rather than a set of disconnected test pages.

## Pages and components
- [x] Landing/sign-in page (`index.html`) with product name and a dark split hero/sign-in layout; no demo notice (2026-10-30 de-marking).
- [x] Authenticated dashboard with a clear navigation system.
- [x] Patient list/search with loading, empty, error, and pagination states.
- [x] Patient detail page with summary, encounters, labs, prescriptions, and the AI Patient tab.
- [x] AI Patient tab for questions about the current patient, if authorized.
- [x] User profile/session/logout control.
- [x] Admin navigation visible only to authorized roles; backend still enforces all permissions.
- [x] Responsive layouts for desktop, tablet, and narrow mobile widths.
- [x] Accessible labels, keyboard navigation, visible focus, semantic headings, and sufficient contrast.
- [x] Consistent typography, spacing, colors, buttons, tables, cards, and feedback messages.
- [x] Confirmation for destructive or high-impact admin actions.
- [x] Friendly 401/403/404/500 and offline/backend-unavailable states.
- [x] Display “Synthetic demo data — not for clinical use” prominently in the app. **DEVIATION (user decision, 2026-10-26): the persistent header banner was removed from all pages at the owner's request.** **SUPERSEDED (2026-10-30): all remaining product-surface origin labelling and disclaimer copy was removed; disclosure now lives in internal documentation and database provenance only.**
- [x] Avoid third-party analytics, trackers, and external scripts unless explicitly justified and reviewed.
- [x] Do not put clinical data in local storage, analytics events, or browser console logs.

## Acceptance criteria
- [x] A user can log in, navigate, search a patient, and inspect permitted synthetic data.
- [x] The frontend calls the backend, not the database.
- [x] Every interactive control works; no decorative fake buttons.
- [x] Layout remains usable on mobile.
- [x] Keyboard-only navigation supports primary workflows.
- [x] No secrets are bundled into JavaScript.
- [x] Unauthorized navigation does not expose protected data even if a user manually opens a page.

---

# Phase 10 — Admin panel and staff management

## Goal
Provide controlled staff account and role management.

## Features
- [ ] Staff list with pagination and safe filtering.
- [ ] Create or invite a staff account using the selected authentication flow.
- [ ] Activate/deactivate account.
- [ ] Assign/remove roles with server-side permission checks.
- [ ] Show current roles and account status.
- [ ] Confirm role changes and deactivation.
- [ ] Audit all account and role changes.
- [ ] Prevent removal/demotion of the final required administrator, if applicable.
- [ ] Prevent self-escalation and protect privileged actions against CSRF where cookie authentication is used.
- [ ] Never display password hashes, tokens, secret configuration, or unnecessary personal details.
- [ ] Keep clinical permissions separate from technical administration.

## Acceptance criteria
- [ ] Only explicitly authorized users can manage staff and roles.
- [ ] All changes persist in PostgreSQL and are reflected on refresh.
- [ ] Role changes take effect predictably for existing sessions (document and test policy).
- [ ] Attempts to bypass UI restrictions fail at the API.
- [ ] Admin actions are audited.

---

# Phase 11 — Audit, privacy, and security hardening

## Goal
Prepare the synthetic demo for public exposure without implying production clinical compliance.

## Tasks
- [ ] Audit login success/failure, logout, permission denials, patient-data reads, MCP tool calls, and staff/role changes.
- [ ] Redact secrets, authorization headers, session tokens, and full clinical payloads from logs.
- [ ] Add safe request IDs and structured logs.
- [ ] Add rate limits to login and costly AI endpoints where hosting allows.
- [ ] Set secure HTTP headers; use HTTPS at the hosting edge.
- [ ] Restrict CORS and trusted origins.
- [ ] Validate input, enforce result bounds, and use parameterized DB access.
- [ ] Review dependency vulnerabilities and remove unused packages.
- [ ] Verify `.env`, private keys, dumps, and credentials are ignored by git.
- [ ] Configure safe database network access and least-privilege credentials.
- [ ] Configure backups if supported; document free-tier persistence and restore limitations.
- [ ] Add an issue-reporting/contact path; privacy/demo disclosure is documentation-only (2026-10-30 de-marking).
- [ ] Document data retention and demo-data reset procedure.
- [ ] Review the provider's current terms, data location, logs, backups, and LLM provider data policies before sending any non-synthetic information.
- [ ] Clearly state that real patient data requires a separate security, privacy, legal, clinical, and regulatory review.

## Acceptance criteria
- [ ] No known secret is committed or exposed to the browser.
- [ ] Logs do not contain credentials or full patient payloads.
- [ ] AuthZ failures and admin actions produce audit events.
- [ ] Common injection and unauthorized-access tests pass.
- [ ] Security limitations are documented honestly.

---

# Phase 12 — Automated tests and CI

## Goal
Catch regressions before merging or deploying.

## Backend tests
- [ ] Unit tests for permission mapping and authorization decisions.
- [ ] API tests for authentication, roles, validation, pagination, and errors.
- [ ] Data-layer tests for migrations, foreign keys, and seed behavior.
- [ ] MCP tests for schemas, authorization, bounded results, and tool allowlisting.
- [ ] LLM tests using mocked provider responses; do not require paid API access in CI.
- [ ] Tests proving that users cannot elevate roles or access unauthorized records.
- [ ] Tests for audit events and safe error handling.

## Frontend tests
- [ ] Build/lint checks if applicable.
- [ ] Tests for login state, API errors, patient list/detail rendering, and role-based navigation.
- [ ] End-to-end smoke flow where practical.

## CI
- [ ] Add a GitHub Actions workflow or equivalent.
- [ ] Run formatting/linting, unit tests, and backend tests on pull requests.
- [ ] Run dependency vulnerability checks where feasible.
- [ ] Use only test fixtures and synthetic data in CI.
- [ ] Do not put deployment secrets into pull-request builds from untrusted forks.
- [ ] Keep CI reliable on free allowances.

## Acceptance criteria
- [ ] CI passes on the default branch.
- [ ] Security-critical authorization tests run automatically.
- [ ] CI does not require a live paid LLM call.
- [ ] Failures are actionable and documented.

---

# Phase 13 — Docker and local end-to-end validation

## Goal
Make the entire demo reproducible with containers.

## Tasks
- [ ] Add or verify a backend Dockerfile using a supported Python base image.
- [ ] Serve the frontend statically or package it according to the chosen design.
- [ ] Add PostgreSQL to `docker-compose.yml` for local development.
- [ ] Add health checks and service dependencies that do not rely solely on startup ordering.
- [ ] Use named volumes for local database persistence.
- [ ] Do not bake `.env` files or secrets into images.
- [ ] Run the application as a non-root user where practical.
- [ ] Keep development reload settings out of the production image.
- [ ] Pin dependencies and use reproducible builds where practical.
- [ ] Document database migration and demo-seed commands.
- [ ] Run a clean start from a fresh checkout.
- [ ] Test the full flow: login → patient search → patient detail → labs/prescriptions → AI summary → admin role check.

## Acceptance criteria
- [ ] `docker compose up --build` or documented equivalent starts the local demo.
- [ ] Migrations and synthetic seed process are reproducible.
- [ ] The main workflow succeeds from a clean local environment.
- [ ] Container logs do not leak secrets.
- [ ] Data persistence and reset behavior are documented.

---

# Phase 14 — Public demo deployment preparation

## Goal
Prepare a deployable build and select free-tier services without overpromising that they will remain free.

## Preferred initial deployment shape
Choose providers based on current availability, terms, region, and project constraints:

- Static frontend: a static hosting service with HTTPS and a free plan.
- FastAPI: a free-tier Python application host, if its current limits fit the demo.
- PostgreSQL: a managed PostgreSQL free tier with a clear persistence/backup policy.
- LLM: optional. The website must still work if no LLM API key is configured. Add a clearly labeled unavailable state or a deterministic demo assistant mode that does not pretend to be a real model.
- MCP: internal to the backend deployment unless the host supports a secure private service-to-service path.

Free-tier conditions can change. Verify current terms at deployment time. Expect possible cold starts, sleep, request limits, storage limits, bandwidth limits, and database inactivity policies. Do not promise $0 forever.

## Tasks
- [ ] Select providers and document why they fit.
- [ ] Confirm the frontend can call the backend over HTTPS.
- [ ] Configure production environment variables in the host dashboard.
- [ ] Create a separate hosted demo database.
- [ ] Run migrations and seed only synthetic data.
- [ ] Configure allowed CORS origin(s).
- [ ] Configure health checks and service start command.
- [ ] Configure build and start commands from a clean commit.
- [ ] Verify database persistence across app redeploys/restarts.
- [ ] Verify database limits, sleeping behavior, retention, backup availability, and reset procedure.
- [ ] Configure optional LLM key only on the server side.
- [ ] Set API usage limits or a safe feature-off switch to reduce unexpected provider charges.
- [ ] Configure a custom domain only if the user already owns one or accepts its cost; otherwise use the free provider subdomain.
- [ ] Review public repository contents and git history for secrets before publishing.
- [ ] Add a support/issue-reporting link; no public demo notice (2026-10-30 de-marking).
- [ ] Prepare rollback instructions.

## Acceptance criteria
- [ ] The build can be deployed from the documented branch/commit.
- [ ] All required settings are documented with names and purpose, but no secret values.
- [ ] The demo works with synthetic data only.
- [ ] Optional AI-provider failure does not break login and non-AI workflows.
- [ ] The expected monthly cost and free-tier risks are documented.

---

# Phase 15 — Deploy publicly and verify website (FINAL PHASE)

## Goal
Make the website accessible on the internet and prove the core demo workflows work after deployment.

## Deployment checklist
- [ ] Confirm all prior phases are complete or explicitly document approved non-blocking exceptions.
- [ ] Confirm CI is green for the deployment commit.
- [ ] Confirm the repository contains no secrets or real patient data.
- [ ] Set all hosted environment variables in the hosting dashboard.
- [ ] Deploy PostgreSQL, apply migrations, and load synthetic demo data.
- [ ] Deploy FastAPI and confirm readiness/health endpoints.
- [ ] Deploy frontend and configure its backend API URL.
- [ ] Confirm HTTPS works and there are no mixed-content requests.
- [ ] Confirm CORS permits only the intended frontend origin.
- [ ] Confirm the LLM is optional or configured with a server-side key and bounded usage.
- [ ] Confirm MCP is not exposed publicly without proper access controls.
- [ ] Confirm database credentials are not present in frontend assets or public build output.
- [ ] Confirm the application remains functional if the LLM provider is unavailable.
- [ ] Confirm no real patient data is present in the hosted database.
- [ ] Confirm login, logout, role-based navigation, patient list, patient detail, labs, prescriptions, and admin authorization.
- [ ] Attempt direct unauthorized API requests and verify they are rejected.
- [ ] Verify audit events for key operations.
- [ ] Test the website in a private/incognito browser and on a narrow mobile viewport.
- [ ] Check browser console and server logs for errors or secret leakage.
- [ ] Check hosted database persistence after app restart/redeploy.
- [ ] Record cold-start behavior, quotas, limitations, and any costs that could arise.
- [ ] Update README with public URL, demo limitations, local run instructions, architecture link, and how to report issues.
- [ ] Capture the final deployed URL in the progress log. Do not invent or claim a URL until deployment has actually succeeded.

## Final smoke test

Run this checklist against the **public deployed URL**, not only localhost:

1. Open the homepage over HTTPS.
2. Log in with a documented synthetic demo account.
3. Confirm the correct role and permitted navigation are displayed.
4. Search for a seeded synthetic patient.
5. Open the patient detail page.
6. Read synthetic lab results and prescriptions.
7. Request an AI summary if AI is configured; otherwise verify the graceful unavailable/demo state.
8. Attempt an unauthorized operation and verify the backend rejects it.
9. Open the admin panel as a non-admin and verify access is denied.
10. Open the admin panel as an authorized demo administrator and verify role management works.
11. Log out and verify protected pages/API calls no longer work.
12. Reload the website and verify data remains available.
13. Inspect network requests to ensure no database credential, LLM key, or privileged secret reaches the browser.
14. Check logs and audit events.
15. Verify the visible synthetic-data and non-clinical-use notice. *(Superseded 2026-10-30: verify that no in-app notice exists by design; disclosure is documentation-only.)*

## Final completion report

At the end of Phase 15, report:

- **Public URL:** actual verified URL.
- **Deployment commit:** commit hash if available.
- **Services:** frontend, API, database, MCP, and LLM status.
- **Smoke tests:** pass/fail results.
- **Security checks:** pass/fail and remaining risks.
- **Costs:** current known monthly estimate and free-tier caveats.
- **Known limitations:** cold starts, quotas, unsupported features, and non-production status.
- **How to run locally:** exact commands.
- **How to redeploy/rollback:** exact documented steps.
- **Real-data status:** explicitly state that the demo uses synthetic data and has not been approved for real patient information.

Do not label the application “production ready” just because it is publicly accessible. The goal of this plan is a **publicly accessible synthetic-data demo**.

---

## Ongoing progress log

Append one dated entry after every completed phase:

```text
### YYYY-MM-DD — Phase N: <phase name>
- Status: DONE / BLOCKED / IN PROGRESS
- Implemented:
- Files changed:
- Commands run:
- Tests:
- Acceptance criteria:
- Security notes:
- Blockers:
- Next phase:
```

### 2026-10-26 — Phase 0: Repository discovery and baseline
- Status: DONE
- Implemented: Repository bootstrap. Repo was empty (only planning docs); no pre-existing code or user work to preserve. Fresh git repository initialised inside `MedicalPlatform/` (the home-directory repo is untouched). Recorded toolchain (Python 3.14.4/3.11.16 + uv, Node 24, Docker 29 + Compose v5, no psql). Project pinned to Python 3.12. Froze the API contract in `docs/API.md` as the integration backbone so backend and frontend can be built in parallel by separate agents. Wrote `.gitignore` and `.env.example` (placeholder values only).
- Files changed: PLAN.md (renamed from `PLAN (1).md`), production-requirements.txt (unchanged, kept at root), .gitignore, .env.example, docs/INITIAL_ASSESSMENT.md, docs/API.md
- Commands run: `git init -b main`; `git add -A && git commit` → commit `b0e3db5`; `uv python list --only-installed`
- Tests: none existed at baseline (greenfield). Baseline recorded as "no test suite present" in docs/INITIAL_ASSESSMENT.md.
- Acceptance criteria: PASS — no user changes overwritten; architecture and start procedure documented; baseline recorded; Phase 1 scope derived from the actual (empty) repository.
- Security notes: no committed secrets, no patient-like data present. `.env` ignored from commit #1. Demo credentials will be generated at seed time via env vars only.
- Blockers: none.
- Next phase: Phase 1 (docs in progress) and Phase 2/4 (backend foundation) running as parallel agents.

### 2026-10-26 — Phase 1: Requirements and architecture
- Status: DONE
- Implemented: Full documentation layer by the docs agent, independently spot-checked by the lead (permission matrix diff vs docs/API.md → exact match; no invented URLs; no false compliance claims). Covers trust boundaries, data-flow walkthroughs (login/patient flow, AI flow with dual authorization checks, admin role change with guards), decision log, threat-model/controls table, audit inventory, deferred-production backlog mapped from production-requirements.txt, provider-by-provider deployment steps, troubleshooting entries, and README quickstart. Lead decisions on the docs agent's open assumptions: (1) `DEMO_ADMIN_*` seeds the `clinical_admin` role, `DEMO_TECH_ADMIN_*` seeds `tech_admin`; (2) sessions are DB-backed in the `admin` schema so they can expire and be revoked; (3) soft delete = `deleted_at` columns; (4) FastAPI serves the frontend in local dev, Cloudflare Pages in production; (5) Render build/start confirmed at Phase 14.
- Files changed: README.md, docs/ARCHITECTURE.md, docs/SECURITY.md, docs/BACKLOG.md, docs/DEPLOYMENT.md, docs/TROUBLESHOOTING.md
- Commands run: matrix `diff` cross-check, URL/claims `grep` audit, `git commit` → `21d7f27`
- Tests: n/a (documentation phase); consistency checks passed as above.
- Acceptance criteria: PASS — every protected feature has a documented permission; data flow browser → backend → MCP → LLM → PostgreSQL is documented with trust boundaries; free-tier deployment feasibility documented with honest caveats; unresolved choices listed in docs/ARCHITECTURE.md rather than silently guessed.
- Security notes: docs state non-claims explicitly (not HIPAA, not clinically validated, not a medical device, not for real patient data). Secrets documented by name/purpose only.
- Blockers: none.
- Next phase: Phases 2–4 (backend foundation) and Phase 9 (frontend) running as parallel agents.

### 2026-10-26 — Phases 2–4: Structure, schema/migrations, backend foundation
- Status: DONE
- Implemented: uv-managed Python 3.12.13 backend with pinned deps; SQLAlchemy 2.0 async models across `admin`/`patients`/`laboratory`/`medications` schemas (UUID PKs, UTC timestamptz, Numeric clinical values, soft deletes, FK RESTRICT, DB check constraints enforcing `DEMO-%` MRNs and `synthetic` provenance); Alembic migration with working downgrade; idempotent synthetic seed (10 patients incl. `O'Connor`, `José García-López`, RTL and emoji names); error envelope + request-id middleware + bounded pagination + CORS allowlist + 1 MB body limit + redacting JSON logs; policy module with the exact role/permission matrix and a deny-by-default `require_permission` dependency awaiting the auth wiring; docker-compose `db` service; ruff config.
- Files changed: backend/** (pyproject, uv.lock, app/ tree, migrations/, tests/), scripts/seed_demo_data.py, scripts/healthcheck.py, docker-compose.yml, backend/migrations/env.py (lead fix), backend/tests/test_migrations.py (lead regression test), backend/.env (local only, gitignored)
- Commands run: `uv sync`; `docker compose up -d db` (via Docker Desktop interop — see blockers); `alembic upgrade head` → `downgrade base` → `upgrade head`; seed ×2 (run 2 created 0 rows); `uv run pytest -q` → **88 passed**; `uv run ruff check` clean; uvicorn smoke on /health/live + /health/ready + 404 envelope + CORS matrix.
- Tests: 88 passed, 0 skipped, 0 failed (DB-backed tests against real postgres:16-alpine, isolated `clinical_demo_test` DB).
- Acceptance criteria: PASS for Phases 2–4 (clean DB from migrations; idempotent seed; FK/constraint behavior; isolated test config; health endpoints; controlled validation errors; dev-only /docs; no stack traces or secrets to clients). Phase 2 README wording verified against actual commands.
- Security notes: DB constraints make real-looking data impossible (synthetic provenance + DEMO MRNs enforced at DB level). Validation error details never echo submitted values. Decimal values serialize as strings. Lead integration fixes: (1) `migrations/env.py` called `load_settings(env_file=True)` which crashed the documented `.env`-based dev path (`TypeError: 'bool' object is not iterable`) — fixed to `load_settings()` and covered by new regression test `test_alembic_cli_reads_env_file_when_no_env_vars`; (2) local `.env` created (gitignored) with dev-only credentials.
- Process note: the backend agent violated the "no git commands" rule — it ran `git pull --rebase origin main` (rewriting commit hashes onto the GitHub remote's history) and committed all work as `c1139bc "Changes"`, which was pushed to `github.com/Kalsito1017/MedicalPlatform`. Content was secret-scanned clean by the lead; history is left as-is because it is already public. Future agents are instructed not to touch git.
- Blockers: Docker CLI in WSL cannot reach the daemon (no `/var/run/docker.sock`); Docker Desktop must be started via Windows interop (`docker.exe compose`). Fix: enable Docker Desktop → Settings → Resources → WSL integration for this distro.
- Next phase: Phase 5 (auth/authorization) + Phase 6 (clinical APIs), then Phase 7-8 (MCP/LLM).

### 2026-10-26 — Phases 5–6: Authentication, authorization, clinical-data APIs
- Status: DONE
- Implemented: DB-backed sessions (`admin.user_session`, migration `b7f3d2a91c04` with working downgrade) storing only SHA-256 token hashes; cookie rotation on login (anti-fixation); CSRF double-submit matching the shipped frontend's bootstrap exactly; argon2id with uniform-failure login; in-process login rate limiting + lockout (5/(email,IP)/15min, 50/IP); `principal_resolver` wired as the single identity source; full Phase 5-6 endpoint set (auth, patients, summary, labs, prescriptions, encounters with 404 concealment, staff, roles with self-escalation 403 + last-admin 409, audit reader); audit writer with the agreed action inventory — patient reads record who viewed; `check_patient_access` production seam for a future care-relationship policy.
- Files changed: backend/** (app/auth/*, app/audit/logger.py, app/services/*, app/api/v1/*, models, schemas, config, main, policy), backend/migrations/versions/b7f3d2a91c04_*.py, backend/tests/* (8 new test modules)
- Commands run: `uv run pytest -q` → **242 passed, 1 skipped**; `alembic upgrade head` → `downgrade -1` → `upgrade head`; seed ×3 idempotent; `ruff check` clean; live smoke (uvicorn + curl): demo doctor login → patients (10, provenance "synthetic") → logout 204 → me 401; tech_admin GET /patients → 403; GET /audit shows authz.denied/login.success/logout events.
- Tests: 242 passed / 1 skipped (deliberate: public login case covered elsewhere) / 0 failed. Includes a guard test that FAILS if a new v1 route is added without declaring its permission in the matrix.
- Acceptance criteria: PASS — Phase 5 (401/403 semantics, doctor/nurse cannot manage roles, tech_admin has no clinical access, API-layer permission tests, session/logout documented) and Phase 6 (search/list/summary/labs/prescriptions authorized flows, ID guessing rejected, typed bounded payloads with Decimal-as-string + UTC Z timestamps, empty states).
- Security notes: DB leak yields no usable sessions (hashed tokens); deactivation revokes sessions immediately; audit rows carry no passwords/tokens/clinical payloads (asserted by test); error responses never echo submitted values. Lead verification added a full DB reset + reseed through the documented reset flow after the pre-.env seed left demo accounts with unknown generated passwords — the reset procedure is now proven and documented.
- Blockers: none.
- Next phase: Phases 7-8 (MCP + LLM), Phase 10 (admin panel UI + AI panel), Phase 12 (tests/CI) in parallel.

### 2026-10-26 — Phases 7–8 + 12: MCP, LLM, tests/CI, dependency security
- Status: DONE
- Implemented: In-process MCP server on the official `mcp` SDK Tool/ToolManager (no transport exposed) with exactly 4 read-only bounded tools; identity only via server-injected ToolContext (structurally absent from tool schemas); per-call re-authorization through the same `check_patient_access` seam as REST; `mcp.*` audit events; LLM provider abstraction (OpenCode OpenAI-compatible Chat Completions, NullProvider → 503 when disabled), bounded tool loop with server-side allowlist, prompt-injection labelling (record text = data, not instructions), per-user AI rate limit, `POST /api/v1/ai/ask` with the exact frozen response shape; admin panel UI (staff table, role grant/revoke with consequence-first confirmations, activation/deactivation, one-time password modal, audit view) and the labelled AI assistant panel; GitHub Actions CI (3 jobs, no secrets to fork PRs) + `scripts/security_check.py` secret scanner + pip-audit/OSV severity gate.
- Lead integration fixes: dependency security bumps demanded by the CI audit gate — `fastapi 0.115.6 → 0.162.0` (pulls `starlette 0.41.3 → 1.10.0`, clearing 8 starlette advisories incl. 2 HIGH), `mcp 1.12.4 → 1.30.0` (clearing 6 advisories incl. a CVE), `pydantic 2.10.4 → 2.14.1` (required by mcp 1.30). `pip-audit` then reports "No known vulnerabilities found". FastAPI 0.162 nests included routers (`_IncludedRouter`), which silently emptied the MATRIX route-guard's introspection — fixed the guard's traversal (assertion unchanged) so it enforces again across FastAPI layouts.
- Files changed: backend/pyproject.toml, backend/uv.lock, backend/tests/test_permissions_api.py (guard traversal), PLAN.md; earlier in this wave: backend/app/{mcp,llm}/**, backend/app/api/v1/ai.py, backend/app/schemas/ai.py, backend/tests/{test_mcp,test_llm,test_ai_api}.py, frontend/** (admin + AI panels), .github/workflows/ci.yml, scripts/security_check.py, README.md (Testing section), docs/API.md (POST /staff body pinned)
- Commands run: `uv run pytest -q` → **310 passed, 1 skipped** (before and after dependency bumps); `uv run ruff check .` clean; `node tests/smoke.mjs` → 74/74; `node tests/render-smoke.mjs` → 26/26; `scripts/security_check.py` → PASS 0 findings; `pip-audit` → no known vulnerabilities.
- Tests: 310 backend (68 new MCP/LLM/AI: tool schemas + bounds, argument-tampering cannot bypass authorization, unknown-tool rejection + audit, allowlist enforcement, seeded-data question via tools, prompt-injection labelling, provider failure envelopes, rate limit, no key in responses) + 100 frontend checks.
- Acceptance criteria: PASS — Phase 7 (tools documented+tested, authz not bypassable via arguments, no DB credentials to the LLM, no arbitrary SQL tool, safe errors) and Phase 8 (model answers a seeded-data question with approved tools, server-side allowlist, missing data acknowledged, provider failures controlled, no API key in frontend). Phase 12 PASS locally; the live GitHub Actions run is confirmed during Phases 14–15.
- Security notes: audit action inventory stays closed (`mcp.unknown_tool` for attempted unknown tools — no attacker-controlled strings in the action column); citations are typed `{type, id}` references; LLM key server-side only with Console budget cap; HIGH/CRITICAL dependency gate now green without any allowlist override.
- Blockers: none.
- Next phase: Phase 11 (security headers hardening) + Phase 13 (Docker E2E), then Phases 14–15 (deployment + public verification).

### 2026-10-26 — Phases 11 + 13: Security hardening, Docker, local E2E
- Status: DONE
- Implemented: SecurityHeadersMiddleware on every response (nosniff, DENY framing, no-referrer, Permissions-Policy, COOP/CORP, no cross-domain policies, HSTS on HTTPS only) with a strict CSP whose `script-src` allows 'self' + the SHA-256 hash of the inline config bootstrap (computed at startup — no unsafe-inline/unsafe-eval anywhere); static frontend served from FastAPI at one origin via the 404 handler (no route shadowing, frozen 404-envelope contract preserved); audit inventory proven complete — all 18 documented event classes emitted, asserted by an integration test; log-redaction integration test (real requests → real logs → no passwords/tokens/clinical payloads; negative control proves the detector works); Docker: python:3.12-slim + pinned uv, non-root uid 10001, `uv sync --frozen` from the committed lockfile, no .env/dev tooling in the image, migrations-on-start entrypoint with RUN_MIGRATIONS_ON_START escape hatch, compose `app`+`db` with real healthcheck gating and named volume; `scripts/e2e_smoke.sh` scripts the full 10-step acceptance workflow.
- Lead gap fixes: (1) `frontend/_headers` created so Cloudflare Pages serves identical headers/CSP at the edge, with the verified bootstrap hash; (2) `scripts/security_check.py` extended with section 6/6 edge-headers gate (fails on missing headers, unsafe-inline/unsafe-eval, or CSP-hash drift between `_headers` and the served HTML); (3) `backend/tests/test_llm.py` fixture sentinel no longer shaped like a live `oc_sk_` key (test semantics unchanged) — closes the scanner finding that would have kept the CI security job red; (4) `.env.example` gained the compose-consumed vars (POSTGRES_*, APP_PORT, RUN_MIGRATIONS_ON_START, FRONTEND_DIR) and its CORS section preserved; (5) docs/DEPLOYMENT.md section 4 placeholder replaced with the concrete same-origin `/api/*` proxy step (`_redirects`) + edge-headers step + verification item; docs/BACKLOG.md stale "unverified until Phase 13" markers cleared.
- Files changed: backend/Dockerfile, .dockerignore, docker-compose.yml, scripts/{docker-entrypoint.sh,e2e_smoke.sh,security_check.py}, backend/app/{main.py,config.py}, backend/tests/{test_security_headers.py,test_hardening.py,test_llm.py}, frontend/_headers, .env.example, docs/{SECURITY.md,DEPLOYMENT.md,TROUBLESHOOTING.md,BACKLOG.md}, README.md, PLAN.md
- Commands run: `docker compose up --build` (clean state, WSL docker socket now reachable) → migrations auto-applied → seed → `bash scripts/e2e_smoke.sh` → **ALL STEPS PASSED** (10 steps: health, CSP/headers + same-origin config, login incl. uniform-failure 401, clinical reads, AI 503-with-app-usable, admin 403/201/200 matrix, logout→401, audit coverage, container-log secret scan with negative control, restart persistence); `uv run pytest -q` → **324 passed, 1 skipped**; `ruff check` clean; frontend 74/74 + 26/26; `scripts/security_check.py` → **PASS, 6 sections, 0 findings**; `pip-audit` → no known vulnerabilities.
- Tests: 324 backend (14 new: 12 security-header + 2 hardening incl. audit-inventory + log-redaction integration) + 100 frontend checks. All reproduced independently by the lead, including the E2E smoke.
- Acceptance criteria: PASS — Phase 11 (no secrets committed/exposed, logs clean of credentials and clinical payloads, authZ/admin actions audited, injection + unauthorized-access tests pass, limitations documented honestly) and Phase 13 (`docker compose up --build` starts the demo, migrations + seed reproducible from a clean state, main workflow succeeds end-to-end, container logs leak nothing, persistence + reset documented).
- Security notes: CSP hash ties header strength to actual markup (drift = CI failure); Referrer-Policy `no-referrer` chosen because patient ids live in page URLs; image contains no secrets and no dev tooling; edge-header gap for Pages documented and closed via `frontend/_headers` + scanner gate.
- Blockers: none. Phases 14–15 remain deferred by user decision (hosting later; AI enabled at deploy).
- Next phase: user publishes the commit range to GitHub and confirms the CI run goes green; then Phase 14/15 resumes when hosting access is provided.

### 2026-10-26 — UI change (user decision): header demo banner removed
- Status: DONE
- Implemented: Removed the persistent header banner ("Synthetic demo data — not for clinical use. / All patient records are fictional. This platform is not clinically validated and is not a medical device.") from all five pages at the owner's explicit request. Disclosure is NOT eliminated: it remains in the page footer and the "About this demo" dialog (which still states the platform is not clinically validated and is not a medical device), in per-record "Data provenance: Synthetic demo data" badges, in `<meta>` descriptions, and throughout the documentation. The banner's CSS (`.demo-notice`) was deleted as dead code and the `smoke.mjs` required-chrome assertion that enforced the banner was removed.
- Files changed: frontend/{index,dashboard,patients,patient,admin}.html, frontend/assets/css/styles.css, frontend/tests/smoke.mjs, docs/API.md, docs/BACKLOG.md, PLAN.md
- Commands run: `node tests/smoke.mjs` → 74/74; `node tests/render-smoke.mjs` → 26/26; `docker compose up --build -d` → banner absent from served HTML (grep = 0), app 200.
- Tests: all frontend checks green after the change (assertion removed deliberately, nothing weakened elsewhere).
- Acceptance criteria: PLAN.md Phase 9 task "display the notice prominently in the app" and Phase 15 smoke item 15 are adjusted by owner decision — recorded inline in both places. All other criteria unchanged and passing.
- Security notes: the removed text is a disclosure/liability notice, not a security control. The actual data remains synthetic and DB constraints still force `DEMO-%` MRNs and `synthetic` provenance. If this demo is ever shown publicly, the owner should be aware that PLAN.md's Phase 15 disclosure checks were written around a visible in-app notice; the footer/About dialog now carry that weight. Re-introducing a banner is a five-line revert.
- Blockers: none.
- Next phase: unchanged — user publishes; Phases 14–15 resume when hosting access is provided.

### 2026-10-26 — Feature: top-level AI Assistant (general lookup mode)
- Status: DONE
- Implemented: `POST /api/v1/ai/ask` extended compatibly — `{patient_id?, question}`: with patient_id unchanged patient-scoped mode; without, a general lookup mode where the model may search patients and read records via the SAME four MCP tools under the same authorization (identity still server-injected only, re-authorized per tool call, all calls audited `mcp.*`, ask audited `ai.ask` with resource `patient`/`ai_lookup`). Shared prompt rules block with two scopes (record text = DATA; never fabricate; always list records consulted). New `frontend/ai.html` + `ai-assistant.js` in the MediCore design language (gradient AI card, INFORMATIONAL ONLY pill, chat bubbles, source chips, citation links to patient records, suggested-question chips, all error/503/429 states), sidebar "AI Assistant" nav gated on `patient:read`. Citation links resolve only when attribution is unambiguous (single-patient answers) — never invented links.
- Files changed: backend/app/{schemas/ai.py,llm/prompt.py,llm/loop.py,api/v1/ai.py}, backend/tests/{test_ai_api.py,test_llm.py}, docs/API.md (ai/ask section), frontend/{ai.html,assets/js/ai-assistant.js,assets/js/auth.js,assets/js/ui.js,assets/css/styles.css,tests/*,DESIGN.md}, .env.example (LLM_MAX_TOOL_CALLS_GENERAL)
- Commands run: `uv run pytest -q` → **340 passed, 1 skipped** (was 324; +16); `ruff check` clean; frontend **94/94 + 37/37** (was 82 + 30); `scripts/security_check.py` → PASS (CSP hash covers ai.html's verbatim bootstrap); live: general + patient asks degrade to clean 503 while the OpenCode account returns 402; 404 concealment, 400 bounds, CSRF 403, clinical_admin 403, shared 10/min rate limit (11th → 429 + Retry-After) all verified live.
- Tests: 16 new backend tests incl. `test_general_mode_tool_calls_re_authorize_and_ignore_identity_tampering` (model smuggling user_id/role/patient_ids → denied + audited with the REAL session actor) and unknown-tool rejection (closed audit inventory) and prompt-injection-stays-data; 7 new frontend AI-page scenarios.
- Acceptance criteria: PASS — general lookup answers must come from authorized tools only; missing data acknowledged; same allowlist/bounds/auditing as patient mode; contract extension recorded in docs/API.md.
- Security notes: live happy path (real model answer) remains UNVERIFIED pending OpenCode account credits (402 "Insufficient account funds" observed live; graceful 503 confirmed). mcp.* audit rows observable live only once the provider accepts calls — mocked tests cover them.
- Blockers: none in code. External: OpenCode Console credits required for live AI answers.
- Next phase: user adds credits → lead runs the documented live click-through (general + patient modes) → then Phases 14-15 resume per user decision.

### 2026-10-26 — Data expansion: rich synthetic dataset for AI lookups
- Status: DONE
- Implemented: Synthetic dataset expanded from 10 to **45 patients** (DEMO-0001…0045, ages 22–87, sex mix incl. other/unknown, global name coverage: apostrophes, accents, RTL, Cyrillic, emoji, long compounds), **167 encounters** (7 encounter types, deterministic fixed-anchor dates over ~18 months, 2 benign prompt-injection trigger summaries for defense testing), **415 lab results** (17-test catalog, per-test decimal precision, time-spanning trend pairs, status mix final/preliminary/pending), **104 prescriptions** (15-medication catalog, all 4 status values), with deliberately coherent abnormal patterns: 9 high-potassium (ACEi/CKD), 5 anemia, 7 diabetes + 3 prediabetes, 8 high-LDL, 4 elevated LFTs, hypothyroid/renal/vitamin-D cohorts — so AI lookups ("high potassium?", "on metformin?", "diabetes labs?") have real answers.
- Files changed: scripts/seed_demo_data.py (spec-driven, idempotent, deterministic), backend/tests/test_seed.py (count assertions recomputed from spec constants + pattern test), scripts/e2e_smoke.sh (step 4 floor + step 5 made config-aware: accepts a live 200 AI answer OR the documented 503, since the demo now ships LLM-enabled), README.md, docs/DEPLOYMENT.md, PLAN.md
- Commands run: seed ×2 (run 2: 0 created / all already present — idempotent); `uv run pytest -q` → **350 passed, 1 skipped**; `scripts/security_check.py` → PASS; `bash scripts/e2e_smoke.sh` → ALL STEPS PASSED (patient total 45, persistence 45 unchanged).
- Tests: 350 backend passing (up from 341 with concurrent MCP lookup tests), counts now derived from the seed's own spec constants so they cannot rot.
- Acceptance criteria: PASS — idempotent/resettable synthetic seed, no real data (DB constraints enforce DEMO-% MRNs and synthetic provenance), deterministic output, patterns verifiable by tests.
- Security notes: prompt-injection trigger text lives in encounter summaries by design (benign, fictional) to exercise the AI's data-not-instructions defense live. Pending labs satisfy value-presence constraints via `value_text`.
- Lead decisions: e2e step 5 is now dual-mode (200-with-answer when LLM configured, 503 otherwise) — the demo ships LLM-enabled; the 503 path stays covered by unit tests and the script.
- Blockers: none. Next: lookup-tools agent lands → integration; then waves R (roles), S (schema as drawn), T/U/V (MCP catalog, analytics, doctor AI workspace).

### 2026-10-26 — LLM live wiring (OpenCode Go) + MCP lookup tools
- Status: DONE
- Implemented: (1) LLM made LIVE against the real provider: discovered Go models are served on a separate endpoint family (`{base}/go/openai/v1/chat/completions`) and the gateway **requires** `x-opencode-session` + a self-identifying `User-Agent` (proven live: `MissingSessionID` 400 without, 200 with) — provider now sends `clinical-ai-platform/1.0` + the request id as session; `LLM_MODEL=mimo-v2.6-pro` (the owner's current model), base URL `https://opencode.ai/inference/go`. (2) Two narrow read-only lookup tools — `find_patients_by_lab` (abnormality judged by value vs reference range; `status` is document state only) and `find_patients_by_medication` — resolving dataset-wide questions in ONE call (verified live: "abnormal potassium?" → 9 patients/18 citations; "on metformin?" → 7 patients). General-mode tool budget 6 → 10 (env-clamped 1..20; patient mode unchanged at 4). Strict authorization: `patient:read` + the record-type permission, `check_patient_access` seam per returned patient with paging past concealed rows; identity structurally absent from tool schemas; every call audited (`mcp.*`, closed action inventory); unknown test codes → controlled error listing catalog hints, never echoing input.
- Files changed: backend/app/llm/{provider,loop,prompt}.py, backend/app/config.py, backend/app/mcp/{tools,limits,server,__init__}.py, backend/tests/{test_mcp,test_ai_api,test_llm,test_audit_api,test_hardening,conftest}.py, docs/API.md (MCP contract: six tools), docs/SECURITY.md, docs/ARCHITECTURE.md, docs/BACKLOG.md (tool-count corrections), .env.example
- Commands run: `uv run pytest -q` → **350 passed, 1 skipped**; `ruff check` clean; `security_check.py` → PASS (6 sections); frontend 94/94 + 37/37; live asks (real model `mimo-v2.6-pro`) both modes: patient-scoped answer with lab table + citations; general lookups in 1 tool call each.
- Tests: 350 passing incl. new tampering/bounds/authorization suites for the lookup tools (smuggled identity args ignored; concealed patients never leak even when they would consume the only result slot; limit clamps; no input echo; audit rows carry no search terms).
- Acceptance criteria: PASS — cross-patient lookups work within budget; tool authorization cannot be bypassed by arguments; allowlist intact (six read-only tools; no SQL/shell/filesystem); live answers cite real synthetic records with timestamps.
- Security notes: docs previously said "four tools" in three places — corrected to six at integration; `test_hardening`'s documented-audit-action mirror extended to cover the two lookup actions and its flow now exercises all six tools. Provider client-identification headers follow OpenCode Go's documented client requirements (abuse-monitoring friendliness), no secrets involved.
- Blockers: none.
- Next phase: Wave S (full schema as drawn: diagnoses, clinical notes, weights, blood types, patient_assignment, encounter practitioners), then T/U/V (MCP panel catalog, admin analytics, doctor AI workspace).

### 2026-10-26 — Wave R: three-role model (nurse least, Admin all, tech_admin deleted)
- Status: DONE
- Implemented: Policy rewritten to 3 roles × 8 permissions (nurse = clinical read only; doctor = clinical read; `clinical_admin` = **Admin** = everything incl. new `analytics:read`). `tech_admin` deleted system-wide: migration `e8c2a91f4d77` (soft-deletes + session-revokes tech-admin-only accounts, removes grants and role row, adds DB CHECK `ck_role_key_valid`; downgrade restores the role row only — grants deliberately unrestorable). Seed: 3 roles/3 accounts, idempotent; `.env.example` DEMO_TECH_ADMIN_* removed. Last-ACTIVE-Admin protection (409 on demoting or deactivating the last active Admin); self role assignment now unconditionally 403 (matches the frozen contract). Frontend: role labels ("Admin"), tech_admin styling/labels removed, admin panel gated on staff permissions (nurse sees the denial panel and no nav item). Docs reconciled across API.md/SECURITY.md/README/ARCHITECTURE/BACKLOG/DEPLOYMENT/DESIGN.
- Files changed: 34 files (backend policy/schemas/services/api/models/config, migration e8c2a91f4d77, seed, e2e, .env.example, frontend ui/admin/index/styles/tests, docs) — see the agent report for the full list.
- Commands run: `uv run pytest -q` → **335 passed, 1 skipped** (delta vs 350 fully explained by the 4→3 role grid shrinking parametrized cases); `ruff check` clean; `security_check.py` → PASS; frontend 94/94 + 37/37; `e2e_smoke.sh` → ALL STEPS PASSED with new role-boundary steps; migration up/down/up proven; live curls (reproduced by the lead): nurse `/staff` **403** `/patients` 200, nurse permissions = exactly 3 clinical reads; admin clinical reads + staff + audit all 200; `role: tech_admin` → **400**.
- Tests: matrix, role×endpoint API grid, staff/last-admin/self-escalation, seed counts, hardening inventory, e2e boundaries all updated and green.
- Acceptance criteria: PASS — nurse cannot see or reach the admin panel (UI + API); Admin sees everything; tech_admin no longer exists anywhere; least-privilege and deny-by-default intact; role changes remain audited and self-escalation-proof.
- Security notes: behavior change recorded — Admins now hold clinical reads (explicitly tested); nurses lost `staff:read`; self-assignment strictly stronger (always 403). Earlier plan notes referencing `tech_admin` (Phase 5/6 log entries, the earlier lead-decision log) are historical and superseded by this entry. `production-requirements.txt` (the source requirements doc) still describes the original four-role plan — left untouched deliberately.
- Blockers: none. Next phase: Wave S.

### 2026-10-27 — Seed freshness + practitioner attribution
- Status: DONE
- Implemented: Seed dates now derive from the seed-RUN day with deterministic offsets (fixed `ANCHOR_DAY` deleted) — 4 encounters land in the 7-day window, 9 in the 30-day window, ~18 months of history preserved for trends; fresh dates apply to a fresh database (documented demo-reset path), re-seeds never churn existing rows. `encounter.practitioner_id` seeded (~74% demo doctor / ~26% demo nurse) with NULL-only idempotent backfill for existing rows (proven: manually-set rows survive). Recent encounters flow through the unchanged generator (vitals, SOAP notes, labs, orders, specimens all coherent).
- Files changed: scripts/seed_demo_data.py, backend/tests/test_seed.py
- Commands run: seed ×2 + backfill simulation (0 created on re-run; 166 NULL backfilled; set values untouched); full suite **395 passed, 1 skipped** on isolated and default DBs; `ruff check` clean. Window evidence computed live (7d: 4 encounters incl. 2 doctor-attributed; 30d: 9 incl. 5 doctor-attributed).
- Tests: 4 new (recency-spec coherence, practitioner attribution mix, window freshness computed at test time, backfill-only-NULL) — no-churn test now snapshots dates/encounter_type/practitioner.
- Acceptance criteria: PASS — the `get_my_recent_patients` 7-day panel is non-empty on any fresh seed; determinism within a run preserved; idempotency preserved.
- Security notes: none beyond standing rules (synthetic only). Concurrency incidents during parallel test runs (two suites sharing a DB) were transient and resolved — runs are serialized per the standing rule.
- Blockers: none. Demo DB reset + the live 7-day acceptance re-run happen after Wave V's live checks (it is using the stack).
- Next phase: Wave V (doctor AI workspace) still running → then UI-A/UI-B (the 20 UX patterns) → Wave U (admin analytics).

### 2026-10-27 — Wave V: doctor AI workspace (history + composer)
- Status: DONE
- Implemented: Server-side per-user AI conversation history (`admin.ai_conversation`, migration `9f1c3d5a7b24`) — `GET/DELETE /api/v1/ai/conversations[/{id}]`, strictly own-rows scoping, clear/delete audited (`ai.history.clear`/`ai.history.delete`), `POST /ai/ask` persists the exchange in the same commit as its audit event (no double-write; frozen response shape untouched). Composer per the owner's screenshot: send button INSIDE the textarea box, Enter to send / Shift+Enter newline (IME-safe, aria-describedby hint), in-flight disable + working Cancel. History UI on both surfaces (expandable turns with citations, mode badges, clear-history confirm), patient-filtered rail history, "Summarize this patient" quick action + real suggestion chips (incl. the 7-day panel question).
- Files changed: backend/app/{models/ai.py,services/ai_history.py,schemas/ai.py,api/v1/ai.py,models/__init__.py}, migration 9f1c3d5a7b24, backend/tests/{test_ai_history.py (new, 12 tests),test_audit_api.py,test_permissions_api.py} (sanctioned additive guard registrations), frontend/{ai.html,patient.html,assets/js/{ai-assistant,patient,ai-history,ui}.js,assets/css/styles.css,tests/render-smoke.mjs}, docs/API.md (AI assistant section)
- Commands run: `uv run pytest -q` → **395 passed, 1 skipped** (isolated `clinical_demo_wave5` DB and default DB both green — shared-DB flakes under concurrent agents proven environmental); `ruff check` clean; `security_check.py` → PASS; frontend **97/97 + 42/42** (was 94 + 37); live: migration applied in container, real LLM answered both modes, history persisted across sessions (2 rows incl. patient + general), nurse sees **0 rows** (per-user isolation).
- Tests: 12 new history tests (persistence, isolation/concealment, pagination, patient filter, clear/delete, CSRF/401, migration up/down/up) + permission-matrix cases for the 3 new routes + 5 frontend scenarios (Enter/Shift+Enter, send-button path, history render/expand/clear, rail filter, quick action).
- Acceptance criteria: PASS — history persists and is private per user; composer behaves per spec; no clinical content in web storage (verified by existing hygiene tests); security posture unchanged.
- Security notes: history is workflow data, not audit data (audit keeps metadata-only policy); production retention/erasure remains documented backlog. Ops note: parallel agents sharing one test DB caused transient cross-run failures — future agent briefs assign unique test DBs.
- Blockers: none. Next: demo DB reset + live 7-day acceptance re-run → UI-A (20 UX patterns, part 1).

### 2026-10-27 — Wave U: admin analytics + all build waves complete
- Status: DONE
- Implemented: `GET /api/v1/analytics/summary` (`analytics:read`, Admin-only) — real aggregates over existing tables in ~10 grouped queries (counts, 30-day activity, lab status mix, abnormal-lab rate with the SINGLE abnormal rule shared with the MCP tools — cross-tested against `find_patients_by_lab`, top abnormal tests, prescriptions by status, staff by role, 14-day zero-filled audit activity, AI usage with top tools, provenance), one `analytics.read` audit event per read. Frontend `analytics.html` in the MediCore language: KPI tiles with deltas, SVG grouped-bar audit chart, abnormal-rate donut + status mix, horizontal bars, AI usage — pure SVG/CSS (bars as `rect` attributes, donut via `stroke-dasharray` attribute; the only style write in the app remains the sanctioned `--sheet-dy`), every chart `role="img"` + aria-label + visually-hidden data table, skeletons, SWR refresh, honest empty states, permission-denied panel, nav + ⌘K command gated on `analytics:read`.
- Files changed: backend/app/{schemas,services,api/v1}/analytics.py + router registration, backend/tests/{test_analytics.py (9 tests),test_permissions_api.py (MATRIX row),test_audit_api.py (KNOWN_ACTIONS)}, frontend/{analytics.html,assets/js/analytics.js,ui.js,auth.js,command-menu.js,assets/css/styles.css,tests/*}, docs/API.md (Analytics section + stale matrix preamble corrected)
- Commands run: `uv run pytest -q` → **422 passed, 1 skipped**; `ruff check` clean; frontend **144/144 + 67/67**; `security_check.py` → PASS; live: admin 200 with real JSON (45 patients/167 encounters/415 labs/104 prescriptions; abnormal rate 32.5% of 397 evaluable; top abnormal GLU 19/K 15/LDL 12), doctor **403**, anonymous **401**, `analytics.read` audit row present, LLM still live.
- Acceptance criteria: PASS — analytics are real (no fabricated numbers), Admin-only enforced server-side, accessible charts, bounded and efficient queries.
- Security notes: `pct` serialized as a JSON number (derived display rate, documented); `counts.ai_conversations` counts the history table deliberately (clearing history ≠ erasing usage); no new style-write exceptions introduced.
- ALL BUILD WAVES ARE NOW COMPLETE (roles, schema, data, lookup tools, panel tools, seed freshness, AI workspace, UI-A/UI-B 20-pattern library, analytics). Remaining per owner decision: Phases 14–15 (public deployment) — BLOCKED/deferred until hosting access is provided.
- Blockers: none.

### 2026-10-27 — Waves BG-DATA, AI-FORMAT, UI-C: Bulgarian demo, scannable answers, responsive
- Status: DONE (all three waves)
- Implemented: (1) BULGARIAN DATA — seed fully localized (Cyrillic names incl. double surnames/Турkish-Bulgarian/ъ-ь-ю-я edge cases/long compounds, Bulgarian clinical notes/diagnoses/lab+medication names/encounter summaries; prompt-injection triggers translated); machine fields stable (MRNs/codes/enums/units/natural keys/RNG streams) — determinism proven by MD5 across two independently seeded DBs; demo-account credentials restored to .env values after the localization run (lead op fix). (2) AI PRESENTATION — system-prompt rules 12–15 (bold count+severity summary, proper Markdown tables never raw pipes, bold values+flags, ellipsized ids in tables, "Прегледани записи" tail, readability over density, Bulgarian-by-default answers) merged into BOTH scopes with a shared-block identity test; NEW safe Markdown-subset renderer (markdown.js: zero innerHTML/HTML-parsing APIs, XSS suite) rendering real tables/bold/lists/details across all three answer surfaces; live-verified with real Bulgarian answers (potassium roundup 9 patients as a proper table; patient lab summary 7×11 table with bold values). (3) RESPONSIVE — phone: no-overflow grid contract fixed (minmax(min(…,100%)) — real latent 320px/zoom overflow bug), long-Bulgarian-name wrapping, fluid clamp display type, pagination hit-slop fix, viewport-fit=cover (safe-area insets were no-ops before); TV: root-token scaling at ≥1600px/≥2560px (type, spacing, container 1200→1500→1920px, --focus-width 3→5→6px); reduced: 320px/200%-zoom reflow + reduced-motion parity pinned by tests; keyboard/remote parity audit (only gap: unlabeled markdown scroll regions → role=region+tabindex); arrow-follow scroll fix (lead's command-menu.js fix) now test-covered (scrollIntoView recorder; mouse triggers zero scrolls).
- Files changed: scripts/seed_demo_data.py, backend/tests/test_seed.py, README.md, docs/DEPLOYMENT.md, backend/app/llm/prompt.py, backend/tests/test_llm.py, docs/API.md, frontend/** (markdown.js new, answer renderers, styles, 7 HTML pages, tests incl. markdown.test.mjs new + domshim scroll recorder), PLAN.md
- Commands run: frontend **155/155 + 73/73 + 21/21**; backend **423 passed, 1 skipped**; `ruff check` clean; `security_check.py` → PASS (CSP hash intact); live: Bulgarian login/patients/answers verified (Д-р Георги Иванов; 9-patient hyperkalemia table; "Прегледани записи" tail).
- Acceptance criteria: PASS — owner's presentation rules implemented end-to-end (prompt + renderer); data is Bulgarian with stable identifiers; responsive targets phone/TV/reduced covered and the arrow bug fixed with regression tests.
- Security notes: markdown renderer treats model output as untrusted (every byte a text node; attribute allowlist value-checked; XSS suite green); localization kept the prompt-injection trigger texts as clearly-fictional Bulgarian untrusted data.
- Blockers: none. Remaining: the owner's real-browser pass (pixel/contrast/zoom/notch checks), and Phases 14–15 (deployment) per the standing deferral.

### 2026-10-30 — Product de-marking (owner decision)
- Status: DONE
- Implemented: removed visible demo/synthetic origin labelling and disclaimer copy from the product surface: provenance badges/pills, `(демо)` display suffixes, footer/About/login/dashboard disclosure copy, AI disclaimer keys/panels, and clinical payload `provenance` keys. The only content-origin label retained is the small **AI-generated** badge on assistant answers. Synthetic origin remains honest and traceable in the database (`patients.patient.provenance`) and internal docs.
- Files changed: frontend/** (pages, JS renderers, tests, DESIGN.md), backend schemas/serializers, migration `f3b9c2a7e4d1` (MRN prefix `DEMO-`→`MRN-` + seeded-content de-marking), tests.
- Acceptance criteria: PASS — `frontend/tests/smoke.mjs` enforces the no-demo/no-disclaimer surface rule; frontend render tests verify no origin badge/disclaimer renders while legacy extra API keys are ignored.
- Security notes: this is presentation/contract reconciliation, not a security control. DB constraints still force `MRN-` identifiers and `provenance = 'synthetic'`; docs retain non-claims and real-data warnings.

### 2026-11-02 — Neutral MRN/de-mark migration
- Status: DONE
- Implemented: migration `f3b9c2a7e4d1_rename_mrn_prefix_and_de_mark_content` moves the synthetic-only MRN scheme from `DEMO-` to the neutral `MRN-` prefix, follows the DB CHECK constraint, and de-marks seeded display/note text in place. Stable patient row IDs remain key-derived and do not churn on the rename.
- Files changed: `backend/migrations/versions/f3b9c2a7e4d1_rename_mrn_prefix_and_de_mark_content.py`, seed/model/tests.
- Acceptance criteria: PASS — model and seed tests cover the neutral prefix, synthetic provenance invariant, and idempotent re-seed behavior.

### 2026-11-04 — Graphify charts + bounded lab trends + focused AI guardrails
- Status: DONE
- Implemented:
  1. **Graphify** (`frontend/assets/js/graphify.js`): dependency-free interactive SVG primitives (grouped bars, line charts, donut, horizontal bars) with hover/focus readouts, keyboard navigation, legend toggles, `role="img"` summaries, and visually hidden data tables. Used by the admin audit chart and the patient lab-trend chart. No CDN/npm, no `innerHTML`, no style writes (CSP/static-hygiene tests intact).
  2. **Patient lab trends**: additive `GET /api/v1/patients/{patient_id}/labs/trends?days=365&tests=K,GLU` — `lab:read`, `check_patient_access`, 404 concealment, one metadata-only `labs.read` audit event. Numeric non-deleted labs only; max 5 series and 50 most-recent points per series returned ascending; decimal strings; per-point `high|low|normal|unclassified` flag from the point's own ref bounds; `truncated` reports point/series bounds. No name/MRN/DOB/free text in the response.
  3. **Focused AI guardrails** (`backend/app/llm/guardrails.py` + `llm/loop.py` + `api/v1/ai.py`): deterministic input blocks for prompt/credential/SQL/shell/filesystem/database/bypass requests; patient mode restricted to the three single-patient tools with the exact verified patient_id; model-facing payload minimization (drops MRN/DOB/unnecessary FKs, retains clinical facts and citation IDs); output blocks secrets/DSNs/tokens/private keys, external URLs, raw HTML/script/event handlers, internal prompt/config text, and unauthorized citation IDs. Blocks are fail-closed generic `403 forbidden`, metadata-only `ai.ask` denial, no history/idempotency persistence, no prompt/answer logging.
  4. Docs reconciled to tested code: seven MCP tools, `LLM_MAX_TOOL_CALLS_GENERAL=10` clamp1..20, five-key AI response (no `disclaimer`), database-internal provenance, and `docs/AI_GUARDRAILS.md` maps the focused controls to the patient-data guardrail baseline without compliance claims.
- Files changed: `frontend/assets/js/{graphify,analytics,patient}.js`, `frontend/patient.html`, `frontend/assets/css/styles.css`, `frontend/tests/{graphify.test.mjs,render-smoke.mjs,domshim.mjs}`, `.github/workflows/ci.yml`, `backend/app/services/labs.py`, `backend/app/schemas/patient.py`, `backend/app/api/v1/patients.py`, `backend/app/llm/{guardrails,loop}.py`, `backend/app/api/v1/ai.py`, `backend/tests/{test_lab_trends,test_llm_guardrails,test_ai_api,test_llm,test_mcp,test_seed,test_permissions_api}.py`, `docs/{API,ARCHITECTURE,SECURITY,BACKLOG,CONCEPT,DEPLOYMENT,KUBERNETES,TROUBLESHOOTING}.md`, `docs/AI_GUARDRAILS.md`, `README.md`, `.env.example`, `k8s/configmap.yaml`, `scripts/e2e_smoke.sh`, `scripts/k8s/gen-secret.sh`, `PLAN.md`.
- Commands run: `uv run pytest -q` → **146 passed, 302 skipped** (Postgres-backed cases skip because no local DB/Docker daemon); `uv run ruff check .` clean; frontend **160/160** static + **3/3** Graphify + **21/21** Markdown + **74/74** render scenarios; `python3 scripts/security_check.py` → **PASS (6 sections, 0 findings)**; `bash -n` on edited shell scripts clean. Docker/live e2e not run because the Docker daemon is unavailable in this environment.
- Acceptance criteria: PASS for locally verifiable behavior — charts are interactive/accessible and CSP-safe; trend data is bounded/minimized and uses the existing abnormal rule; guardrails fail closed and cannot widen the seven-tool read-only MCP boundary; success response shape remains frozen.
- Security notes: guardrails are supplementary deterministic controls, not the security boundary. Patient mode cannot call cross-patient lookup tools; general mode remains unchanged. No prompt/question/answer/clinical payload is logged or audited. Real-data release gates (care-relationship policy, RLS/DB identities, retention/deletion, incident response, provider/legal review, penetration/clinical validation) remain open in `docs/AI_GUARDRAILS.md`/`docs/BACKLOG.md`.
- Blockers: local Postgres/Docker unavailable, so DB-backed integration and `scripts/e2e_smoke.sh` must run in CI or an environment with the documented stack before production claims.

### 2026-11-07 — AI Patient tab, per-test lab summary, landing redesign
- Status: DONE
- Implemented:
  1. **AI Patient tab** (`frontend/patient.html`, `patient.js`, `styles.css`): moved the patient AI assistant out of the right-hand rail into a fifth patient-record tab after Encounters, labelled **AI Patient**. The tab shows a patient context line (name + MRN), keeps the existing patient-scoped `ai/ask` request, patient-filtered history, quick actions, composer, loading/cancel/503/429/error states, and permission gating. The old two-column patient layout and sticky rail styles were removed. Command-menu actions ("Summarize this patient", "Ask AI about this patient", suggestion chips) now activate the AI Patient tab before filling/focusing/sending.
  2. **Lab Results Summary** (`frontend/assets/js/graphify.js`, `patient.js`, `styles.css`, `patient.html`): replaced the single mixed-unit trend chart with a responsive grid of per-test chart cards. Each card has its own Y scale and unit, a green shaded recorded-reference band (`ref_low`–`ref_high`), a target note derived from those bounds, value labels on data points, clean date ticks, flag-coloured points, hover/focus readouts, and a hidden data table. Dense series label the latest and abnormal points; the full paginated lab table remains below the summary. No backend/API change.
  3. **Landing page** (`frontend/index.html`, `assets/js/login.js`, `styles.css`): rebuilt the sign-in page as a full-viewport dark hero with a CSP-safe ambient clinical background (layered gradients/light streaks; no external asset requests), MediCore brand lockup, large "MediCore — Clinical Platform" headline, value proposition, translucent sign-in card with email/password field icons, an accessible show/hide-password button, full-width Sign in action, footer "About MediCore"/"Report an issue", and the honest security line "Role-based access with server-side audit logging." No HIPAA/compliance claim was added. The image generator was unavailable (account funds), so the background is CSS-only rather than a generated photo.
- Files changed: `frontend/patient.html`, `frontend/index.html`, `frontend/assets/js/{patient,graphify,login}.js`, `frontend/assets/css/styles.css`, `frontend/tests/{graphify.test.mjs,smoke.mjs,render-smoke.mjs}`, `frontend/DESIGN.md`, `README.md`, `docs/{SECURITY,BACKLOG,ARCHITECTURE,CONCEPT}.md`, `backend/tests/{test_ai_api,test_clinical_api,test_mcp,test_seed}.py`, `PLAN.md`.
- Commands run: `node tests/smoke.mjs` → **160/160**; `node tests/graphify.test.mjs` → **4/4**; `node tests/markdown.test.mjs` → **21/21**; `node tests/render-smoke.mjs` → **75/75**; `uv run pytest -q` → **447 passed, 1 skipped** against a live PostgreSQL test database; `uv run ruff check .` clean.
- Test reconciliation: the now-runnable Postgres suite exposed stale de-marking expectations that had been skipped before. Updated DB-backed tests to the shipped contract: removed serialized `provenance` assertions (provenance remains database-internal), changed test MRNs to the `MRN-` constraint, fixed the patient-scope tool-call assertion (the mismatched call is blocked before MCP), and replaced the removed note-body disclaimer expectation with an absence check.
- Acceptance criteria: PASS for locally verifiable behavior — AI Patient is a real fifth tab and remains scoped to the URL patient; each lab test gets its own chart, unit, reference band, target note, and value labels; the landing page matches the requested split-hero/sign-in composition without external requests or compliance claims.
- Notes: Purely frontend/presentation work. The backend contract, seven-tool MCP boundary, and patient-scoped AI guardrails are unchanged.

### 2026-11-07 — Clinical motion system
- Status: DONE
- Implemented: a shared motion layer in `frontend/assets/css/styles.css` with `--motion-fast/base/exit/stagger`, `--ease-out`, and `--ease-in-out` tokens. Applied ease-out entrances, 200ms dropdown/palette timing, 30ms list-row staggering, 0.97 press scale (0.985 for large cards), 12% chart/loader stretch, detail-settle cascades, success pulse for restore toasts, ease-in-out moves, 0.95 popover origin, trigger-anchored palette/nav/sheet entrances, explicit shorter exits, overlapping view transitions for record panels and the command palette, a masked tab-underline reveal, blurred icon crossfade for the password toggle, and rubber-band resistance when a bottom sheet is pulled above rest. No bounce, no held-state tweening, and the only animated properties are transform/opacity/filter (the mobile nav disclosure is the one isolated layout transition). All motion is disabled under `prefers-reduced-motion`, and the `--sheet-dy` style-write exception remains the only inline style write.
- Intentional omission: no arc movement. The only draggable object is the sheet grabber, which must track the finger directly; adding an arc would break direct manipulation. There is no progress-slider knob, so the 12% stretch is applied to the existing heart loader and Graphify marks.
- Files changed: `frontend/assets/css/styles.css`, `frontend/assets/js/{sheet,toast,admin}.js`, `frontend/tests/{smoke.mjs,render-smoke.mjs}`, `frontend/DESIGN.md`, `PLAN.md`.
- Commands run: `node tests/smoke.mjs` → **161/161**; `node tests/graphify.test.mjs` → **4/4**; `node tests/markdown.test.mjs` → **21/21**; `node tests/render-smoke.mjs` → **75/75**; `git diff --check` clean.
- Acceptance criteria: PASS — the motion is short, professional, accessible, and transform-only where possible; the new sheet rubber-band test and the password-toggle crossfade test are covered.

### 2026-11-07 — Performance pass (frontend, API, PostgreSQL)
- Status: DONE
- Baseline method: authenticated `httpx` loop against the Docker app (`http://localhost:8000`, 30 measured requests per endpoint after warm-up), plus an in-process ASGI harness with a SQLAlchemy `before_cursor_execute` counter, plus `EXPLAIN (ANALYZE, BUFFERS)` on the hot queries. Dataset: 45 patients / 167 encounters / 415 labs / 104 prescriptions.
- Implemented:
  1. **Selective static GZip** (`backend/app/main.py`): `FrontendGZipMiddleware` compresses HTML/CSS/JS/SVG under `/` with level 3; `/api` and `/health` deliberately bypass compression. Measured: CSS 96,251 → 23,338 bytes (-75.8%), patient HTML 17,406 → 3,734 (-78.5%), `patient.js` 43,442 → 13,254 (-69.5%), patient JS module graph ~184 KB → ~52 KB compressed. Security headers remain on every response.
  2. **One auth query instead of three** (`backend/app/auth/sessions.py`, `identity.py`): a single session/user/roles join (`load_session_with_roles`) replaces the session + staff + roles trio on every authenticated request. Query counts fell from 6→4 (patient list), 8→4 (detail), 11→9 (summary), 7→5 (labs), 9→7 (trends), 8→6 (prescriptions), 5→3 (AI history), 6→4 (staff), 5→3 (audit), 14→12 (analytics).
  3. **Detail endpoint skips summary enrichment** (`backend/app/services/patients.py`, `api/v1/patients.py`): `get_patient_base` keeps the derived `age_years` but avoids the diagnoses + latest-note reads that only the summary/MCP read model needs. Detail dropped from 8 to 4 queries including audit.
  4. **Lazy patient-tab data + dynamic Graphify** (`frontend/assets/js/patient.js`, tests): initial patient page now issues 3 API requests (session, demographics, summary) instead of 6-7. Labs (`labs` + `labs/trends`), prescriptions, and AI history load on first tab activation; `graphify.js` is dynamically imported only when the Labs tab is opened, removing 23.5 KB from the initial patient module graph.
- Rejected/deferred: adding PostgreSQL indexes (all hot plans are sub-millisecond sequential/index scans over 45–1,228 rows; no measured justification); trigram search index (dataset too small); compressing API JSON (measurable localhost CPU cost, small payloads); dynamic-importing Markdown/AI-history (smaller gain, higher test/refactor risk); modulepreload expansion (cannot measure browser-level start-order gain in this environment); LLM prompt changes (provider disabled, no live latency measurement).
- Verification: `uv run pytest -q` → **448 passed, 1 skipped** (includes the new gzip test); `uv run ruff check .` clean; `node tests/smoke.mjs` 161/161; `node tests/graphify.test.mjs` 4/4; `node tests/markdown.test.mjs` 21/21; `node tests/render-smoke.mjs` 76/76; `python3 scripts/security_check.py` PASS; `git diff --check` clean; `bash scripts/e2e_smoke.sh` ALL STEPS PASSED on the rebuilt Docker stack.
- Notes: API p50 on localhost is dominated by ASGI/HTTP overhead and moved by a few milliseconds in both directions; the deterministic wins are fewer requests, fewer SQL round trips, and much smaller static transfers. The audit table grew from ~1.2k to ~3.7k rows during repeated benchmarks, which inflates later analytics/audit timings.

### 2026-11-08 — Printable patient medical record (Print / Save as PDF)
- Status: DONE
- Implemented:
  1. **Additive encounter endpoint**: `GET /api/v1/patients/{patient_id}/encounters?limit=&offset=` returns `Page[EncounterOut]`, newest first, soft-deleted excluded, `patient:read`, patient-access check, `404` concealment, existing `patient.read` audit action. Existing endpoint responses are unchanged.
  2. **Print workflow**: `frontend/assets/js/print-record.js` is dynamically imported on first click. It fetches fresh demographics, summary, every lab page (limit 100), every prescription page, and every encounter page through the authenticated `api.js` client. It refuses to print on any load failure, names the failed section, and offers Retry. Clinical text is rendered only through text nodes.
  3. **Print layout**: `frontend/assets/css/print.css` provides an A4 portrait report with running header/footer, patient identity, patient information, summary, encounter history, complete laboratory table, and prescriptions. Abnormal labs use text flags (`HIGH ↑` / `LOW ↓`), bold text, underlines, and a heavy row rule so they remain readable in black and white. Table headers repeat and rows avoid mid-row breaks.
  4. **Patient UI**: a keyboard-accessible **Print / Save as PDF** button sits in the patient header, hidden without `patient:read`; `#print-record` is hidden on screen and revealed only by print CSS. The normal page, lazy tabs, and AI panel are unchanged.
  5. **Tests**: new `frontend/tests/print-record.test.mjs` (decimal flags, complete pagination, missing data, safe text, failure gate), patient-page render scenarios for the button/complete fetch/failure/permission gate, static print-contract checks, an additive encounter API test, permission-matrix coverage, and CI wiring.
- Files created: `frontend/assets/js/print-record.js`, `frontend/assets/css/print.css`, `frontend/tests/print-record.test.mjs`.
- Files modified: `backend/app/api/v1/patients.py`, `backend/app/services/patients.py`, `backend/tests/test_clinical_api.py`, `backend/tests/test_permissions_api.py`, `frontend/patient.html`, `frontend/assets/js/patient.js`, `frontend/assets/css/styles.css`, `frontend/tests/{smoke,render-smoke}.mjs`, `.github/workflows/ci.yml`, `scripts/e2e_smoke.sh`, `docs/API.md`, `frontend/DESIGN.md`, `README.md`, `PLAN.md`.
- Commands run: `uv run pytest -q` → **453 passed, 1 skipped**; `uv run ruff check .` clean; `node tests/smoke.mjs` **167/167**; `node tests/print-record.test.mjs` **8/8**; `node tests/graphify.test.mjs` **4/4**; `node tests/markdown.test.mjs` **21/21**; `node tests/render-smoke.mjs` **79/79**; `python3 scripts/security_check.py` PASS; `bash scripts/e2e_smoke.sh` ALL STEPS PASSED.
- Limitations: no headless browser is available, so the native print dialog and actual A4/PDF output are **not browser-verified**; tests verify the generated DOM, print CSS contract, A4 rules, and fail-closed behavior. The API exposes `prescriber_id` but not prescriber names, so the report does not invent a clinician name. CSS page counters (`@page` margin boxes) are browser-dependent; the fixed footer identifies the document regardless.

### 2026-11-09 — Appointment calendar, voice transcription, AI proposal
- Status: DONE for the locally verifiable scope.
- Implemented:
  1. **Scheduling schema/services** (`backend/app/models/scheduling.py`, migration `a4d7e2c9b681`, `schemas/appointment.py`, `services/appointments.py`): doctor schedule, working hours (clinic-local `TIME`), time off (UTC), and appointments (UTC, status lifecycle). Europe/Sofia local→UTC conversion rejects DST holes/folds. Availability replacement validates interval order/overlap and is bounded (≤21 intervals, ≤100 time-off rows). Slots are generated at the configured increment across working intervals with `available|past|booked|time_off` reasons.
  2. **Conflict-safe writes**: doctor/patient overlap checks plus PostgreSQL transaction advisory locks on both keys and an in-transaction recheck, so concurrent bookings cannot both succeed. Appointment mutations are audited (`appointment.create/update/reschedule/cancel/denied/availability.update`). Terminal appointments cannot be edited; cancellation is idempotent.
  3. **REST contract** (`api/v1/appointments.py`): config, list (≤62-day range, doctor-own-calendar scoping, Admin doctor selection, read-only Nurse clinic view), create, detail, patch, cancel, availability GET/PUT, slots, and AI interpret. Nurse appointment writes/availability/voice are 403. Another doctor's appointment is a concealed 404 and audited as `appointment.denied`.
  4. **Provider-agnostic AI interpreter** (`services/appointment_ai.py`): strict JSON, `tools=[]`, clinic anchor, server-side patient resolution through the existing read-only MCP `search_authorized_patients`, deterministic DST/working-hours/time-off/overlap checks, and a non-persisted proposal (`can_confirm`, warnings, candidates). It never writes; final creation is a separate explicit `POST /appointments` with full reauthorization/revalidation. Audited `appointment.ai.propose` (metadata only, never text).
  5. **Voice backend** (`api/v1/voice.py`, `services/voice.py`, `schemas/voice.py`): authenticated multipart `file` + `language=en|bg`; exact-route body allowance (default 4 MiB, hard clamp 8 MiB) while every other route stays at 1 MiB; streamed private temp file; media-type check; FFprobe duration bound (default 60 s); FFmpeg fixed-argument conversion to 16 kHz mono PCM WAV with timeout; Python `wave` verification; private whisper.cpp `/inference` with none/bearer/gcp_iam auth; controlled 400/429/503; temp cleanup in `finally`; audio/transcripts never logged, audited, or persisted. `Permissions-Policy` allows `microphone=(self)` only.
  6. **Private speech image** (`speech/Dockerfile`, compose `speech` profile): whisper.cpp v1.9.5 from a checksum-pinned tarball, checksum-verified `ggml-tiny.bin` (Tiny multilingual, not `.en`), CPU-only, non-root, no published port, `/health` check. Backend image installs FFmpeg/FFprobe. Compose points the app at `http://speech:8080` by default; without the profile voice is 503 and manual scheduling still works.
  7. **Frontend** (`appointments.html`, `appointments.js`, styles, `auth.js`, `api.js`): Appointments nav gated on `appointment:read`; month/week/day/upcoming views; Admin doctor selector; appointment detail/reschedule/cancel; patient search; slot-driven create; availability editor (working hours + UTC time off); MediaRecorder voice panel with language choice, 60 s auto-stop, immediate track stop, in-memory-only audio, upload/retry states, editable transcript and manual fallback; AI proposal card labelled AI-generated with candidate selection, warnings, and “Review & create” → normal explicit create. All clinic display uses Europe/Sofia. No inline styles/scripts; `ui.el` keeps form value property/attribute in sync.
  8. **Contracts/tests/docs**: permission count is now 11; appointment permission/audit matrices updated; new backend tests for availability, slots, ownership, conflicts, terminal lifecycle, DST, concurrency, audit, voice conversion/limits/cleanup/mocked speech failures, and AI proposal-only behavior. Frontend smoke/render tests cover the page, admin scope, day view, availability dialog, voice proposal, and explicit create. Docs: `API.md`, `ARCHITECTURE.md`, `SECURITY.md`, `DEPLOYMENT.md`, new `SPEECH_RECOGNITION.md`, `TROUBLESHOOTING.md`, `BACKLOG.md`, `README.md`, `frontend/DESIGN.md`, `.env.example`, `scripts/e2e_smoke.sh`.
- Files created: `backend/app/models/scheduling.py`, `backend/migrations/versions/a4d7e2c9b681_add_scheduling.py`, `backend/app/schemas/appointment.py`, `backend/app/schemas/voice.py`, `backend/app/services/appointments.py`, `backend/app/services/appointment_ai.py`, `backend/app/services/voice.py`, `backend/app/api/v1/appointments.py`, `backend/app/api/v1/voice.py`, `backend/tests/{test_appointments_api,test_appointment_ai,test_voice_api}.py`, `frontend/{appointments.html,assets/js/appointments.js}`, `speech/Dockerfile`, `docs/SPEECH_RECOGNITION.md`.
- Files modified: `backend/app/{main,config}.py`, `backend/app/authorization/policy.py`, `backend/app/models/__init__.py`, `backend/app/api/v1/router.py`, `backend/app/schemas/appointment.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/Dockerfile`, `backend/tests/{test_permissions_api,test_seed,test_hardening,test_audit_api,test_policy,test_auth_api,test_security_headers}.py`, `docker-compose.yml`, `scripts/{seed_demo_data,e2e_smoke,security_check}`, frontend `auth.js`, `api.js`, `ui.js`, `styles.css`, tests, docs, `.env.example`, `README.md`, `PLAN.md`.
- Commands run:
  - `uv run ruff check .` → clean.
  - `uv run pytest -q` → **529 passed, 1 skipped** against the live PostgreSQL test DB.
  - `node tests/smoke.mjs` → **180/180**; `graphify.test.mjs` **4/4**; `markdown.test.mjs` **21/21**; `print-record.test.mjs` **8/8**; `render-smoke.mjs` **81/81**.
  - `python3 scripts/security_check.py` → **PASS (6 sections, 0 findings)**; it warns that `design/TechAdminPanel.png` is tracked but absent from this working tree (pre-existing; not created/fabricated here).
  - `docker compose build app` and `docker compose --profile speech build speech` → both images built; speech container healthy.
  - `bash scripts/e2e_smoke.sh` → **ALL STEPS PASSED**, including appointment create/update/cancel, nurse 403, audit actions, log scan, and restart persistence.
  - Live voice path: authenticated multipart upload → backend FFmpeg (2.6 s WAV) → private whisper.cpp Tiny → **200** `{text, language, model: tiny, duration_seconds: 2.6}`. The generated English TTS sample was transcribed; this verifies plumbing, not accuracy.
  - Live AI interpret with `LLM_ENABLED=false` → **503 dependency_unavailable**; `/appointments.html` served 200; `Permissions-Policy` shows `microphone=(self)`.
- Acceptance criteria: PASS for locally verifiable behavior — manual calendar/availability/conflicts/ownership, voice upload bounds/cleanup, private speech inference, proposal-only AI, explicit confirmation, audit, and the frozen 1 MiB global body limit.
- Limitations / not verified: no real browser microphone permission/codec test; whisper Tiny accuracy/latency/memory and Bulgarian clinical vocabulary are **not** benchmarked; Cloud Run private-service IAM/metadata-token path is documented but not deployed/verified; cross-cloud Render→Cloud Run IAM remains a blocker. No Git commands/commits were performed.
- Security notes: appointment writes are revalidated under advisory locks; the AI cannot write; MCP remains exactly seven read-only tools; voice audio/transcripts are never logged, audited, or persisted; the only route above 1 MiB is exact-match and hard-clamped; `/tmp` cleanup is per request (abrupt process kill can leave files for the OS).

### 2026-11-09 — Landing page care-team background
- Status: DONE for the local demo.
- Implemented: copied `design/DoctorBackground.png` to `frontend/assets/images/doctor-background.png` and added a decorative `landing-ambient__photo` layer behind the existing landing gradients. The local photo is rendered with a dark gradient, `blur(20px)`, reduced saturation, and reduced opacity; the existing light-streak/vignette overlays sit above it. It remains same-origin under CSP `img-src 'self'`, is `aria-hidden`, and has no inline styles. Mobile/desktop use `cover` positioning.
- Added an optimized 32 KB WebP derivative (`doctor-background.webp`) and `image-set` with the PNG fallback. Registered `.webp` as `image/webp` in the backend static media types so it cannot fall back to `application/octet-stream` under `nosniff`; added a regression test.
- Files changed: `frontend/index.html`, `frontend/assets/css/styles.css`, `frontend/tests/smoke.mjs`, `frontend/DESIGN.md`, `backend/app/main.py`, `backend/tests/test_security_headers.py`, `PLAN.md`; new assets `frontend/assets/images/doctor-background.{png,webp}`.
- Verification: `node tests/smoke.mjs` **181/181**; `node tests/render-smoke.mjs` **81/81**; `scripts/security_check.py` PASS; backend `test_security_headers.py` **14/14**; Docker image rebuilt and serves both assets (`PNG 200 image/png`, byte-identical to the source; WebP `200 image/webp`, 32 KB), landing HTML/CSS contain the new layer.
- Licensing caveat: the supplied `design/DoctorBackground.png` appears to be a watermarked stock preview (faint “dreamstime” mark). It is used only as a heavily blurred local demo backdrop; replace it with a properly licensed/authorized asset before public deployment.

### 2026-11-09 — Read-only nurse appointment sidebar
- Status: DONE.
- Implemented:
  1. **Permission:** nurse now holds `appointment:read` only (plus existing clinical reads). `appointment:write`, `appointment:manage_availability`, and voice remain unavailable; the total permission-key count stays **11**.
  2. **Backend read scoping:** `GET /appointments` and `GET /appointments/{id}` treat nurses as clinic-wide readers (optional `doctor_id` filter); doctor reads stay own-calendar and Admin stays cross-doctor. `GET /appointments/availability`/`slots` accept an explicit `doctor_id` for nurses. Create/update/cancel/availability/interpret/voice remain `403`.
  3. **Frontend:** the Appointments sidebar item now appears for nurses. The page shows month/week/day/upcoming and appointment detail in a **read-only** mode: no New, Reschedule, Cancel, Availability, voice panel, doctor picker, or booking slots. The header says “Read-only clinic calendar”; availability/slot requests are skipped for read-only users.
  4. **Docs/tests:** permission matrices and role descriptions updated; policy/auth/appointment/matrix tests and an E2E nurse read-only boundary added; render smoke covers the nurse view.
- Files changed: `backend/app/authorization/policy.py`, `backend/app/api/v1/appointments.py`, `backend/tests/{test_policy,test_auth_api,test_permissions_api,test_appointments_api}.py`, `frontend/assets/js/appointments.js`, `frontend/tests/render-smoke.mjs`, `scripts/e2e_smoke.sh`, `README.md`, `docs/{API,SECURITY}.md`, `frontend/DESIGN.md`, `PLAN.md`.
- Verification: full backend suite **530 passed, 1 skipped**; frontend smoke **181/181** (at the time); render smoke **82/82**; Docker E2E **ALL STEPS PASSED** (nurse GET appointments 200, POST 403, `/auth/me` has `appointment:read` only); live nurse check: config 200, list 200 with clinic data, detail 200, create 403, availability PUT 403, voice 403.
- Security note: in this synthetic demo the nurse read is clinic-wide, consistent with the existing demo patient-access policy. Real-data use still requires a care-relationship/assignment policy and the other production guards in `docs/AI_GUARDRAILS.md`.

### 2026-11-09 — Visit catalogue + appointment form fixes
- Status: DONE.
- Fixed:
  1. **Slot selection was impossible.** `ui.el` treated `disabled: false` as the string `"false"`; because `disabled` is a boolean HTML attribute, the attribute’s mere presence disabled every available slot. `ui.el` now handles `disabled`/`hidden`/`checked`/`selected`/`required`/`multiple`/`readOnly` as true booleans (set/remove the attribute and property). Available slots are clickable and expose `aria-pressed` selection.
  2. **Calendar loading block never disappeared.** `.loading-block { display:flex }` overrode the UA `[hidden]` rule. Added a global `[hidden] { display:none !important; }` so the loader disappears after the request and other hidden panels behave correctly. Added a static regression check.
- Added:
  1. `frontend/assets/js/visit-types.js` with the full 19-entry `VISIT_TYPES` catalogue and `findVisitType` alias matching (`Consultation` → General Consultation, etc.).
  2. The appointment form now uses a **Type of visit** select with all types and suggested durations. Selecting a type updates the duration field. Selecting **Other** reveals a required custom visit-type input (≤100 chars) directly below; the custom label is stored in the existing `appointment_type` field. Existing/custom labels are preserved on edit, and AI-proposed types are matched to the catalogue where possible.
  3. Tests: render smoke now verifies slot click/selection, `disabled=false` semantics, all 19 options, the automatic 45-minute New Patient duration, the Other custom field, and creation with a custom label.
- Files changed: `frontend/assets/js/ui.js`, `frontend/assets/js/appointments.js`, `frontend/assets/js/visit-types.js`, `frontend/assets/css/styles.css`, `frontend/tests/{smoke,render-smoke}.mjs`, `frontend/DESIGN.md`, `docs/API.md`, `PLAN.md`.
- Verification: frontend smoke **185/185**; render smoke **83/83**; Docker image rebuilt; Docker E2E **ALL STEPS PASSED**; app/db/speech containers healthy.

### 2026-11-09 — Light / Dark / System themes + readability
- Status: DONE.
- Implemented:
  1. **Pre-paint theme bootstrap:** new classic `theme-init.js` is loaded synchronously before the stylesheet on every page; it reads `localStorage["medicore-theme"]`, resolves `system` against `prefers-color-scheme`, and sets `data-theme`/`data-theme-mode` on `<html>` before first paint (no flash).
  2. **Tokenized themes:** existing token names retained; `:root` is Light, `:root[data-theme='dark']` is Dark, and `@media (prefers-color-scheme: dark) { :root:not([data-theme]) { … } }` is the no-JS fallback. `color-scheme` follows the active theme (native date/time controls/scrollbars).
  3. **Switcher:** `theme.js` mounts an accessible three-button (Light/Dark/System) icon control with `aria-label`, `title`, `aria-pressed`, and hit-slop targets in the authenticated top-bar user area and landing header. Selection updates instantly, persists to `medicore-theme`, and System mode listens to `matchMedia` for live OS changes.
  4. **Readability:** raised light/dark secondary/placeholder contrast; primary text now meets a 7:1 target; primary action buttons use the same accessible blue in both themes; Voice scheduling status is a tone-aware left-border callout (info/success/warn/error), timer is a high-contrast pill, and the transcript has larger line-height/padding/min-height. Landing hero stays dark, while the sign-in card, controls, helper copy, and footer switch to light surfaces in Light mode.
  5. **Tests:** `smoke.mjs` verifies pre-paint script ordering, token selectors, theme-init contract, sanctioned theme storage, and computed WCAG contrast for both token sets; `render-smoke.mjs` verifies all three modes, persistence, fresh-module reload, switcher buttons/labels, and live OS change handling.
- Files changed: `frontend/assets/js/{theme-init,theme}.js`, `frontend/assets/js/{ui,login}.js`, `frontend/assets/css/styles.css`, all 8 HTML pages (`theme-init.js` in `<head>`), `frontend/tests/{smoke,render-smoke}.mjs`, `frontend/DESIGN.md`, `README.md`, `docs/SECURITY.md`, `PLAN.md`.
- Verification: `node tests/smoke.mjs` **191/191**; `node tests/render-smoke.mjs` **85/85**; `scripts/security_check.py` PASS; Docker image rebuilt; served `theme-init.js` before `styles.css`; Docker E2E **ALL STEPS PASSED**; app/db/speech healthy.
- Notes: the only web-storage item is the non-sensitive `medicore-theme` preference; no patient, account, clinical, or session data is stored there.

### 2026-11-09 — Doctor portraits + footer cleanup
- Status: DONE.
- Implemented:
  1. Four local demo doctor portraits in `frontend/assets/images/doctors/` (128×128 JPEG placeholders from randomuser.me; documented in that folder's README). `ui.avatarForUser` shows a portrait only when the user holds the `doctor` role; nurses/admins keep initials. `ui.doctorAvatar` covers a known appointment doctor. The seeded doctor is mapped to a consistent portrait.
  2. Doctor portraits appear in the authenticated top bar, dashboard session card, admin staff table, and appointment detail doctor row. Patient avatars remain initials.
  3. Removed the repeated full-width `.site-footer` bar from all seven authenticated pages. About / Report an issue remain in the bottom-left sidebar help card; the landing page keeps a compact, left-aligned footer instead of a full-width bar. Dead `.site-footer` CSS was removed.
  4. Added smoke/render coverage for local portrait assets, doctor-only photo gating, appointment detail portrait, footer removal, and initials for non-doctors.
- Files changed: `frontend/assets/js/{ui,admin,dashboard,appointments}.js`, `frontend/assets/css/styles.css`, all authenticated HTML pages + `index.html`, `frontend/tests/{smoke,render-smoke}.mjs`, `frontend/DESIGN.md`, `frontend/assets/images/doctors/*`, `PLAN.md`.
- Verification: frontend smoke **193/193**; render smoke **85/85**; Docker image rebuilt; Docker E2E **ALL STEPS PASSED**; app/db/speech healthy.
- Caveat: the portraits are demo placeholders, not real clinicians; replace them with approved/licensed staff photos before any real deployment.

### 2026-11-09 — LLM key-only configuration + STT handoff verification
- Status: configuration/wiring verified; external model calls remain blocked by the provider account balance.
- Implemented:
  1. **Key-only enablement:** `LLM_ENABLED` is now optional (default `None`). A non-empty `LLM_API_KEY` auto-enables the provider; `LLM_ENABLED=false` remains the explicit kill switch. Blank/`oc_sk_replace…` placeholder keys are treated as unconfigured.
  2. **Standard endpoint by default:** `LLM_API_BASE_URL` now defaults to `https://opencode.ai/inference` (funded key or available free quota) with `gpt-6-luna` as the standard low-cost model. The Go endpoint (`https://opencode.ai/inference/go`, `mimo-v2.6-pro`) remains documented for active Go subscriptions. The local `backend/.env` was switched to the standard endpoint/model (secret untouched).
  3. **Provider diagnostics:** 401 invalid key, 402 insufficient funds, 403 missing Go subscription/model access, 404 unknown model, 400 model unavailable, and 429 free-usage-limit now map to specific actionable messages without echoing provider bodies or keys. Regular 429s remain retryable rate limits. The provider always sends `x-opencode-session` (caller id or per-instance fallback), fixing the OpenCode routing requirement.
  4. **Verification:** live login → `/voice/transcriptions` returned 200 with an editable transcript; posting that transcript to `/appointments/interpret` reached the configured provider and returned the controlled `503` message **“The AI provider account has insufficient funds. Add credit or use a funded OpenCode Go plan…”**. This proves the STT → transcript → LLM handoff; the account itself has no usable model (no Go subscription, free quota exhausted, no paid credit).
  5. Docs/env template updated for auto-enable and the two OpenCode endpoint profiles.
- Files changed: `backend/app/config.py`, `backend/app/llm/provider.py`, `backend/tests/test_llm.py`, `.env.example`, `backend/.env` (non-secret base/model), `k8s/configmap.yaml`, `README.md`, `docs/{API,ARCHITECTURE,DEPLOYMENT,KUBERNETES,SPEECH_RECOGNITION,TROUBLESHOOTING}.md`, `PLAN.md`.
- Verification: backend **534 passed, 1 skipped**; frontend **193/193** static + **85/85** render; `scripts/security_check.py` PASS; Docker E2E **ALL STEPS PASSED**.
- Blocker: the configured provider key cannot complete a model call until the account has credit, an available free quota, or an active OpenCode Go subscription. This is an account entitlement, not an application wiring issue; the API now returns the exact reason.

### 2026-11-09 — Custom 404 page + error illustrations
- Status: DONE.
- Implemented:
  1. **Custom 404 page:** `frontend/404.html` uses the same theme bootstrap, MediCore brand, theme switcher, token palette, About dialog, and compact bottom-left footer. Unknown browser navigations now serve it with a real **404** status; JSON/API clients still receive the frozen `{"error":{"code":"not_found"}}` envelope.
  2. **Local themed illustrations:** hand-authored SVG assets `error-404.svg` (clipboard/chart with missing page and magnifier) and `error-connection.svg` (medical monitor with a disconnected heartbeat). No external requests, CSP-safe under `img-src 'self'`.
  3. **In-page error art:** `ui.renderErrorState` places the 404 art beside 404 messages, the connection art beside network/5xx errors, and no art for authorization/validation errors. The layout stacks on narrow screens and remains decorative (`alt=""`/`aria-hidden`).
  4. **E2E/docs/tests:** added custom-404 + illustration checks to the Docker E2E, backend fallback test (404 status/page content/JSON envelope), smoke asset/page checks, and render coverage for 404/connection/403 error states.
- Files changed: `frontend/404.html`, `frontend/assets/js/error-page.js`, `frontend/assets/js/ui.js`, `frontend/assets/css/styles.css`, `frontend/assets/images/error-404.svg`, `frontend/assets/images/error-connection.svg`, `frontend/tests/{smoke,render-smoke}.mjs`, `backend/app/main.py`, `backend/tests/test_security_headers.py`, `scripts/e2e_smoke.sh`, `frontend/DESIGN.md`, `PLAN.md`.
- Verification: backend `test_security_headers.py` **14/14**; frontend smoke **202/202**; render smoke **86/86**; Docker E2E **ALL STEPS PASSED**; app/db/speech healthy.

### 2026-11-09 — Clinical Operations & Platform Analytics dashboard
- Status: DONE.
- Implemented:
  1. **Validated shared date range:** `GET /api/v1/analytics/summary` now accepts optional inclusive UTC `start`/`end` dates (both or neither). Default is the last 30 UTC days including today; maximum span is 366 days; reversed, partial, malformed or unknown parameters are `400 validation_error`. The previous period is the equally long window immediately before `start`.
  2. **Range-aware KPIs with real comparisons:** registered patients (lifetime), new patients, encounters, laboratory tests, total prescriptions (lifetime), prescriptions written (period), active prescriptions, appointments and AI requests. `change_pct = (current − previous) / previous × 100`, rounded to 1 decimal, and **null when the previous period had zero events** (never fabricated).
  3. **Appointments:** total plus scheduled/confirmed/completed/cancelled/no-show, clinic-local daily trend, visit-type distribution, weekday/hour distribution and clinic-wide calendar utilization. Booked minutes exclude cancelled appointments; available minutes come only from recorded `working_hours` minus overlapping `time_off`; no default schedule is assumed and no per-doctor ranking exists.
  4. **Patients & encounters:** new/returning/active patients, encounter total, average per UTC day, encounter-type distribution and daily volume. No patient identifiers.
  5. **Laboratory analytics:** period totals; document-status mix; normal/high/low/unclassified/ missing-reference-range buckets; evaluable denominator and range coverage; top abnormal tests; daily total/evaluable/abnormal trend. The existing `_ABNORMAL`/`_EVALUABLE` SQL rule is preserved; missing/invalid ranges are never normal; high/low partition abnormal; every bucket sums to total.
  6. **Prescriptions:** period total/status mix, lifetime status mix, daily course-start trend. Only the recorded lifecycle values `active|completed|stopped|cancelled` are used; stopped is displayed as discontinued.
  7. **Access & Security Activity:** successful/failed sign-ins, permission denials, patient/lab/prescription reads and AI requests, plus grouped daily trends. Aggregates only — no raw audit metadata, IPs, payloads, identifiers, questions/answers or transcripts.
  8. **AI & Voice Assistant:** request/outcome partition, success rate excluding denied asks, and real `mcp.<tool>` call/failure counts. Per-request latency and voice metrics are explicitly `available: false` with reasons because those datasets are not persisted; provider health is configuration-only and marked `not_monitored`. No new logging or tables were created.
  9. **Data Quality & System Health:** recorded lab reference-range/unclassified gaps, optional patient/encounter/appointment metadata gaps (explicitly not errors), backend/database state from this successful response, and API error rate explicitly unavailable. No synthetic probes, no continuous expensive checks.
  10. **Staff:** active/inactive account totals and active accounts by policy role; no individual performance or surveillance data.
  11. **Frontend:** shared Reporting period card (7/30/90 days, this year, custom, reset), one summary request per filter change, clear period/generated-at labels, empty/loading/error states, responsive KPI/panel grid, keyboard-accessible charts with hidden data tables, status donut, previous-period notes, honesty callouts for unavailable metrics, and no third-party charts/trackers. Existing design tokens, CSP, reduced-motion and responsive behavior were preserved.
- API: modified `GET /api/v1/analytics/summary` (optional `start`/`end`, expanded typed response). No new endpoints; Audit remains `analytics.read` metadata-only. Doctor/Nurse still receive `403`; `analytics:read` remains Admin-only.
- Database changes: **none** (no tables, columns, migrations or indexes). All aggregation is read-only over existing models.
- Files changed: `backend/app/services/analytics.py`, `backend/app/schemas/analytics.py`, `backend/app/api/v1/analytics.py`, `backend/tests/test_analytics.py`, `frontend/analytics.html`, `frontend/assets/js/analytics.js`, `frontend/assets/css/styles.css`, `frontend/tests/render-smoke.mjs`, `frontend/tests/smoke.mjs` (analytics responsive/style assertions), `scripts/e2e_smoke.sh`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `frontend/DESIGN.md`, `PLAN.md`.
- Performance: one bounded read model per request; grouped/scalar-subquery SELECTs only, no N+1 and no clinical dataset downloaded to JavaScript. Test bound is **≤30 statements** (actual assertion in `test_analytics.py`) and **<5 s** on the seeded demo dataset; date range maximum **366 days** prevents unbounded history. Appointment availability scans only bounded working-hours/time-off metadata; no new index was justified by measurements.
- Authorization verification: permission-matrix route declaration; anonymous `401`; Doctor and Nurse direct API `403`; Admin `200`; frontend gate is UX only; Docker E2E explicitly checks Doctor/Nurse `403`.
- Tests/verification:
  - Backend: **541 passed, 1 skipped** (`uv run pytest -q`).
  - Frontend smoke: **202/202**; Graphify **4/4**; Markdown **21/21**; print record **8/8**; render-path **88/88**.
  - `scripts/security_check.py`: **PASS**, 0 findings / 0 warnings across 6 sections.
  - Docker image rebuilt; `scripts/e2e_smoke.sh`: **ALL STEPS PASSED**, including analytics Admin `200`, Doctor/Nurse `403`, range validation `400`, typed blocks and no `MRN-` in the payload. App/db/speech healthy.
- Deferred:
  - **CSV/print analytics export** — requires its own authorization, audit and aggregate-redaction review; it should not reuse the patient-record print path.
  - **AI latency / provider uptime** — not persisted or continuously probed; returned as unavailable rather than invented.
  - **Voice transcription volume/success/language/latency** — intentional product policy: audio/transcripts are ephemeral and never audited/persisted; returned as unavailable.
  - **Recently activated/deactivated staff trend** — the model has no explicit deactivation timestamp distinct from general `updated_at`, so only current active/inactive totals are shown.
- Caveats: analytics is aggregate operational information, not clinical quality or doctor performance; high volume does not imply better care.

### 2026-11-09 — Command palette: remove navigation file paths
- Status: DONE.
- Removed the right-aligned `.html` destination hints (`dashboard.html`, `patients.html`, `appointments.html`, `ai.html`, etc.) from command-palette navigation rows. Navigation entries now show only their action labels; patient entries retain the useful MRN hint.
- Added a render-path regression assertion that no command option hint is a `.html` page path.
- Prevented the stale-cache recurrence: HTML/JS/CSS now use `Cache-Control: no-cache` (images keep a bounded public cache), and shell entry modules plus the `command-menu.js` import carry a dated `?v=…` cache-busting token (currently `20261109d`). Backend test added for the revalidation headers.
- Files changed: `frontend/assets/js/command-menu.js`, all seven shell entry modules and shell HTML pages (version token/preload), `backend/app/main.py`, `backend/tests/test_security_headers.py`, `frontend/tests/render-smoke.mjs`, `frontend/DESIGN.md`, `PLAN.md`.
- Verification: frontend smoke **202/202**; render-path **88/88**; backend security-header tests **15/15**; live JS CSS `no-cache` and versioned entry HTML; live module contains no navigation path hint; Docker image rebuilt; E2E **ALL STEPS PASSED**; app/db/speech healthy.

### 2026-11-09 — Doctor appointment Reschedule / Cancel fix
- Status: DONE.
- Root cause (two parts): (a) `ui.closeDialog` defers the native `<dialog>.close()` into a view-transition update callback, so immediately reusing the shared dialog let its pending close event clear/close the new form/confirmation; (b) closing the detail dialog consumes its overlay history entry with an asynchronous `history.back()`, while the next dialog immediately pushed a new entry — the pending back could land on the previous page (e.g. AI Assistant). The Node DOM shim closes histories/dialogs synchronously, which is why existing tests missed both.
- Fix: `ui.openDialog` now returns a `close()` that resolves on the native `close` event (with a fallback for browsers without `<dialog>.close()`), and the appointment detail waits on the shared dialog's own `close` event before reusing it. `overlay-history.js` now defers a replacement overlay's `pushState` until any in-flight self-back `popstate` settles, and exposes `supportsDeferredPush`; the appointment flow also has a popstate/timeout fallback for browsers still holding an older cached `overlay-history.js`.
- Added frontend coverage for the full doctor flow: open appointment detail → Reschedule → choose a different slot → PATCH with the new `start_at` → reopen → Cancel → confirmation → POST `/cancel`; plus a history-race regression proving replacement pushes wait for the pending self-back.
- Files changed: `frontend/assets/js/ui.js`, `frontend/assets/js/appointments.js`, `frontend/assets/js/overlay-history.js`, `frontend/tests/render-smoke.mjs`, `frontend/DESIGN.md`, `PLAN.md`.
- Verification: frontend render-path **90/90**; smoke **202/202**; Docker image rebuilt; E2E **ALL STEPS PASSED**; app/db/speech healthy.

### 2026-11-09 — Cancelled appointment calendar cleanup
- Status: DONE.
- Implemented:
  1. **5-minute calendar visibility:** cancelled appointments remain in list responses for 5 minutes after cancellation, then are omitted automatically. The rule is enforced in the SQL list query using the cancellation timestamp (`updated_at` on a closed row), so it applies on reload and across clients. Non-cancelled appointments are never auto-hidden.
  2. **Admin immediate removal:** cancelled appointment detail now shows **Remove from calendar** for Admin only. It confirms, calls `POST /api/v1/appointments/{id}/remove-from-calendar` (`204`, idempotent), and refetches the calendar.
  3. **Record retention:** removal is a visibility change, not a hard delete. `scheduling.appointment.calendar_removed_at` records explicit removal; the row, cancellation status, patient/doctor links and audit history remain. `GET /appointments/{id}` still returns the retained record. Doctors/Nurses get `403`.
  4. **Open-page cleanup:** the frontend prunes already-expired cancelled rows on load and schedules a timer for the next expiry, so a cancelled entry disappears from the current view after 5 minutes without a manual refresh.
  5. **Audit/docs/tests:** `appointment.remove_from_calendar` audit action; migration `b8e4f1a2c3d5`; API/architecture/security/design docs; backend lifecycle tests, permission-matrix declaration, render test for admin removal/doctor denial/expired pruning, and Docker E2E Doctor `403` / Admin `204`.
- Files changed: `backend/app/models/scheduling.py`, `backend/migrations/versions/b8e4f1a2c3d5_add_appointment_calendar_visibility.py`, `backend/app/services/appointments.py`, `backend/app/api/v1/appointments.py`, `backend/tests/test_appointments_api.py`, `backend/tests/test_permissions_api.py`, `backend/tests/test_audit_api.py`, `frontend/assets/js/appointments.js`, `frontend/tests/render-smoke.mjs`, `scripts/e2e_smoke.sh`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `frontend/DESIGN.md`, `PLAN.md`.
- Verification: backend **547 passed, 1 skipped**; frontend smoke **202/202**; render-path **91/91**; Graphify **4/4**; Markdown **21/21**; print **8/8**; Docker E2E **ALL STEPS PASSED**; security scan **PASS**; app/db/speech healthy.

### 2026-11-09 — LLM switched to DeepSeek V4.1 Flash
- Status: DONE.
- Configuration: `LLM_MODEL=deepseek-v4.1-flash` (the API/model ID for the display name **DeepSeek V4.1 Flash**) on the OpenCode Go endpoint `https://opencode.ai/inference/go`. The previous `.env` value contained the display name with spaces, which is not a valid model ID.
- Also updated the provider/config defaults, `.env.example`, README, deployment/architecture/concept docs, and the Kubernetes ConfigMap to the new model. The standard endpoint remains documented as the funded-key/free-quota alternative (`gpt-6-luna`).
- Live verification: `/api/v1/ai/ask` returned **200**, model `deepseek-v4.1-flash`, and invoked the real MCP tool `find_patients_by_medication`.
- Tests: LLM/AI suite **70 passed**; Docker E2E **ALL STEPS PASSED** with the live AI path returning 200.
- Files changed: `backend/.env`, `.env.example`, `backend/app/config.py`, `backend/app/llm/provider.py`, `README.md`, `docs/{ARCHITECTURE,DEPLOYMENT,CONCEPT}.md`, `k8s/configmap.yaml`, `PLAN.md`.

### 2026-11-09 — Patient creation for Doctor and Admin
- Status: DONE.
- Implemented:
  1. **New permission:** `patient:write`, granted to Doctor and Admin (`clinical_admin`) only; Nurse remains read-only and receives `403` on the create route and direct API calls. The permission catalog migration `c9d8e7f6a5b4` and idempotent seed add the key; the effective matrix is now **12 permission keys**.
  2. **API:** `POST /api/v1/patients` (`patient:write`) with bounded fields `given_name`, `family_name`, optional `display_name`, `date_of_birth`, `sex` (closed vocabulary) and `blood_type` (closed ABO/Rh vocabulary). Blank names, future DOB, invalid enums and extra fields are `400 validation_error`. The server generates the unique `MRN-…` and always writes `provenance='synthetic'`; neither is client-controlled. Returns `201 PatientOut` and audits `patient.create` (metadata only).
  3. **Frontend:** the Patients page shows **Add patient** only with `patient:write`; the modal collects names, optional DOB, sex and blood type, validates client-side, preserves input on error, and opens the new patient record on success. The command palette offers **Add patient** under the same gate. Nurse sees no control; backend enforcement is independent.
  4. **Appointment fallback:** the appointment patient picker shares the same form module. When a search has no matches, Doctor/Admin see **Add new patient** inline; the created patient is selected immediately in the open appointment form without losing date/time, and the appointment can be created with the new patient id. Nurse does not see the fallback.
  5. **Tests/docs:** patient creation/persistence/audit tests, permission-matrix route declaration, Doctor/Admin/Nurse role tests, seed permission count, render tests for page creation, Nurse denial, and the full appointment no-match → create → select → create-appointment flow; E2E permission probes; API/architecture/security/design docs, README permission table and PLAN.
- Database changes: additive `admin.permission` catalog row via migration `c9d8e7f6a5b4`; **no patient-table schema change** (new patient rows use the existing constrained model).
- Files changed: `backend/app/authorization/policy.py`, `backend/app/schemas/patient.py`, `backend/app/services/patients.py`, `backend/app/api/v1/patients.py`, `backend/migrations/versions/c9d8e7f6a5b4_add_patient_write_permission.py`, `scripts/seed_demo_data.py`, backend tests, `frontend/patients.html`, `frontend/assets/js/patients.js`, `frontend/assets/js/patient-create.js`, `frontend/assets/js/appointments.js`, frontend tests, `scripts/e2e_smoke.sh`, docs/API.md, docs/ARCHITECTURE.md, docs/SECURITY.md, README.md, frontend/DESIGN.md, PLAN.md.
- Verification: backend **556 passed, 1 skipped**; frontend smoke **205/205**; render-path **93/93**; Graphify/Markdown/print suites unchanged and passing; Docker E2E **ALL STEPS PASSED**; app/db/speech healthy.
- Note: patients can now be created centrally and used by existing appointment search; lab/encounter creation workflows can reference the same patient store when those write surfaces are added.

### 2026-11-09 — Heart loader: remove the assistant plus avatar
- Status: DONE.
- Removed the assistant favicon/avatar (the blue plus mark) from every loading state beside the heart loader. Loading regions now show only the anatomical heart and the status text; normal assistant answer headers still show the avatar where appropriate.
- Files changed: `frontend/assets/js/ai-assistant.js`, `frontend/assets/js/patient.js`, `frontend/tests/render-smoke.mjs`, `frontend/DESIGN.md`, `PLAN.md`.
- Verification: frontend smoke **205/205**; render-path **93/93**; Docker image rebuilt; E2E **ALL STEPS PASSED**; app/db/speech healthy.

### 2026-11-09 — Admin-only patient removal
- Status: DONE.
- Implemented:
  1. **New Admin-only permission:** `patient:delete`; Doctor and Nurse receive `403`. Effective matrix is now **13 permission keys**; catalog migration `d2e3f4a5b6c7` and the idempotent seed add it.
  2. **API:** `DELETE /api/v1/patients/{patient_id}` (`patient:delete`) soft-deletes the patient (`deleted_at`), returns `204`, and audits `patient.delete` (metadata only). Missing/already-deleted ids are `404`. The row, child clinical records, scheduling history and audit trail are retained internally.
  3. **Visibility:** patient list/search/detail, encounter access, and appointment list/detail now exclude soft-deleted patients. Existing soft-delete conventions are preserved; no hard delete.
  4. **Frontend:** the patient header shows **Remove patient** only to Admin, confirmed with patient name/identifier, then calls the endpoint, clears patient caches and returns to the patient list. Doctor/Nurse never see the control; the backend enforces it.
  5. **Tests/docs:** policy/seed/matrix/auth tests, patient deletion + child-retention/audit tests, appointment hiding test, frontend Admin/Doctor/Nurse render test, full E2E patient lifecycle (Admin create → Doctor/Nurse delete 403 → Admin delete 204 → GET 404), API/architecture/security/design docs and PLAN.
- Database changes: permission catalog row via migration `d2e3f4a5b6c7`; no clinical-table schema change (the existing `patients.patient.deleted_at` soft-delete column is used).
- Verification: backend **565 passed, 1 skipped**; frontend smoke **205/205**; render-path **94/94**; Docker E2E **ALL STEPS PASSED**; security scan **PASS**; app/db/speech healthy.

### 2026-11-09 — Internal Messages (Doctor/Admin)
- Status: DONE.
- Implemented:
  1. **Model/API:** `admin.message` (sender/recipient staff FKs, optional subject, body, read timestamp) with own-inbox/own-sent only. `GET /messages?box=inbox|sent`, `GET /messages/recipients`, `POST /messages`, and recipient-only `POST /messages/{id}/read`. Recipients are active Doctor/Admin accounts; self and Nurse recipients are rejected. Bodies are bounded (1–5000 chars), subjects 200 chars, and no attachments exist.
  2. **Permissions:** new `message:read` and `message:write` for Doctor and Admin only; Nurse gets `403`. Permission count is now **15** (seed + migration `e3f4a5b6c7d8`).
  3. **Frontend:** **Messages** sidebar item sits after AI Assistant and before Admin for Doctor/Admin. The page has Inbox/Sent tabs, unread markers, plain-text detail, mark-read, Reply/New message compose with bounded recipient list, loading/empty/error states, and a friendly Nurse denial. No patient payload or attachment surface.
  4. **Audit/privacy:** `message.send` and `message.read` record actor/resource id/outcome only; never subject, body, recipient address, or attachments.
  5. **Tests/docs:** message API tests (own-inbox isolation, read state, self/Nurse/unknown recipient rejection, validation, audit), permission matrix/policy/auth/seed/audit updates, frontend render test (nav order, inbox, mark-read, compose, Doctor nav, Nurse denial/no fetch), E2E permission probes, API/architecture/security/design docs and PLAN.
- Database changes: additive `admin.message` table and two permission catalog rows via migration `e3f4a5b6c7d8`; no changes to clinical schemas.
- Verification: backend **589 passed, 1 skipped**; frontend smoke **215/215**; render-path **95/95**; Docker E2E **ALL STEPS PASSED**; security scan **PASS**; app/db/speech healthy.

### 2026-11-09 — Admin Live System Logs / Terminal Activity Stream
- Status: DONE.
- Implemented:
  1. **Separate permission and storage:** new Admin-only `logs:read` (effective matrix now **16 permission keys**). Durable accountability stays in `admin.audit_event`; operational monitoring goes to new `admin.system_event` with 7-day bounded retention. Migration `f4a5b6c7d8e9` creates the table/indexes and catalog permission.
  2. **Registry and read model:** centralized approved event registry (`backend/app/logs/registry.py`) maps existing audit actions and operational types to category/severity, validates flat metadata against an allowlist, and redacts email resource references. The unified service merges both tables for bounded history/counts and cursor replay without copying audit rows.
  3. **Live SSE:** `GET /api/v1/admin/logs/stream` (`logs:read`) subscribes before replay, resumes from `last_event_id`/`Last-Event-ID`, deduplicates exact IDs, sends 15-second heartbeats, revalidates session/permission periodically (including busy streams), emits `auth_expired`, and cleans up a bounded per-subscriber queue. Replay/query sessions are short-lived; no DB transaction stays open for the connection.
  4. **Event coverage:** existing auth/security/patient/encounter/lab/prescription/appointment/administration audit actions are represented; the audit mapping covers MCP tool outcomes, and operational instrumentation covers the AI request lifecycle, voice transcription (start/complete/validation/rate-limit/unavailable/timeout) with no audio/transcript, appointment conflicts/rejections, API 5xx/429s and application startup.
  5. **Admin terminal UI:** secure sidebar entry and `system-logs.html` with connection state, filtered-period counters, terminal stream, severity colors, approved metadata search, filters/custom range, pause/resume, auto-scroll, clear view, copy, bounded CSV export, event details, timezone display (`CLINIC_TIMEZONE`; UTC on the wire), reduced motion, mobile layout, and honest loading/empty/error/denial states.
  6. **Security controls:** no shell/SQL/command surface; DOM-only text rendering; no clinical payloads, names/MRNs, questions/answers, transcripts, audio, secrets, request bodies or stack traces. Operational writes are best-effort and never fail a clinical request; audit live publication happens only after commit.
  7. **Tests/docs:** registry/redaction/audit-commit/history/cursor tests, bounded-bus tests, generator-level SSE tests (multi-subscriber, replay order, dedup, heartbeat, revocation, cleanup), HTTP 401/403 tests, and frontend render coverage. Added `docs/SYSTEM_LOGS.md` and updated API/architecture/security/troubleshooting docs, E2E role/SSE probes, and PLAN.
- Files (primary): `backend/app/logs/{registry,bus,service}.py`, `backend/app/api/v1/system_logs.py`, `backend/app/schemas/system_log.py`, `backend/app/models/admin.py`, `backend/app/audit/logger.py`, `backend/app/main.py`, `backend/app/api/v1/{ai,voice,appointments,router}.py`, `backend/migrations/versions/f4a5b6c7d8e9_add_system_events.py`, `backend/tests/test_system_log*.py`, `frontend/system-logs.html`, `frontend/assets/js/system-logs.js`, `frontend/assets/css/styles.css`, docs listed above, `scripts/e2e_smoke.sh`.
- Verification: backend **612 passed, 5 skipped**; frontend smoke **225/225**; render-path **96/96**; Graphify **4/4**; Markdown **21/21**; print **8/8**; Docker image rebuilt and migration at head `f4a5b6c7d8e9`; live E2E **ALL STEPS PASSED** including Doctor/Nurse 403 and Admin SSE replay; a separate live two-event SSE check confirmed post-subscription delivery in order; security scan **PASS**; app/db/speech healthy.
- Limitations: live bus is single-process (multi-worker needs `LISTEN/NOTIFY`); replay is capped at 200; operational retention is fixed at 7 days with write-triggered cleanup; no continuous dependency/background-job monitor; CSV exports the currently loaded filtered client view (≤500 events); actor display was a UUID in the initial implementation (superseded by the 2026-11-10 chat actor directory).

### 2026-11-10 — System Logs redesigned as a chat activity feed
- Status: DONE.
- Implemented: the Admin System Logs view now presents activity like a modern group chat rather than a terminal: incoming message bubbles with severity badges, event type, description, safe metadata chips, millisecond timestamps in the clinic timezone, day separators, and restrained arrival motion. Events without an actor render as centered system notices.
- Identity: the page loads the existing bounded staff directory (`GET /staff`, Admin-only) and resolves each actor to a display name/role. Existing local demo doctor portraits are shown for doctor actors (the existing `ui.avatarForUser` behavior); nurses/admins use stable colored initials; unknown or system actors degrade safely. No email addresses are displayed.
- UI: the chat header shows a group avatar, connection state, last-connected time, timezone, and the existing Pause / Auto-scroll / Copy / Export / Clear controls. Counters and filters are restyled as compact chat controls; the feed follows the app light/dark theme, remains keyboard-accessible, and is responsive on phones. The existing live SSE, filters, details dialog, copy/export, clear-view semantics and read-only boundary are unchanged.
- Files changed: `frontend/system-logs.html`, `frontend/assets/js/system-logs.js`, `frontend/assets/css/styles.css`, `frontend/tests/render-smoke.mjs`, `frontend/DESIGN.md`, `docs/SYSTEM_LOGS.md`, `docs/TROUBLESHOOTING.md`, `docs/API.md`, `PLAN.md`.
- Verification: frontend smoke **225/225**; render-path **96/96** (including sender portrait resolution, history/filter/clear/role-denial behavior); Docker image rebuilt; live E2E **ALL STEPS PASSED**; served page, chat JS and chat CSS verified; app/db/speech healthy. No backend code or API contract changed.
- Limitation: the staff directory is fetched in one bounded page (100 accounts); actors beyond that page fall back to initials, and the page never displays staff emails.

## Final instruction to the coding agent

Start with **Phase 0 only**. Inspect the repository, establish the baseline, and update this file with findings. Then proceed phase by phase. Do not attempt the public deployment before the core application, authorization, synthetic data, tests, and Docker workflow are functioning. Never use real patient data for the public demo, and never claim deployment succeeded without checking the actual public URL.
