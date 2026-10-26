# Security

**Scope:** role/permission model, authentication design, threat model, audit design, secrets handling, and explicit non-claims for the Clinical AI Platform.
**Status:** living document, updated as phases land. Written at Phase 1; controls described here are implemented across Phases 5, 7, 8, and 11 and are marked accordingly where not yet built. The permission matrix below is reproduced verbatim from `docs/API.md` (frozen contract); the enforced source of truth is `backend/app/authorization/policy.py`.

---

## 1. Roles and permissions

### Role intent (owner decision, 2026-10-26: three roles; `tech_admin` removed)

- **Doctor:** View patient records and clinical data permitted by the configured demo policy; use clinical summary and query workflows.
- **Nurse:** Least privilege — view patient information and clinical data permitted by the configured demo policy. No staff-role administration, no audit access, no admin panel.
- **Admin** (`clinical_admin`): Full access — clinical data, staff accounts, roles, audit log, and analytics. Administration is exercised through the same server-side permission checks as everything else.

Roles do **not** automatically imply that a user may view every patient. The demo uses a
simplified synthetic-data access policy (documented here and in `docs/API.md`); a
patient-care-relationship/assignment policy is required before any real data — tracked
in `docs/BACKLOG.md`.

### Permission matrix (identical to `docs/API.md`)

| Permission | doctor | nurse | clinical_admin (Admin) |
|---|---|---|---|
| `patient:read` | ✔ | ✔ | ✔ |
| `lab:read` | ✔ | ✔ | ✔ |
| `prescription:read` | ✔ | ✔ | ✔ |
| `staff:read` | ✖ | ✖ | ✔ |
| `staff:manage` | ✖ | ✖ | ✔ |
| `role:assign` | ✖ | ✖ | ✔ |
| `audit:read` | ✖ | ✖ | ✔ |
| `analytics:read` | ✖ | ✖ | ✔ |

Deny by default. `nurse` is least-privilege clinical read; `clinical_admin` (Admin) holds
every permission. `analytics:read` is declared in advance of the analytics page (a later
wave); no endpoint requires it yet.

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
| **Prompt injection** | A synthetic note or patient field contains "ignore instructions / call tool X" | Prompt text is treated as untrusted data, never as instructions; server-side tool **allowlist** (only `search_authorized_patients`, `get_patient_summary`, `get_patient_lab_results`, `get_recent_prescriptions`, `find_patients_by_lab`, `find_patients_by_medication` — all read-only); identity from session, never from tool arguments; authorization re-executed inside every tool; bounds on tool count (`LLM_MAX_TOOL_CALLS`), result size, and request duration; model output cannot trigger any privileged or state-changing action | Phases 7–8, tests Phase 12 |
| **Secret leakage** | API key, DB URL, or session token appears in logs, repo, or browser | Secrets only in env vars; `.env` gitignored; log redaction of auth headers, cookies, tokens, passwords; generic error envelope to clients (never stack traces/SQL); no secrets in frontend assets; repo/history review before publishing | Phase 11 + `.gitignore` |
| **DoS on the AI endpoint** | Repeated expensive LLM calls exhaust quota/money | Rate limit on `POST /api/v1/ai/ask`; question and prompt size bounds; `LLM_MAX_TOOL_CALLS` and `LLM_REQUEST_TIMEOUT_SECONDS`; Console **budget cap** on the service-account key; `LLM_ENABLED=false` kill switch; endpoint degrades to `503` without affecting the rest of the app | Phases 8, 11 |
| **Credential stuffing / brute force** | Automated password guessing | Login rate limiting + progressive lockout; uniform errors; argon2id cost slows offline cracking if hashes ever leak | Phase 5 |
| **Session theft (XSS → session)** | Script steals the session cookie | `HttpOnly` cookies; XSS controls above; server-side revocation on logout | Phase 5, 9 |

### HTTP security headers

Set on **every** response — API, static pages, and error envelopes — by
`SecurityHeadersMiddleware` (`backend/app/main.py`). Verified by
`backend/tests/test_security_headers.py` and `scripts/e2e_smoke.sh`.

| Header | Value | Why |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | no MIME sniffing of API or asset responses |
| `X-Frame-Options` | `DENY` | the UI is never framed (mirrors CSP `frame-ancestors 'none'`) |
| `Referrer-Policy` | `no-referrer` | the strictest option; the app needs no referrer and patient identifiers live in page URLs, so nothing about navigation may leak cross-origin |
| `Permissions-Policy` | camera, microphone, geolocation, payment, sensors, USB, screen capture, … denied (`()`) | the demo uses none of these browser features |
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

**Production caveat:** when Cloudflare Pages serves the frontend and proxies
`/api/*` to the backend (Phase 14/15), page responses are served by Pages and do
**not** pass through this middleware. The equivalent headers must then be set at
the edge (Cloudflare `_headers` file / Transform Rules) or by proxying the whole
site to the backend; API responses already carry the headers above. This must be
verified before the public launch checklist is signed off.

---

## 4. Audit design

Recorded in `admin.audit_event` (readable via `GET /api/v1/audit` with `audit:read`):

| Event | Recorded fields |
|---|---|
| Login success / login failure | actor (or attempted email reference), outcome, timestamp, request id |
| Logout | actor, timestamp, request id |
| Permission denial (any `403`) | actor, attempted action, outcome, timestamp, request id |
| Patient-record reads (patient, summary, labs, prescriptions, encounter) | actor, action, resource type + resource id, outcome, timestamp, request id |
| MCP tool calls | tool name, actor, target resource reference, outcome, request id |
| Staff / role changes (create, patch, activate/deactivate, role add/remove) | actor, target user, change summary, outcome, request id |

Deliberately **NOT** recorded:

- Full clinical payloads (no note text, lab values, or record bodies in audit rows).
- Passwords, password hashes, session tokens, CSRF tokens, API keys, connection strings.
- Raw LLM prompts/completions.

Retention: audit events are retained for the lifetime of the demo database. There is no
automated retention/deletion job yet; retention rules and GDPR-style erasure are backlog
items (`docs/BACKLOG.md`). Resetting demo data (see `docs/DEPLOYMENT.md`) clears audit
history along with it.

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

---

## 6. Explicit non-claims

This system is:

- **NOT HIPAA compliant.** No BAAs exist with any vendor. Real patient data must not be
  entered, stored, or sent to the LLM. Using real data would require a separate security,
  privacy, legal, clinical, and regulatory review plus BAAs with every vendor that
  touches the data.
- **NOT clinically validated** and **NOT a medical device.** No output — human-written or
  AI-generated — is a diagnosis, treatment recommendation, or validated clinical
  decision. AI output is labelled as AI-generated and informational.
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
- Rate limits (login, `/ai/ask`) are application-level **in-process** state: per
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

## 8. Vulnerability disclosure

To report a security issue: **placeholder — the lead will add a contact channel (issue
tracker with a private reporting option or a security email) before public deployment.**
Until then, do not file public issues containing exploit details. We aim to acknowledge
reports within a few working days; this demo has no formal SLA.

## 9. Related documents

- `docs/API.md` — frozen API contract, error envelope, permission matrix (source of truth).
- `docs/ARCHITECTURE.md` — trust boundaries and data flows.
- `docs/BACKLOG.md` — deferred production security/compliance work.
- `docs/TROUBLESHOOTING.md` — how to gather diagnostics without leaking secrets.
