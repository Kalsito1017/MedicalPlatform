# Production Backlog

**Scope:** the enterprise/production checklist in `production-requirements.txt`, split honestly into what this build already covers, what is deferred (with what it would take), and what is explicitly out of scope for the demo.
**Status:** living document, updated as phases land. Written at Phase 1; "already covered" items map to plan phases and are marked with the phase that lands them. Nothing here is a promise — it is an honest inventory.

**Baseline assumption:** `production-requirements.txt` describes an *internal production
tool with real clinical data*. This build is a **public synthetic-data demo with
production-grade foundations**. Everything involving real patient data, HIPAA, or
clinical use is gated on a separate security/privacy/legal/regulatory review.

---

## A. Already covered by this build

Mapped to the phase/controls that implement each item.

| Requirement (from production-requirements.txt) | How it is covered | Lands in |
|---|---|---|
| Schema designed and reviewed; aligned with admin/patients/laboratory/medications | Four-schema design in `docs/ARCHITECTURE.md`, SQLAlchemy models + Alembic | Phase 3 |
| Foreign keys, constraints, indexes, timestamps on all tables | Alembic migrations define them; tests verify FK behavior | Phase 3, 12 |
| Soft deletes for patient/encounter/lab/prescription/staff | Soft-delete columns (deleted_at) instead of row removal | Phase 3 |
| UTC timestamps in DB, explicit conversion in application layer | UTC everywhere; ISO-8601 with timezone on the wire (`docs/API.md`) | Phase 3–4 |
| Unicode names (apostrophes, accents, emoji, RTL) | UTF-8 throughout; ORM parameterized access; seeded synthetic names exercised in tests | Phase 3, 6, 12 |
| Dosage/clinical values as precise decimals, never floats | `Numeric`/decimal columns for lab values and doses | Phase 3 |
| Migrations that run forward and roll back | Alembic (up/down revisions) | Phase 3 |
| Idempotent/resettable synthetic seed with provenance labeling | `scripts/seed_demo_data.py`; `provenance: "synthetic"` in every clinical payload | Phase 3, 6 |
| Login handles `+` and valid characters in emails | Typed email validation accepting standard addresses; tests | Phase 5, 12 |
| Rate limits and progressive lockouts on login | Login throttling, `429 rate_limited` + `Retry-After`, uniform errors | Phase 5 |
| Secure HttpOnly cookies + SameSite + CSRF | `cap_session` HttpOnly/Secure/SameSite=Lax + double-submit CSRF | Phase 5 |
| No long-lived sensitive tokens in browser storage | Server-side sessions only; no JWT/localStorage | Phase 5 |
| Sessions time out | `SESSION_TTL_HOURS` expiry enforced server-side | Phase 5 |
| Four roles exactly as in the plan | doctor, nurse, clinical_admin, tech_admin | Phase 5 |
| Explicit permission matrix | `docs/API.md` + `backend/app/authorization/policy.py`, with tests | Phase 5, 12 |
| Least privilege, deny-by-default on every endpoint and MCP tool | Single policy module consulted by REST + MCP | Phase 5, 7 |
| Server-side authorization on every protected action | All checks in backend; UI hiding is cosmetic | Phase 5–10 |
| IDOR protection (changing an ID in the URL) | Per-request authz + `404` concealment + tests | Phase 6, 12 |
| No self-role escalation; role changes authorized, validated, audited | Self-target → 403; `role:assign` + CSRF + audit events | Phase 5, 10 |
| Tech admins get no automatic clinical access | Matrix: `tech_admin` has zero clinical permissions | Phase 5 |
| MCP tools narrow, typed, read-only, allowlisted | Exactly 4 tools — `search_authorized_patients`, `get_patient_summary`, `get_patient_lab_results`, `get_recent_prescriptions`; no execute_sql/shell/filesystem, ever | Phase 7 |
| Acting identity from verified auth context, never LLM argument | In-process session context; identity never a tool argument | Phase 7 |
| Same authorization layer re-executed inside every MCP tool | Policy module re-checked per tool call | Phase 7 |
| Query/result/time limits and safe errors on tools | Bounded args, capped results, timeouts, no stack traces | Phase 7 |
| System instructions force tool use for record facts; distinguish retrieved vs general | System prompt + response structure (`used_tools`, `citations`) | Phase 8 |
| Bounds on prompt size, tool calls, request duration | `LLM_MAX_TOOL_CALLS`, `LLM_REQUEST_TIMEOUT_SECONDS`, size caps | Phase 8 |
| Protection against prompt injection | Untrusted-data treatment of prompts, tool allowlist, per-call authz (see `docs/SECURITY.md`) | Phase 8, 12 |
| Model spend caps | OpenCode Console budget cap on the service-account key | Phase 8, 14 |
| Human approval before irreversible AI actions | Not needed in this build: the AI toolset is strictly read-only and cannot trigger any state change | Phase 7–8 |
| Clear labeling that AI output is not a diagnosis | `ai_generated: true` + disclaimer in every response; UI notice | Phase 8, 9 |
| Audit events (login, logout, denials, record reads, MCP calls, staff/role changes) | `admin.audit_event` + `GET /api/v1/audit` | Phase 11 |
| Audit records show who *viewed* a record | Patient-record reads are audited, not only writes | Phase 11 |
| Structured logs, request/correlation IDs, redaction | `X-Request-Id` on every response; redacted structured logs | Phase 4, 11 |
| Server-side validation on all inputs | Pydantic typed schemas; bounded pagination/filters | Phase 4 |
| Parameterized queries / ORM only | SQLAlchemy only; no string-concatenated SQL | Phase 3+ |
| SQLi/XSS/CSRF protections | See threat table in `docs/SECURITY.md` | 5, 9, 11 |
| Restrictive CORS (known origins only) | Single frontend origin in `ALLOWED_ORIGINS` | Phase 4, 14 |
| Security headers; TLS in transit | HTTPS at hosting edge; response headers (Phase 11); TLS to Neon and LLM | Phase 11, 14 |
| Least-privilege DB account; no superuser credentials anywhere | Separate app vs migration roles (`DATABASE_URL` / `MIGRATION_DATABASE_URL`) | Phase 3, 14 |
| Secrets only in env vars / secret manager | Env vars in host dashboards; `.env` gitignored; key rotation documented | Phase 2, 11, 14 |
| Dependency scanning | CI dependency/vulnerability check where feasible | Phase 12 |
| Rate limits on costly AI endpoints | `POST /api/v1/ai/ask` rate limited + bounded | Phase 8, 11 |
| Health checks (`/health/live`, `/health/ready`) | Implemented; readiness checks DB, LLM optional | Phase 4 |
| Unit/API/data/MCP/LLM-mocked/escalation/audit tests | Test suite per Phase 12 categories; CI runs them | Phase 12 |
| Frontend tests (login state, role nav, patient list/detail, errors) | Frontend checks in CI | Phase 12 |
| Test data realistic but never real | Synthetic seed and fixtures only | Phase 3, 12 |
| Responsive layouts; loading/empty/error states; input-preserving forms; destructive-action confirmation | Frontend implementation | Phase 9 |
| No clinical data in local storage, analytics, browser console | Frontend rules; no third-party analytics | Phase 9 |
| Keyboard navigation and accessible markup for primary workflows | Built in (formal audit deferred — see B) | Phase 9 |
| Staff list/create/activate/roles; audited; CSRF-protected; last-admin protection; backend enforces all | Admin panel | Phase 10 |
| Explicit statement: not clinically validated, not a medical device | README + `docs/SECURITY.md` non-claims + footer/About-dialog disclosure + provenance badges (header banner removed at owner request, 2026-10-26) | Done (docs + UI) |
| Architecture decision records | Decision log in `docs/ARCHITECTURE.md` | Done |
| README that gets a new engineer running in under an hour | `README.md` | Done (commands verified in Phase 13: Docker build + E2E smoke) |
| Documented data-retention and reset procedures | Demo-data reset in `docs/DEPLOYMENT.md` §9; retention deferred (see B) | Done (reset, Phase 13) |
| Explicit non-goals | `docs/SECURITY.md` §6, `PLAN.md` | Done |

