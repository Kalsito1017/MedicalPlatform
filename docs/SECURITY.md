# Security

**Scope:** role/permission model, authentication design, threat model, audit design, secrets handling, and explicit non-claims for the Clinical AI Platform.
**Status:** living document, updated as phases land. Written at Phase 1; controls described here are implemented across Phases 5, 7, 8, and 11 and are marked accordingly where not yet built. The permission matrix below is reproduced verbatim from `docs/API.md` (frozen contract); the enforced source of truth is `backend/app/authorization/policy.py`.

---

## 1. Roles and permissions

### Role intent (owner decision, 2026-10-26: three roles; `tech_admin` removed)

- **Doctor:** View patient records and clinical data permitted by the configured demo policy; use clinical summary and query workflows.
- **Nurse:** Least privilege — view patient information and clinical data permitted by the configured demo policy, plus internal staff messaging. No staff-role administration, no audit access, no admin panel.
- **Admin** (`clinical_admin`): Full access — clinical data, staff accounts, roles, audit log, and analytics. Administration is exercised through the same server-side permission checks as everything else.

Roles do **not** automatically imply that a user may view every patient. The demo uses a
simplified synthetic-data access policy (documented here and in `docs/API.md`); a
patient-care-relationship/assignment policy is required before any real data — tracked
in `docs/BACKLOG.md`.

### Permission matrix (identical to `docs/API.md`)

| Permission | doctor | nurse | clinical_admin (Admin) |
|---|---|---|---|
| `patient:read` | ✔ | ✔ | ✔ |
| `patient:write` | ✔ | ✖ | ✔ |
| `patient:delete` | ✖ | ✖ | ✔ |
| `message:read` | ✔ | ✔ | ✔ |
| `message:write` | ✔ | ✔ | ✔ |
| `lab:read` | ✔ | ✔ | ✔ |
| `prescription:read` | ✔ | ✔ | ✔ |
| `staff:read` | ✖ | ✖ | ✔ |
| `staff:manage` | ✖ | ✖ | ✔ |
| `role:assign` | ✖ | ✖ | ✔ |
| `audit:read` | ✖ | ✖ | ✔ |
| `logs:read` | ✖ | ✖ | ✔ |
| `analytics:read` | ✖ | ✖ | ✔ |
| `appointment:read` | ✔ | ✔ | ✔ |
| `appointment:write` | ✔ | ✖ | ✔ |
| `appointment:manage_availability` | ✔ | ✖ | ✔ |
| `appointment:export` | ✔ | ✖ | ✔ |

Internal messaging is the deliberate exception to the Nurse's read-only
boundary: `message:read` and `message:write` add staff-to-staff communication
only. They grant no staff, audit, analytics, patient-write or appointment-write
access. Inbox/sent reads are always scoped to the authenticated caller, read
state is recipient-only, and Admin has no implicit access to another user's
private conversations.

**Message attachments.** One optional file may accompany an internal message.
The body-size middleware raises the 1 MiB global limit only for the exact
`POST /api/v1/messages/attachments` route, clamped to an 8 MiB hard ceiling; the
service separately enforces `MESSAGE_ATTACHMENT_MAX_BYTES` (default 5 MiB) on
the file itself. Only PDF, PNG, JPEG, WebP, TXT, CSV, DOCX and XLSX are allowed;
SVG/HTML/executables are rejected, the filename is reduced to a safe basename,
and simple magic-signature checks reject mismatched content. Bytes are stored in
PostgreSQL with the message and served only to the sender or recipient as an
attachment with `nosniff` and `no-store`; unrelated staff (including Admin) get
`404`. There is no antivirus/content-disarm scanner, so downloaded files remain
untrusted and must not be opened as active content.

Deny by default. `nurse` is least-privilege clinical read with a **read-only
clinic-wide appointment view** (`appointment:read` only; no writes, no
availability management, no voice scheduling); `clinical_admin` (Admin) holds
every permission. `analytics:read` is enforced by
`GET /api/v1/analytics/summary` (wave U, 2026-10-27; expanded with validated ranges and operational metrics 2026-11-09). Appointment routes
(2026-11-09) enforce doctor-own-calendar scoping server-side; the Admin/nurse
reader must name a doctor for scoped availability/slot reads, and writes cannot
be performed by a nurse or by a doctor targeting another doctor's calendar
through a request body or AI output. There are now **17** permission keys.

### Where checks live

