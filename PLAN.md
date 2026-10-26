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
- Roles: doctor, nurse, clinical administrator, technical administrator.
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
10. **Use synthetic data only.** Seed data must be fictional and obviously marked as demo data.
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
│   ├── login.html
│   ├── dashboard.html
│   ├── patients.html
│   ├── patient.html
│   ├── admin.html
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
- **Nurse:** View patient information permitted by the configured demo policy; no staff-role administration.
- **Clinical administrator:** Manage appropriate clinical workflow metadata and permitted staff accounts, subject to explicit policy. Do not automatically grant technical configuration or unrestricted clinical-data access.
- **Technical administrator:** Manage technical settings and user access where explicitly permitted. Do not automatically grant clinical access merely because the user is a technical administrator.

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
| 3 | PostgreSQL schema and migrations | DONE | 2026-10-26 | 4 schemas, FKs/indexes/soft-deletes/UTC/Numeric, Alembic up+down+up verified, idempotent synthetic seed (10 patients incl. apostrophe/accents/RTL/emoji) |
| 4 | Backend foundation and API conventions | DONE | 2026-10-26 | Error envelope, request-id middleware, pagination bounds, CORS allowlist, body limit, log redaction, 88 tests passing |
| 5 | Authentication and role authorization | DONE | 2026-10-26 | DB-backed sessions (hashed tokens), argon2id, CSRF bootstrap, rate limiting, principal wiring, 401/403 verified at API layer over full role×endpoint matrix |
| 6 | Synthetic data and clinical-data APIs | DONE | 2026-10-26 | patients/summary/labs/prescriptions/encounters + staff/roles/audit APIs, audit writer, check_patient_access seam, IDOR/concealment tested, 242 tests passing |
| 7 | MCP server and tool authorization | NOT STARTED | — | |
| 8 | LLM integration and safe response handling | NOT STARTED | — | |
| 9 | Frontend experience | NOT STARTED | — | |
| 10 | Admin panel and staff management | NOT STARTED | — | |
| 11 | Audit, privacy, and security hardening | NOT STARTED | — | |
| 12 | Automated tests and CI | IN PROGRESS | — | CI workflow + secret scanner + dependency audit landed (3 jobs, no secrets to fork PRs); blocked on MCP/LLM tests (Wave 3) and dependency bumps (starlette/mcp HIGH advisories) before green |
| 13 | Docker and local end-to-end validation | NOT STARTED | — | |
| 14 | Public demo deployment preparation | NOT STARTED | — | |
| 15 | Deploy publicly and verify website | NOT STARTED | — | |

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
- [ ] Add data provenance/demo labels so the UI makes clear the records are fictional.

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
- [ ] Landing/login page with product name and demo-only notice.
- [ ] Authenticated dashboard with a clear navigation system.
- [ ] Patient list/search with loading, empty, error, and pagination states.
- [ ] Patient detail page with summary, encounters, labs, and prescriptions.
- [ ] AI assistant panel for questions about the current synthetic patient, if authorized.
- [ ] User profile/session/logout control.
- [ ] Admin navigation visible only to authorized roles; backend still enforces all permissions.
- [ ] Responsive layouts for desktop, tablet, and narrow mobile widths.
- [ ] Accessible labels, keyboard navigation, visible focus, semantic headings, and sufficient contrast.
- [ ] Consistent typography, spacing, colors, buttons, tables, cards, and feedback messages.
- [ ] Confirmation for destructive or high-impact admin actions.
- [ ] Friendly 401/403/404/500 and offline/backend-unavailable states.
- [ ] Display “Synthetic demo data — not for clinical use” prominently in the app.
- [ ] Avoid third-party analytics, trackers, and external scripts unless explicitly justified and reviewed.
- [ ] Do not put clinical data in local storage, analytics events, or browser console logs.

## Acceptance criteria
- [ ] A user can log in, navigate, search a patient, and inspect permitted synthetic data.
- [ ] The frontend calls the backend, not the database.
- [ ] Every interactive control works; no decorative fake buttons.
- [ ] Layout remains usable on mobile.
- [ ] Keyboard-only navigation supports primary workflows.
- [ ] No secrets are bundled into JavaScript.
- [ ] Unauthorized navigation does not expose protected data even if a user manually opens a page.

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
- [ ] Add a privacy/demo notice and contact/issue-reporting path appropriate to the project.
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
- [ ] Add a public demo notice and a support/issue-reporting link.
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
15. Verify the visible synthetic-data and non-clinical-use notice.

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

## Final instruction to the coding agent

Start with **Phase 0 only**. Inspect the repository, establish the baseline, and update this file with findings. Then proceed phase by phase. Do not attempt the public deployment before the core application, authorization, synthetic data, tests, and Docker workflow are functioning. Never use real patient data for the public demo, and never claim deployment succeeded without checking the actual public URL.
