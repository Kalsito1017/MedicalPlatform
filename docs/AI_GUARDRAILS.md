# Focused AI guardrails and patient-data baseline assessment

**Review date:** 2026-11-09
**Baseline reviewed:** `patient_data_ai_agent_security_guardrails.md`
**Scope:** the current repository: FastAPI backend, PostgreSQL schema, frontend,
internal MCP server, optional LLM, optional whisper.cpp speech service,
appointment scheduling, and the deployment/test configuration.
**Status:** engineering assessment for a **synthetic-data demo**. This is not a
compliance certification, legal opinion, clinical validation, or proof that the
system is safe for real patient data.

Legend: **Yes** = implemented in trusted code and covered by tests/docs where
applicable; **Partial** = some controls exist but a material part is missing or
demo-scoped; **Gap** = not implemented; **N/A** = no such feature/data flow.

## 1. Core safety principles

| Principle | Status | Evidence / limitation |
|---|---|---|
| Deny by default | **Yes** | `backend/app/authorization/policy.py`; unknown MCP tools and invalid contexts are denied in `backend/app/mcp/server.py`; every REST route declares a permission and the matrix guard fails on an undeclared route (`backend/tests/test_permissions_api.py`). |
| Minimum necessary access | **Partial** | AI tool payloads are minimized before prompt insertion (`backend/app/llm/guardrails.py`), and AI tools are bounded. General clinical REST endpoints intentionally return full authorized records; there is no per-purpose field minimization. |
| Purpose limitation | **Gap** | No purpose, consent, or legal-basis field/flow exists. The demo policy grants `patient:read` access to all synthetic patients. |
| No direct model-to-database access | **Yes** | The LLM can only request the seven in-process read-only MCP tools; it has no DB credentials or transport (`backend/app/mcp/`, `backend/app/llm/loop.py`). |
| Trusted enforcement | **Yes** | Authorization, patient-access checks, query bounds, and writes are enforced in backend services; the frontend only reflects permissions. |
| Separate identities | **Partial** | The human identity comes only from the server-side session. The model has no identity of its own and receives a server-injected `ToolContext`; there is no separately authenticated agent/service identity or separate AI database role. |
| Fail closed | **Yes, with gaps** | Missing sessions deny; MCP/LLM/voice dependency failures map to controlled 403/503. Some audit paths are deliberately best-effort (for example denial records), so an audit-sink failure there does not block the original response; audit tamper-resistance is absent. |
| No silent privilege escalation | **Yes** | All MCP tools are read-only; self role assignment is blocked; the LLM cannot write; appointment creation requires an explicit doctor-confirmed REST call. |
| Privacy by design | **Yes for demo scope** | The seed is synthetic-only; provenance stays database-internal; no real data is present. |
| Human oversight | **Partial** | AI answers are labelled AI-generated and informational; appointment AI only proposes and requires explicit confirmation. There is no clinician-approval workflow for AI answer content and no monitoring for automation bias. |

## 2. Data classification and inventory

**Partial.** The repo documents the synthetic cohort, the clinical/audit tables,
and the new scheduling/voice data flows (`docs/ARCHITECTURE.md`,
`docs/SPEECH_RECOGNITION.md`, `docs/API.md`). There is **no formal data
inventory** covering every category, lawful/authorized purpose, all copies,
vendors, regions, retention, deletion, backups, encryption, or external-model
exposure. Voice recordings, transcripts, AI conversation history, appointment
details, and print exports are patient-linked categories that would need to be
in that inventory before real-data use.

## 3. Identity, authentication, authorization

| Requirement | Status | Evidence / limitation |
|---|---|---|
| Strong authentication, MFA for workforce/privileged accounts | **Partial / Gap** | Argon2id passwords, server-side sessions, CSRF, login rate limiting. **No MFA and no step-up authentication.** |
| Short-lived/scoped credentials | **Partial** | Session TTL is configurable (default 8 h); the LLM and speech service use server-side secrets. No per-service short-lived agent credential or workload identity in the demo. |
| Server-side token/session validation | **Yes** | Session lookup; `Principal` injected server-side; frontend-supplied identity is never trusted. |
| CSRF / session fixation / replay | **Yes / Partial** | CSRF double-submit is enforced; login creates a new server-side session. No explicit session-fixation regression test or token-binding beyond the HttpOnly cookie. |
| Authorize every object and operation | **Partial** | Every REST route and MCP tool re-authorizes. Patient access uses a single demo seam; there is **no care-relationship, consent, purpose, organization, or tenant check**. |
| Tenant isolation | **N/A / Gap** | The demo is explicitly single-tenant. No tenant key, RLS, or tenant-isolation tests exist. |
| Row-level security | **Gap** | No PostgreSQL RLS; authorization is in the application layer only. |
| Re-check authorization in background jobs | **N/A** | No queued/background patient-data jobs; the seed is an administrative script. |
| Break-glass | **N/A** | Deliberately not implemented; no hidden bypass exists. |
| Explicit permission matrix | **Partial** | 11 permissions with doctor/nurse/admin tests. There is **no separate export/print/share permission**; any `patient:read` user can use the print workflow. |
| Default deny | **Yes** | Unspecified role/permission combinations are denied and tested. |