- **One policy module:** `backend/app/authorization/policy.py` is the single place where
  roles map to permissions. Both the REST API and every MCP tool consult it. There is no
  second mapping, no inline role checks scattered in handlers, and no client-side
  permission logic that matters.
- **Server-side enforcement only.** Hiding a button or page in the UI is never a
  control. Every protected endpoint and tool re-checks authorization on the server;
  unauthenticated requests get `401`, authenticated-but-unpermitted requests get `403`
  (concealed resources get `404` — see `docs/API.md`).
- **Per-request evaluation.** Permissions are recomputed from the database per request,
  so role changes take effect predictably for existing sessions.

---

## 2. Authentication design

Implemented in `backend/app/auth/` (Phase 5). No 4-digit PIN. No long-lived tokens in
browser storage.

### Password hashing

- **argon2id** (via `argon2-cffi`) with library-default safe parameters (memory cost,
  time cost, parallelism tuned at implementation and recorded in code).
- Only the hash is stored (`admin.staff_user.password_hash`). Plaintext passwords exist
  only transiently at login/seed time and are never logged, echoed, or committed.
- Demo account passwords are supplied at **seed time** via `DEMO_*` environment
  variables (see `.env.example`), never committed to the repository.

### Sessions

- Server-side session records (database), referenced by the `cap_session` cookie.
- Cookie attributes: **`HttpOnly`** (JavaScript cannot read it — shrinks XSS impact),
  **`Secure`** (sent only over HTTPS), **`SameSite=Lax`** (blocks cross-site POST/PUT/
  DELETE from carrying the cookie; top-level GET navigation is tolerated so links into
  the app work), **`Path=/`**.
- Expiry: `SESSION_TTL_HOURS` (default 8). Expired sessions are rejected with `401`.
- Revocation: logout (`POST /api/v1/auth/logout`) invalidates the session server-side;
  deactivating a user (`PATCH /api/v1/staff/{user_id}` with `is_active: false`) prevents
  further use because the session resolves to a user that is no longer active. A "revoke
  all sessions for a user" admin UI is a backlog item (`docs/BACKLOG.md`).

### CSRF

- **Double-submit token.** The server issues a `csrf_token` cookie; every non-GET request
  must echo it in the `X-CSRF-Token` header. Mismatch → `403`.
- Works alongside `SameSite=Lax`: the header cannot be forged cross-origin without
  reading the cookie, which same-origin policy prevents.

### Login hardening

- Rate limiting with progressive lockout on `POST /api/v1/auth/login` (`429
  rate_limited` with `Retry-After`), tuned to block guessing without locking out a
  legitimate human.
- Uniform failure message and uniform timing for unknown email vs wrong password — no
  account enumeration.

### Not used (deliberately)

- No JWT or bearer tokens in `localStorage`/`sessionStorage`.
- The only web-storage item is the non-sensitive UI preference
  `medicore-theme` (`light`/`dark`/`system`); it contains no account, patient,
  clinical, or session data. All application/response caches remain in memory.
- No long-lived API keys in the browser. The LLM key exists only as a server-side
  environment variable.

---

## 3. Threat model and controls