---

## B. Deferred (tracked, not built)

Each item: why it matters, rough effort, prerequisite, what it takes.

| # | Item | Why it matters | Effort | Prerequisite | What it takes |
|---|---|---|---|---|---|
| B1 | **MFA (TOTP) + recovery path** | Password compromise is currently full account compromise | M | Auth stack (Phase 5) | TOTP enrollment/verify endpoints, backup codes with documented recovery, login flow changes, tests |
| B2 | **Password reset (single-use, expiring links)** | Lost password currently means admin re-creates the account | S | Email delivery channel or admin-issued token flow | Token table with expiry/single-use semantics, email or out-of-band delivery, rate limits, tests |
| B3 | **SSO / SAML** | Hospital/enterprise IT expects IdP login | L | Real identity provider, org decision | SAML/OIDC integration, just-in-time user provisioning, mapping IdP groups to the four roles |
| B4 | **SCIM (or equivalent)** | Offboarding must remove access immediately across systems | L | SSO (B3), directory provider | SCIM 2.0 endpoints for user lifecycle, reconciliation jobs, audit of external changes |
| B5 | **Session revocation UI ("revoke all sessions")** | Admins cannot currently force-logout a compromised account everywhere (server-side revocation exists; UI does not) | S | Admin panel (Phase 10) | Revoke-all-sessions endpoint + admin UI + audit event; already-supported server-side revocation plumbing |
| B6 | **Impersonation ("log in as user")** with full audit | Support tooling convenience | M | Explicit design review; must NOT exist in the demo (no break-glass) | Explicit opt-in action, prominent UI banner, complete audit trail, time-bounded sessions, policy review |
| B7 | **Multi-tenancy / tenant isolation** | Needed if more than one organization ever uses it | L | Org decision | Tenant key on every table + enforced scoping in policy layer, or separate databases per tenant; extensive isolation tests. (Until then: strictly single-tenant — see C.) |
| B8 | **Patient-care-relationship / assignment policy** | Role-based access to all patients is acceptable only for synthetic demo data | M | Real-data review | Care-team/assignment tables, policy rules in `policy.py`, UI surfacing, tests; **hard prerequisite for any real patient data** |
| B9 | **Retention, erasure, GDPR-style requests** | Audit and clinical rows currently live until the DB is reset | M | Data classification decision | Retention schedules, deletion jobs that respect audit obligations, erasure request workflow, documented procedure |
| B10 | **Backups + point-in-time recovery + tested restores** | Free tier has minimal persistence guarantees; a mistake can destroy the demo DB | S–M | Paid DB tier or accepted provider limits | Enable provider PITR/backup, scheduled export, documented + periodically tested restore procedure |
| B11 | **Disaster recovery plan (incl. second region if required)** | Single region, single provider, no DR story | L | Uptime requirement decision | RTO/RPO targets, DR runbook, periodic drills; second region only if contract demands |
| B12 | **Monitoring, alerting, on-call, runbooks, postmortems** | Failures are currently discovered by users | M | Ops owner, alerting channel | Uptime checks from outside, error tracking, dashboards, alert routing to a human, runbooks per failure mode |
| B13 | **Status page** | Honest availability communication for a public demo | S | B12 monitoring | Static or hosted status page fed by health checks |
| B14 | **Feature flags** | Only one coarse switch exists today (`LLM_ENABLED`) | S | — | Flag service or config-driven flags, per-flag tests, cleanup discipline |
| B15 | **Infrastructure as code** | Provider config is click-ops in dashboards today | M | Provider choice stability | Terraform/Pulumi for Pages/Render/Neon config, secret handling in a manager, drift checks |
| B16 | **Zero-downtime deploys + one-click rollback** | Render free tier restarts with downtime; rollback is manual redeploy of a previous commit | M | Paid hosting tier, CI/CD maturity | Rolling/blue-green deploy strategy, migration compatibility policy (expand/contract), automated rollback |
| B17 | **Load testing** | Unknown behavior under concurrent users | S | Representative environment (staging) | k6/locust scenarios for login, patient search, AI endpoint; establish baselines and limits |
| B18 | **Penetration test + security questionnaire evidence** | External validation of the threat model | M | Stable deploy, scope definition | Engage testers, remediate findings, maintain evidence pack |
| B19 | **SOC 2 (policies + evidence)** | Required by many enterprise buyers | L | Organization-wide program | Policies, controls evidence, auditor; organizational effort far beyond this codebase |
| B20 | **HIPAA controls + BAAs with every vendor** | Required the moment any real patient record exists | L | Legal/compliance program | BAAs (hosting, DB, LLM provider), access controls per HIPAA, audit controls, risk assessment, training; **out of reach of this demo** |
| B21 | **Data residency controls** | Contracts/regulations may pin data to a region | M | Contract/regulatory requirements | Region-pinned hosting tiers, LLM provider data-policy review, documentation |
| B22 | **Accessibility audit (WCAG, screen readers, contrast)** | Required for many healthcare contracts; built-in markup is a start, not a validation | M | Stable UI (Phase 9) | Manual + automated audit, screen-reader testing, remediation, re-test |
| B23 | **Cost alerts** | Pay-per-use LLM and future paid tiers can surprise | S | Provider billing setup | Billing alerts/thresholds on Render/Neon/OpenCode Console; periodic cost review |
| B24 | **Connection pooling tuning, caching, background job queues** | Real data volumes and async work will need them | M | Real workload signals | PgBouncer/pooler config, cache invalidation design, queue with retries/dead-letter |
| B25 | **Changelog, code-review/branching policy, dependency upgrade cadence, bus-factor measures** | Process maturity for a real team | S–M | Team process | CHANGELOG.md, branch protection, scheduled upgrades, shared ownership of accounts/domains |
| B26 | **Privacy policy and terms matching actual data handling** | Expected of any public site | S | Final feature set | Draft + legal review; simple demo notice exists today |
| B27 | **Regular access reviews** | Least privilege decays over time without review | S | Real user population | Quarterly review of `user_role` assignments and active accounts, documented sign-off |
| B28 | **Stolen-laptop / compromised-credential response plan** | Password or device compromise currently has no playbook | S | B5 (session revocation UI), ops owner | Documented playbook: revoke sessions, rotate keys (procedure in `docs/SECURITY.md` §5), force password change, audit review |