## 4. Database and data-layer protections

| Requirement | Status | Evidence / limitation |
|---|---|---|
| Dedicated non-human DB identity per service | **Partial / Gap** | The app uses one application DB role; the AI does not have its own read-only role. Docker Compose's `POSTGRES_USER=app` is a development convenience and is not a production least-privilege identity. |
| AI read-only views/stored procedures | **Gap** | The MCP server executes through the same service/database role as the REST API. It is read-only **by tool design**, not by a separate DB grant. |
| Parameterized queries / no arbitrary SQL | **Yes** | SQLAlchemy ORM/parameterized queries only; no `execute_sql`/shell/filesystem tools exist. |
| Row/tenant filters in the DB | **Gap** | No RLS/tenant filters. |
| Separate read/write paths | **Partial** | AI tools are read-only; the REST API and MCP server share the same DB role and service layer. |
| Bulk-extraction limits | **Partial** | Pagination limits, AI tool result caps, and UI pagination exist. There is no dedicated export-volume policy or approval flow. |
| Transactions/integrity constraints | **Yes** | FKs, check constraints, advisory locks for appointment writes, and transactional audit writes. |
| Private DB networking/TLS | **Partial** | Production topology documents private provider networking/TLS; local compose publishes PostgreSQL on `localhost:5432` for development. |
| No credentials to model/browser/logs | **Yes** | Secrets are env-only/redacted; no DB DSN or LLM key is sent to the browser or model. |

## 5. LLM and agent-specific controls

| Requirement | Status | Evidence / limitation |
|---|---|---|
| Model outside trust boundary | **Yes** | Prompt text is untrusted; tools are server-reauthorized; no model-selected identity or SQL. |
| Minimum necessary data to model | **Yes / Partial** | Tool results are minimized, but general-lookup mode can intentionally span multiple authorized patients. |
| Approved provider/terms/training/residency | **Gap** | Not verified. The repo documents the optional provider but does not establish contractual data-processing, training, retention, subprocessor, residency, or incident terms. |
| No patient data in static prompts/examples | **Yes** | Prompts contain rules and schema text, not patient data. |
| Narrow typed tools / argument validation | **Yes** | Pydantic schemas, typed MCP tools, bounded limits, fixed allowlist. |
| Backend re-authorizes every tool call | **Yes** | `ToolContext` identity and per-tool checks; tests cover tampered patient/scope arguments. |
| No shell/DB/browser/HTTP tools | **Yes** | None exist and the architecture forbids adding them. |
| Outbound SSRF/exfiltration restrictions | **Partial** | No arbitrary-fetch tool exists; the speech base URL is config-validated. There are no network-egress allowlists/firewall controls in the app. |
| Timeouts/quotas/result-size limits | **Yes** | LLM loop budget, tool limits, AI/voice rate limits, body limits. |
| Treat tool output as untrusted | **Yes** | Record data is labelled DATA; output guardrails inspect model answers; appointment proposals are structure-validated. |
| Prompt-injection handling | **Yes** | Untrusted-input labelling, deterministic input checks, tool-scope binding; tests in `backend/tests/test_llm_guardrails.py`. |
| Cross-patient leakage prevention | **Partial** | Patient-scoped mode binds the exact patient; general mode is authorized. AI conversation history is per-user, but its stored patient facts are **not re-authorized against current patient access when read** (demo policy grants all patients, so this is latent). |
| Ambiguity → stop/clarify | **Yes** | Patient-scoped identifiers are exact; appointment interpreter returns candidates/missing fields instead of guessing. |
| Output validation / no invented facts | **Yes / Partial** | Deterministic output guardrails, citations, prompt rules, and structured appointment proposals. This is not clinical validation and cannot prove correctness. |
| Distinguish facts vs interpretation | **Partial** | Answers are labelled AI-generated and cite records; appointment proposals are labelled AI-generated. There is no formal clinician approval state for answers. |

## 6. Clinical safety and human oversight

**Partial.** The product is documented as an informational clinical-records demo,
not a diagnostic/prescribing device. AI tools are read-only; AI answers are
labelled; appointment AI cannot write and requires an explicit confirmed
`POST /appointments`. There is **no validated intended-use/clinical-risk file,
no clinician review/approval workflow for AI content, no subgroup/clinical
performance validation, and no automation-bias monitoring**. Emergency
escalation is documented as absent/out of scope rather than implemented.