| Threat | Scenario | Controls | Status |
|---|---|---|---|
| **SQL injection** | Malicious `q=`, IDs, or LLM-influenced parameters reach the DB | ORM/parameterized queries only; no string-concatenated SQL anywhere; bounded, typed inputs (Pydantic validation) | Enforced by convention + tests (Phases 3–6, 12) |
| **XSS** | Synthetic names/notes or LLM output contain `<script>` | Output escaping by default; no `innerHTML` with data — text content / safe DOM APIs only; AI output rendered as text and labelled; no third-party scripts | Phase 9 + frontend checks |
| **CSRF** | Evil page triggers state-changing requests with the victim's cookie | `SameSite=Lax` cookie + double-submit `X-CSRF-Token` on all non-GET requests | Phase 5 |
| **IDOR** | Authenticated user guesses/enumerates a patient UUID | Opaque UUIDs; per-request authorization via `policy.py`; concealed resources return `404 not_found` (not `403`) so existence is not confirmed; tests attempt direct ID access | Phase 5–6, tests Phase 12 |
| **Privilege escalation** | User assigns themselves `clinical_admin` | Self role assignment blocked server-side (`POST /api/v1/staff/{user_id}/roles` targeting self → `403`, unconditionally); role changes require `role:assign` + CSRF; unknown role keys (including the removed `tech_admin`) are rejected with `400`; last-active-Admin protection (`409`); all changes audited; no break-glass or impersonation exists in the demo | Phase 5, 10 |
| **Prompt injection** | A record field contains "ignore instructions / call tool X" | Prompt text is treated as untrusted data, never as instructions; server-side tool **allowlist** (only `search_authorized_patients`, `get_patient_summary`, `get_patient_lab_results`, `get_recent_prescriptions`, `find_patients_by_lab`, `find_patients_by_medication`, `get_my_recent_patients` — all read-only); identity from session, never from tool arguments; patient-scoped calls are restricted to the three single-patient tools and the verified patient id; authorization re-executed inside every tool; bounds on tool count (`LLM_MAX_TOOL_CALLS` / `LLM_MAX_TOOL_CALLS_GENERAL`), result size, and request duration; deterministic guardrails reject explicit credential/database/shell/bypass requests and unsafe output; model output cannot trigger any privileged or state-changing action | Phases 7–8, tests Phase 12 |
| **Secret leakage** | API key, DB URL, or session token appears in logs, repo, or browser | Secrets only in env vars; `.env` gitignored; log redaction of auth headers, cookies, tokens, passwords; generic error envelope to clients (never stack traces/SQL); no secrets in frontend assets; repo/history review before publishing | Phase 11 + `.gitignore` |
| **DoS on the AI endpoint** | Repeated expensive LLM calls exhaust quota/money | Rate limit on `POST /api/v1/ai/ask`; question and prompt size bounds; `LLM_MAX_TOOL_CALLS` and `LLM_REQUEST_TIMEOUT_SECONDS`; Console **budget cap** on the service-account key; `LLM_ENABLED=false` kill switch; endpoint degrades to `503` without affecting the rest of the app | Phases 8, 11 |
| **Credential stuffing / brute force** | Automated password guessing | Login rate limiting + progressive lockout; uniform errors; argon2id cost slows offline cracking if hashes ever leak | Phase 5 |
| **Session theft (XSS → session)** | Script steals the session cookie | `HttpOnly` cookies; XSS controls above; server-side revocation on logout | Phase 5, 9 |

### Focused AI guardrails

`backend/app/llm/guardrails.py` is a deterministic supplementary layer around
the LLM loop, not a replacement for the trusted authorization boundary:

- Input requests for system prompts, credentials/DSNs, SQL, shell/filesystem/
  database access, authorization bypass, or out-of-scope patients fail closed
  before the provider call.
- Patient-scoped tool execution is restricted to `get_patient_summary`,
  `get_patient_lab_results`, and `get_recent_prescriptions` with exactly the
  verified `patient_id`; cross-patient tools and mismatched arguments are
  denied before MCP.
- Tool payloads are minimized before prompt insertion: record/citation IDs and
  clinically necessary facts remain; MRNs, raw dates of birth, and unnecessary
  foreign keys are removed from model context.
- Output containing secret/DSN/token/private-key patterns, external URLs, raw
  HTML/script/event-handler payloads, internal prompt/config text, or citation
  IDs absent from authorized tool results is blocked.

Blocked requests receive a generic `403 forbidden`; no question, answer, prompt,
or clinical payload is logged or audited. These checks are lexical and bounded —
they are defence in depth, not proof that arbitrary model output is safe.

### HTTP security headers

Set on **every** response — API, static pages, and error envelopes — by
`SecurityHeadersMiddleware` (`backend/app/main.py`). Verified by
`backend/tests/test_security_headers.py` and `scripts/e2e_smoke.sh`.

| Header | Value | Why |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | no MIME sniffing of API or asset responses |
| `X-Frame-Options` | `DENY` | the UI is never framed (mirrors CSP `frame-ancestors 'none'`) |
| `Referrer-Policy` | `no-referrer` | the strictest option; the app needs no referrer and patient identifiers live in page URLs, so nothing about navigation may leak cross-origin |
| `Permissions-Policy` | microphone `(self)`; camera, geolocation, payment, sensors, USB, screen capture, … denied (`()`) | voice scheduling uses `getUserMedia` from this origin after an explicit user action; no other browser feature is allowed |
| `Cross-Origin-Opener-Policy` | `same-origin` | isolates the browsing context from cross-origin openers |
| `Cross-Origin-Resource-Policy` | `same-origin` | responses are for this origin only |
| `X-Permitted-Cross-Domain-Policies` | `none` | no Flash/PDF cross-domain policies |
| `Content-Security-Policy` | see below | XSS containment |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | **only** when the request arrived over HTTPS (directly or via `X-Forwarded-Proto: https`); never sent on plain-HTTP localhost |