---

## C. Explicitly out of scope for this demo

| Item | Reason |
|---|---|
| **Real patient data of any kind** | Requires separate security/privacy/legal/clinical/regulatory review and BAAs. The demo is synthetic-only by decision. |
| **HIPAA compliance claims, BAAs** | Cannot be achieved by a free-tier demo; tracked as B20 for a real deployment. |
| **Clinical decision-making / medical device functionality** | Not clinically validated; no regulatory path is being pursued. AI output is informational and labelled as such. |
| **Multi-tenancy (for now)** | Single-tenant by documented decision (production-requirements line 38–39 permits documenting a single-tenant decision); B7 tracks the work if that changes. |
| **PCI scope (card numbers, payments)** | Deliberately excluded — no payment data ever enters the system. Nothing to build; keep it that way. |
| **File uploads** | The application has no file-upload feature by design; the production checklist's upload-scanning requirement is therefore moot. If uploads are ever added, the scanning/size-limit/storage controls become a prerequisite. |
| **Encryption at rest beyond provider defaults** | Neon and the hosting providers encrypt data at rest by default; application-level envelope encryption is not needed for synthetic demo data (see B10/B21 for real-data work). |
| **Impersonation / break-glass access** | PLAN.md §4 forbids break-glass in the demo; impersonation exists only as a reviewed future option (B6). |
| **4-digit PIN or other single weak factor** | Explicitly rejected by the plan as an authentication method. |
| **Executing tools for the LLM beyond the 4 read-only tools** (`execute_sql`, shell, filesystem) | Permanent architectural constraint, not a backlog item. Will not be added. |
| **Shared demo accounts for real users** | Demo accounts are for demonstration only and are documented as shared/insecure-by-design. Production requires unique identities per user. |
| **SSO/SCIM/MFA without an owning organization** | These require enterprise infrastructure and owners; listed as deferred (B1–B4) but effectively out of reach until the project has an organization behind it. |

---

## Notes on the source checklist

`production-requirements.txt` frames the system as an internal live-production tool.
Items that only make sense in that frame (real clinical data flows, staging/prod parity,
on-call rotations) are deferred rather than silently dropped. The "explicit non-goals"
from the end of that file are carried into `docs/SECURITY.md` §6 verbatim in spirit and
remain binding.