## 7. Encryption, secrets, infrastructure

| Requirement | Status | Evidence / limitation |
|---|---|---|
| TLS in transit | **Partial** | Provider TLS is documented; local compose is HTTP on localhost. |
| Encryption at rest | **Partial** | Relies on provider defaults; no application-level encryption/HSM or independent verification in this repo. |
| Key/credential rotation and revocation | **Partial** | Documented procedure and server-side session revocation; no automated rotation/compromise exercise. |
| Secrets manager | **Partial** | Environment variables + `.gitignore` + redaction + secret scan; not a managed secrets service. |
| IAM/container least privilege | **Partial** | Backend/speech containers run non-root; DB role separation is missing. |
| Environment separation | **Partial** | Local/Docker/test separation exists; no production/staging environment. |
| Host/container hardening/image scan/SBOM | **Partial / Gap** | Non-root images, pinned deps, dependency audit, secret scan; no image scanner, SBOM, or IaC scan in this repo. |
| Private networking/segmentation | **Partial** | Speech is private to the compose network; DB is published on localhost for development; production topology is documentation only. |
| Backups/restore tests | **Gap** | Provider backups are documented as a requirement, not implemented/tested here. |
| Admin/DB/debug endpoints not public | **Partial** | Docs UI is disabled in production; DB is exposed to localhost for dev; no production verification. |

## 8. Files, images, uploads, exports

**Partial / Gap.** The only upload is the bounded voice transcription route.

- **Yes:** authenticated `appointment:write`, exact-route body cap (4 MiB default /
  8 MiB hard ceiling), media-type check, FFprobe/FFmpeg validation, duration
  bound, private temp directory (`0700`/`0600`), per-request cleanup, no public
  URLs, no filename-based paths, no audio/transcript persistence or logging.
- **Partial:** the converted WAV is validated with Python `wave`; the transcript
  is treated as untrusted text and only sent to the LLM after a separate user
  action.
- **Gap:** no malware/AV scan for uploaded audio, no file-signature allowlist
  beyond FFprobe, no upload quarantine, and no separate export/print permission
  or export audit. The print workflow fetches complete records under
  `patient:read` and is not recorded as a distinct export event.

## 9. RAG, embeddings, caches, memory

**Partial / N/A.** There is no RAG, vector database, embedding store, or shared
server-side cache. Frontend SWR cache is in-memory per tab. AI conversation
history (`admin.ai_conversation`) is a persistent, per-user store of questions,
answers, patient references, tools, and citations; it is not fed back to the
model as cross-conversation context, and users can delete it (`ai.history.*`).
**Gaps:** no automatic retention, no re-authorization of stored patient facts
when access changes, and no documented deletion propagation for history/backups.

## 10. Logging, monitoring, audit

| Requirement | Status | Evidence / limitation |
|---|---|---|
| Audit authenticated actor + minimized resource + outcome + request id | **Partial** | `admin.audit_event` records actor, action, resource ref, outcome, request id, IP, timestamp. No purpose/policy-version/source-system fields. |
| Audit patient-data access | **Partial** | Patient/summary/labs/prescriptions reads, MCP calls, AI asks, appointment mutations/denials, and analytics reads are audited. **Not audited:** successful `GET /ai/conversations` history reads, successful appointment list/detail reads, voice transcription, and print/export as a distinct event. |
| Human approval audit | **Partial** | Appointment create/update/cancel are audited; AI answer approval does not exist. |
| No full prompts/records/secrets in logs | **Yes** | Structured path-only request logging; audit metadata only; log-redaction integration tests; voice transcript was absent from app/speech logs in a live smoke. |
| Restricted log access/retention | **Partial** | Audit endpoint requires `audit:read`; retention is demo-lifetime and documented as a backlog item. |
| Monitoring/alerts | **Gap** | No anomaly detection, alerting, SIEM, or on-call integration. |
| Tamper-resistant audit | **Gap** | Audit rows live in a normal PostgreSQL table; no append-only/hash-chain/WORM storage. The app DB role can modify them. |

## 11. Retention, deletion, consent, data rights

**Gap for real data.** AI history has user-initiated deletion; there is no
automatic retention policy, no patient-data deletion/correction propagation
across derivatives/backups, no consent/authorization tracking or withdrawal
flow, and no access/portability workflow. These are tracked in
`docs/BACKLOG.md` (B9 and related items).

## 12. Secure development and change management

**Partial.** Threat-model and guardrail documentation exist; CI runs ruff,
pytest, frontend checks, secret scanning, and dependency audit. Gaps: no formal
privacy impact/clinical risk assessment, no enforced code-review process in this
environment, no image/SBOM/IaC scanning, no release approval/rollback exercise,
and no independent penetration test. Tests use synthetic data only.

## 13. Required security tests