**CSP (application responses):** `default-src 'self'; base-uri 'self';
object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'
'sha256-…'; style-src 'self'; img-src 'self' data:; font-src 'self';
connect-src 'self'`. The buildless frontend needs exactly one inline script per
page (the `window.__APP_CONFIG__` deployment bootstrap). Its content is hashed
at backend startup, so `script-src` is `'self'` + hashes — **no
`'unsafe-inline'`, no `'unsafe-eval'`**. Editing the bootstrap or page markup
requires a backend restart to recompute the hashes (no code change). Pages
therefore cannot add inline scripts, inline styles, or inline event handlers
without breaking under CSP — by design.

**Development-only relaxation:** the Swagger/ReDoc UI (disabled in production)
receives a relaxed CSP (jsdelivr CDN + inline) on `/docs` and `/redoc` only;
production mode relaxes nothing and the docs UI is off.

**Appointment scheduling controls.** The appointment calendar is scheduling
metadata, not clinical data. Authorization is still enforced server-side:
Doctors read/write only their own calendar; an Admin may act across doctors and
must name `doctor_id` for scoped reads/writes; a Nurse has a **read-only**
clinic-wide view and no write/availability/voice permissions. A Doctor naming
another doctor is `403`; another doctor's appointment id is concealed as `404`
and audited `appointment.denied`. Nurse write requests are `403`. Writes
check patient access, working hours, time off, and both doctor/patient overlap.
Concurrent bookings take PostgreSQL transaction advisory locks on the doctor
and patient and re-check overlap inside the transaction, so two simultaneous
requests cannot both succeed. Availability replacement validates interval
order/overlap and is bounded (≤21 working intervals, ≤100 time-off rows).
Appointment mutations are audited; the AI interpreter can only propose.
Passing the scheduled `end_at` only removes a row from the active calendar; it
does not change status, delete the row, or reuse `calendar_removed_at`.
Historical reads are server-scoped to the Doctor's own history or the
Admin/Nurse clinic-wide read view. `appointment:export` is granted only to
Doctors and Admins; Nurse receives `403`. Exports use an explicit bounded date
range, omit notes/clinical content, and are audited as
`appointment.history.export` without payloads. Lifecycle events store only
appointment id, event type, actor, request id, old/new times/status and
timestamp; they never contain clinical notes.

**Voice transcription and the private speech service.** The microphone is used
only after an explicit user action; `MediaRecorder` is feature-detected, tracks
are stopped immediately on stop, and audio stays in browser memory. The upload
route is the only request allowed above the global 1 MiB body limit; its
allowance is an exact `(POST, /api/v1/voice/transcriptions)` match clamped to a
hard 8 MiB ceiling (default 4 MiB) in `BodySizeLimitMiddleware`, so no other
route inherits it. The server streams the upload to a `0700` temporary
directory, writes the source `0600`, validates the client media type, probes
duration with FFprobe, rejects >`VOICE_MAX_DURATION_SECONDS` (default 60), and
converts with FFmpeg to 16 kHz mono PCM WAV using argument lists (never a
shell) and timeouts. The converted PCM size is bounded and re-read with Python's
`wave` module. The temporary directory is removed in `finally`. Audio and
transcripts are **never** logged, audited, stored in the database, or written to
web storage. The private whisper.cpp service is reachable only on the compose
network in the local stack and is called with an optional bearer token or a
Cloud Run ID token (`WHISPER_AUTH_MODE`); failure is a controlled `503`/`429`.
The AI proposal endpoint calls the model with `tools=[]`, resolves patients
through the existing read-only MCP tool, and never receives or returns authority
to write; final creation is a separate explicit doctor-confirmed request that
re-runs authorization, validation, locks, and audit. Transcription itself stays
inside the private speech service; the AI proposal step is separate and, when
the LLM is enabled, sends the edited transcript (possibly containing a patient
name) to the configured external provider. With `LLM_ENABLED=false` it fails
closed with `503` and makes no external call.

**Production caveat:** when Cloudflare Pages serves the frontend and proxies
`/api/*` to the backend (Phase 14/15), page responses are served by Pages and do
**not** pass through this middleware. The equivalent headers must then be set at
the edge (Cloudflare `_headers` file / Transform Rules) or by proxying the whole
site to the backend; API responses already carry the headers above. This must be
verified before the public launch checklist is signed off. The microphone
header must also be preserved at the edge, or browser recording will be blocked.

---

## 4. Audit design

Recorded in `admin.audit_event` (readable via `GET /api/v1/audit` with `audit:read`):

| Event | Recorded fields |
|---|---|
| Login success / login failure | actor (or attempted email reference), outcome, timestamp, request id |
| Logout | actor, timestamp, request id |
| Permission denial (any `403`) | actor, attempted action, outcome, timestamp, request id |
| Patient-record reads (patient, summary, labs, prescriptions, encounter) | actor, action, resource type + resource id, outcome, timestamp, request id |
| Diagnosis list read (`diagnoses.read`) | actor, patient resource id, outcome, timestamp, request id; **never** diagnosis text or code values |
| Diagnosis ICD-10 code update (`diagnosis.code.update`) | actor, diagnosis resource id, outcome, timestamp, request id; **never** the diagnosis text or the code value in the audit row |
| Transcript patient suggestion (`patient.suggest`) | actor, resource type `patient`, outcome, timestamp, request id; **never** the transcript, suggested name/MRN or search text |
| Patient creation (`patient.create`) | actor, new patient resource id, outcome, timestamp, request id; no clinical payload |
| Patient removal (`patient.delete`) | actor, soft-deleted patient resource id, outcome, timestamp, request id; no clinical payload. The row is retained; child records and audit history are not erased |
| Staff messages (`message.send`, `message.read`, `message.attachment.download`) | actor, message resource id, outcome, timestamp, request id; **never** subject, body, recipient address, filename or attachment bytes |
| MCP tool calls | tool name, actor, target resource reference, outcome, request id |
| Staff / role changes (create, patch, activate/deactivate, role add/remove) | actor, target user, change summary, outcome, request id |
| Appointment mutations (`appointment.create`, `appointment.update`, `appointment.reschedule`, `appointment.cancel`, `appointment.complete`, `appointment.no_show`, `appointment.remove_from_calendar`, `appointment.denied`, `appointment.availability.update`) | actor, appointment/doctor resource reference, outcome, request id; **no** patient clinical content, notes, or transcript |
| Appointment history export (`appointment.history.export`) | actor, resource type `appointment_history`, outcome, request id; **never** filter values, patient names, notes or exported bytes |
| Appointment AI proposal (`appointment.ai.propose`) | actor, proposal resource type, outcome, request id; **never** the transcript, patient name, or model output |
| Transcription correction analysis (`appointment.correction`) | actor, resource type `appointment_transcript`, outcome, request id; **never** the transcript, original/suggested phrases, patient names, MRNs, or model output. Safe counts/language/model/latency go to the operational log only |
| Voice transcription | **not audited by design**: it is a transient audio-to-text operation with no clinical record, and the transcript/audio must not be retained. Safe outcome/latency metadata is written to the separate operational log (`voice.transcription.*`) and shown in System Logs. |
| Analytics reads (`analytics.read`) | actor, resource type `analytics`, outcome, timestamp, request id; **never** the selected range values, returned aggregates, patient identifiers or clinical payload |

Analytics is a **read-only aggregate** surface enforced by `analytics:read`
(Admin only). Every request is re-authorized server-side; Doctor/Nurse direct
API calls receive `403` regardless of navigation. The read model accepts only
validated inclusive UTC dates (both or neither, maximum 366 days) and never
accepts arbitrary SQL, filters or per-patient selectors. Aggregate responses
contain no patient names, MRNs, notes, AI questions/answers, audio, transcripts,
raw audit metadata (IP/user-agent/resource ids), passwords, tokens or secrets.
Detailed audit inspection stays in the separate `audit:read` interface.

Per-request AI latency and voice transcription metrics are **not persisted**;
the AI section reports them as `available: false` rather than logging extra
sensitive data to make a chart. Provider availability is not continuously
probed. Analytics export/print was deferred: a future export must be its own
authorized, audited, aggregate-only path with explicit redaction review.

Deliberately **NOT** recorded:

- Full clinical payloads (no note text, lab values, or record bodies in audit rows).
- Passwords, password hashes, session tokens, CSRF tokens, API keys, connection strings.
- Raw LLM prompts/completions.
- Audio recordings, transcription text, and speech-service request/response bodies.