**Partial.** Implemented and passing in this repo:

- **Authorization/IDOR:** role×endpoint matrix, cross-doctor appointment
  concealment, nurse read-only appointment access (writes denied),
  patient-access concealment, self-escalation refusal.
- **Appointment concurrency:** doctor/patient advisory locks; one of two
  concurrent bookings wins.
- **LLM/prompt injection:** cross-patient/tool-scope tampering, malicious/
  out-of-scope requests, output secret/HTML/citation blocking, patient-scoped
  tool binding (`backend/tests/test_llm_guardrails.py`, `test_ai_api.py`).
- **Voice/upload bounds:** unsupported/empty/long audio, body-size allowance,
  FFmpeg conversion, temp cleanup, mocked speech failures.
- **Log/audit hygiene:** secret/clinical-payload log scan; documented audit
  inventory.
- **AI proposal safety:** `tools=[]`, proposal-only, DST/conflict warnings,
  explicit create.

Not covered / missing: MFA and step-up, tenant isolation, RLS/separate AI DB
identity, malware scanning of uploads, export-volume approval, deletion/
correction propagation, backup restore, incident-response exercise, provider
data-handling verification, and clinical performance/subgroup validation.

## 14. Incident response

**Gap.** There is no incident-response runbook, notification template, evidence
preservation procedure, or exercised credential-revocation drill in this repo.
`docs/BACKLOG.md` tracks the missing compromised-credential plan (B28).

## 15. Agent operating instructions

**Partial / mostly implemented.** The system prompt separates trusted rules from
untrusted data, forbids credential/system-prompt disclosure, requires tool
results for record facts, cites records, and says not to invent missing facts.
The appointment interpreter uses `tools=[]` and a strict JSON contract. The model
cannot write or choose identity. Missing from the prompt layer: the explicit
"stop and report a possible incident if a tool returns another patient's data"
workflow and persistent-memory rules; these are partly enforced structurally
instead.

## 16. Release gate

**NOT MET for real patient data.** The repository remains a synthetic-data
demo. The following baseline release-gate items are not satisfied:

- Complete data inventory/data-flow/retention inventory.
- Approved threat model, privacy assessment, and clinical risk assessment.
- Legal/regulatory/contract/residency review and approved model/subprocessors.
- Separate least-privilege AI DB identity/RLS/tenant isolation.
- Care-relationship/consent/purpose authorization.
- MFA/step-up and production secrets/HSM/backup/egress controls.
- Audit tamper-resistance and monitoring/alerting.
- Retention/deletion/correction/consent workflows.
- Malware scanning for uploads and export approval/audit.
- Incident-response exercise and independent penetration/clinical validation.
- Named security/privacy/clinical owners and formal sign-off.

## 17. Standards and legal review

**Not assessed here.** No claim of GDPR, HIPAA, ISO 27001/27701, NIST, or
medical-device compliance is made. Applicability requires qualified legal,
privacy, security, and clinical review.

## 18. Ownership and review

**Gap.** No named owners or review cadence are recorded in this repo. The
baseline should be re-reviewed at least annually and after material changes to
models, tools, data sources, providers, regions, roles, or intended use.

## Current implementation summary

**Working controls worth keeping:** deny-by-default policy and route matrix,
server-side session identity, CSRF, parameterized ORM, seven read-only MCP
tools, per-call tool reauthorization, minimized tool payloads, prompt-injection
containment, deterministic output checks, bounded AI/voice inputs, appointment
advisory locks and explicit confirmation, synthetic-only data, no audio/
transcript persistence or logging, private speech networking, security headers,
audit metadata for sensitive mutations, secret scanning, and dependency checks.

**Highest-priority remediation before any real-data consideration:**

1. Add care-relationship/consent/purpose authorization and tenant isolation
   (or formally rule them out for the deployment).
2. Introduce a separate least-privilege AI DB identity/read-only views and RLS.
3. Add MFA/step-up, production secrets/HSM, backup restoration, egress
   controls, image/SBOM scanning, and monitoring/alerting.
4. Close audit gaps: successful AI-history reads, appointment reads, voice
   transcription, and print/export events; add tamper-resistance.
5. Define retention/deletion/correction propagation for AI history, audit,
   files/transcripts, caches, and backups.
6. Add malware scanning/quarantine for uploads and a distinct export permission/
   approval/audit flow.
7. Verify the external LLM and speech provider terms, training/retention,
   subprocessors, residency, and incident obligations.
8. Run independent penetration, privacy, clinical-safety, and legal reviews;
   exercise incident response and credential revocation.

## Non-claims

Do not describe this implementation as GDPR compliant, HIPAA compliant,
certified, clinically validated, production-ready, or safe for real patient
data. Those claims require controls, contracts, operational practices, and
independent assessments beyond this repository.