Retention: audit events are retained for the lifetime of the demo database. There is no
automated retention/deletion job yet; retention rules and GDPR-style erasure are backlog
items (`docs/BACKLOG.md`). Resetting demo data (see `docs/DEPLOYMENT.md`) clears audit
history along with it.

### Operational system-event log (System Logs)

System Logs is a **read-only operational monitor**, not a second audit trail and
not a clinical-record viewer. Its storage and retention are deliberately
separate:

| Concern | Audit events | Operational events |
|---|---|---|
| Table | `admin.audit_event` | `admin.system_event` |
| Purpose | accountability/security | application and dependency monitoring |
| Retention | demo lifetime; no automated deletion | **7 days**, pruned best-effort on operational writes |
| Access | `audit:read` | `logs:read` |
| Examples | login, denial, patient/lab/prescription reads, appointment/staff changes | AI/tool latency and failures, voice outcomes, appointment conflicts/rejections, API 5xx/429s, startup |
| Failure handling | written on the caller's transaction; failure follows the existing request/audit policy and is not silently swallowed | best-effort: a failure logs a server warning and never blocks the clinical request |

Controls:

- **Authorization:** only `clinical_admin` holds `logs:read`. History and stream
  endpoints return 401 anonymous / 403 for Doctor and Nurse. The SSE generator
  re-resolves the session and permission periodically (including on busy
  streams) and sends `auth_expired` before closing after revocation,
  deactivation or permission removal. Navigation hiding is not a control.
- **Data minimization:** operational rows carry only an approved event type,
  category/severity from the registry, an optional actor UUID, opaque resource
  type/reference, request id, outcome and a flat metadata allowlist
  (`language`, durations, model/provider, status, safe error code/tool,
  method/route/HTTP status, counts, reason). Values are bounded and validated
  before persistence. Free-form, nested or unknown keys are dropped.
- **Never recorded:** clinical text or values, patient names/MRNs/DOB, AI
  questions/answers, transcripts, audio, request/response bodies, passwords,
  tokens, cookies, API keys, connection strings, authorization headers,
  filesystem contents, environment variables, or raw exception text/stack
  traces. Unknown audit actions map to a generic `system.audit_event` with a
  bounded action string; the durable audit row itself is unchanged.
- **Audit integrity:** the live bus receives an audit payload only after the
  caller's transaction commits (`after_commit`); rollbacks discard it. The bus
  is not a source of truth; reconnect/replay reads the durable table. Rows are
  never duplicated into `system_event`.
- **No shell:** the page and API expose no command input, shell, SQL console,
  container console or server-control operation. Export is a bounded CSV of the
  currently loaded filtered view; Clear View clears only the browser display
  after an explicit confirmation and never deletes backend records.
- **Bounded resources:** 7-day query window, max 100 history rows, max 500
  offset, max 200 replay events, a 500-event per-subscriber queue (oldest
  dropped), 500-event client buffer, and 15-second heartbeats. SSE connections
  do not hold a database transaction; each replay/auth check uses a short-lived
  session.
- **Operational failure policy:** writing an operational event is best-effort
  and must never turn a successful clinical action into a failure. If the
  operational store is unavailable, the request proceeds and the application
  log records a warning. Audit persistence is deliberately stricter; it remains
  part of the caller's transaction and follows `docs/API.md`.

---

## 5. Secrets handling

- All secrets live in **environment variables** (local `.env`; host dashboard in
  production). `.env` is gitignored; only `.env.example` with placeholders is committed.
- Nothing secret is committed: no keys, no passwords, no database dumps, no real data.
  This is verified before each publish (repo + git history review).
- The LLM key is an OpenCode Console **service-account key** (`oc_sk_…`) restricted to
  **"Models: Request"** permission only, with a **budget cap** set in the Console. It is
  set only in the backend host's environment — it never appears in frontend assets,
  browser requests, or logs.
- **Key rotation:** rotate any leaked or periodically reviewed credential by generating a
  new value in the provider console, updating the single environment variable, restarting
  the service, and revoking the old value. Because secrets are referenced only by env
  var name, rotation touches no code.
- Log redaction: authorization headers, cookies, tokens, and request bodies containing
  passwords are redacted before logging (Phase 11).
- **Cloud IAM is a separate control plane.** Application RBAC does not replace AWS IAM.
  If the project is deployed to AWS/EKS, use per-workload IAM roles, Secrets Manager,
  and GitHub OIDC instead of static keys; see `docs/AWS_EKS_IAM_PLAN.md`. No cloud IAM
  is configured by this repository today.

---

## 6. Explicit non-claims

This system is:

- **NOT HIPAA compliant.** No BAAs exist with any vendor. Real patient data must not be
  entered, stored, or sent to the LLM. Using real data would require a separate security,
  privacy, legal, clinical, and regulatory review plus BAAs with every vendor that
  touches the data.
- **NOT clinically validated** and **NOT a medical device.** No output — human-written or
  AI-generated — is a diagnosis, treatment recommendation, or validated clinical
  decision. AI output is labelled AI-generated.
- **NOT a production clinical system.** It is a publicly reachable synthetic-data demo
  with production-grade *foundations*, not production hardening. Production hardening
  items (MFA, SSO, SCIM, backups/PITR, monitoring/on-call, retention/erasure, pen tests,
  SOC 2, …) are tracked in `docs/BACKLOG.md`.
- **NOT suitable for real patient data or shared-account use as-is.** Public demo
  accounts are shared and provide no per-user accountability beyond audit records.

Public availability is not the same as being compliant or safe for clinical use.

---

## 7. Known limitations

- Single-tenant; no tenant isolation layer (documented decision — see `docs/BACKLOG.md`).
- Simplified demo access policy: role-based, not patient-care-relationship-based.
- No MFA; password compromise is full account compromise.
- No automated audit retention/erasure.
- Free-tier hosting sleeps/autosuspends; availability is best-effort.
- Rate limits (login, `/ai/ask`, voice transcription, appointment interpretation)
  are application-level **in-process** state: per
  instance, reset on restart, not coordinated across instances. A shared store
  (Redis or a database table) is required before scaling out. There is no edge
  WAF/DDoS protection beyond what the hosting providers provide.
- Sessions expire after `SESSION_TTL_HOURS` (default 8 hours) and are then
  rejected with `401`; there is no refresh-token or "log out everywhere" UI
  (deactivating a user revokes all their sessions server-side).
- CSP is strict but hash-based for the single inline page bootstrap: pages may
  not add inline scripts/styles/handlers without breaking, and page edits
  require a backend restart to recompute the hashes. The dev docs UI uses a
  relaxed CSP (never in production). The headers cover the backend-served
  frontend (local/Docker); the production Pages-hosted frontend needs the same
  headers configured at the edge (see §3 "HTTP security headers").
- Audit is read-only via API; it is not tamper-evident (no append-only store or hashing).
- Speech recognition is CPU-only whisper.cpp Base multilingual. Transcription
  accuracy, latency, and memory use have **not** been benchmarked across accents,
  Bulgarian/English clinical vocabulary, or the target host; treat transcripts
  as editable drafts. No GPU path, no Vosk fallback, and no cloud speech API.
- The browser microphone/`MediaRecorder` path is feature-detected and has a
  manual text fallback, but real-browser permission prompts and codec behaviour
  are not verified by the automated suite (it has no browser).
- `WHISPER_AUTH_MODE=gcp_iam` metadata-token fetching and Cloud Run
  service-to-service IAM are implemented/documented but **not verified** here;
  the current deployment target is Render + Cloudflare + Neon.
- Voice rate limiting is in-process like the other limiters; voice temp files
  live under the system temp directory and are removed per request, but an
  abrupt process kill can leave files for the OS to reap.

## 8. Vulnerability disclosure

To report a security issue: **placeholder — the lead will add a contact channel (issue
tracker with a private reporting option or a security email) before public deployment.**
Until then, do not file public issues containing exploit details. We aim to acknowledge
reports within a few working days; this demo has no formal SLA.

## 9. Related documents

- `docs/API.md` — frozen API contract, error envelope, permission matrix (source of truth).
- `docs/ARCHITECTURE.md` — trust boundaries and data flows.
- `docs/AI_GUARDRAILS.md` — section-by-section assessment against
  `patient_data_ai_agent_security_guardrails.md`, including partial controls and
  open gaps; not a compliance certification.
- `docs/BACKLOG.md` — deferred production security/compliance work.
- `docs/AWS_EKS_IAM_PLAN.md` — Plan B AWS EKS/IAM deployment blueprint (not implemented).
- `docs/TROUBLESHOOTING.md` — how to gather diagnostics without leaking secrets.
