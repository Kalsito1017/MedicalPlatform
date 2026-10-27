# API Contract (frozen — integration backbone)

> This contract is frozen at Phase 1 so backend and frontend can be built in parallel.
> Any change must be recorded here first. Base path: **`/api/v1`**. Content type: `application/json`.

## Conventions

- **Timestamps:** ISO-8601 with timezone, UTC (`2026-10-26T13:00:00Z`).
- **IDs:** opaque UUID strings. Never sequential; never guessable.
- **Versioning:** URL path (`/api/v1`). Breaking changes require `/api/v2`.
- **Correlation:** every response carries `X-Request-Id`. Clients should echo it in bug reports.

### Error envelope (all non-2xx JSON responses)

```json
{
  "error": {
    "code": "forbidden",
    "message": "You do not have permission to perform this action.",
    "request_id": "9f0c…",
    "details": {}
  }
}
```

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `validation_error` | Malformed input. `details` lists field errors. |
| 401 | `unauthenticated` | No/expired session. |
| 403 | `forbidden` | Authenticated but not permitted. |
| 404 | `not_found` | Missing **or deliberately concealed** resource. |
| 409 | `conflict` | State conflict (e.g. last-admin protection). |
| 429 | `rate_limited` | Retry after `Retry-After` seconds. |
| 500 | `internal_error` | Generic message only; details go to server logs. |
| 503 | `dependency_unavailable` | Optional dependency (e.g. LLM) down. |

Stack traces, SQL, credentials, and internal paths are **never** returned to clients.

### Pagination (list endpoints)

Query: `?limit=<1..100, default 20>&offset=<0..>`. Response:

```json
{ "items": [ … ], "total": 137, "limit": 20, "offset": 0 }
```

Filter parameters are bounded and validated; unknown filters are a `validation_error`.

### Authentication (cookie session)

- Session cookie: `cap_session`, `HttpOnly; Secure; SameSite=Lax; Path=/`.
- CSRF: double-submit. Non-GET requests require header `X-CSRF-Token` matching the `csrf_token` cookie.
- Every protected endpoint is authorized **server-side**. UI hiding is never the control.

## Endpoints

### Health (unauthenticated)

| Method | Path | Notes |
|---|---|---|
| GET | `/health/live` | Process liveness. Never depends on DB/LLM. |
| GET | `/health/ready` | Checks DB. LLM is optional and does not gate readiness. |

### Auth

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/login` | public | `{email, password}` → 200 + session cookie + user payload. Rate limited. Uniform failure message for unknown email vs bad password. |
| POST | `/api/v1/auth/logout` | authenticated | Invalidates session. |
| GET | `/api/v1/auth/me` | authenticated | Current user, roles, effective permissions. 401 when anonymous. |

User payload: `{ "id", "email", "display_name", "roles": ["doctor"], "permissions": ["patient:read", …], "is_active" }`.

### Patients (synthetic data)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/patients` | `patient:read` | Paginated. `?q=` filters synthetic name/MRN. |
| GET | `/api/v1/patients/{patient_id}` | `patient:read` | Demographics + provenance label. |
| GET | `/api/v1/patients/{patient_id}/summary` | `patient:read` | Synthetic summary: demographics, encounters, active problems, latest labs, active meds **plus the roundup fields below** (additive extension, 2026-10-27). |
| GET | `/api/v1/patients/{patient_id}/labs` | `lab:read` | Paginated. `?limit=` bounded. |
| GET | `/api/v1/patients/{patient_id}/prescriptions` | `prescription:read` | Paginated. |
| GET | `/api/v1/encounters/{encounter_id}` | `patient:read` | Returns 404 (not 403) when concealed. |

Every clinical payload includes `"provenance": "synthetic"`; the UI marks synthetic data
via per-record provenance badges plus footer and "About this demo" dialog disclosures
(the persistent header banner was removed at the owner's request, 2026-10-26).

### Synthetic patient summary payload (`GET /patients/{patient_id}/summary`)

**Change record:** additive extension, 2026-10-27 (wave W, doctor-panel tools).
No existing key was removed or renamed; every list stays bounded by
`app.services.patients` constants. The MCP `get_patient_summary` tool returns
the **same keys** (its text strings additionally carry `[truncated]` markers at
the tool bounds) so the UI and the AI report identical facts.

```json
{
  "patient": { "id", "mrn", "display_name", "given_name", "family_name",
               "date_of_birth", "sex", "blood_type", "age_years",
               "provenance", "created_at", "updated_at" },
  "encounters": [ { …, "weight_kg", "height_cm" } ],
  "vitals": { "weight_kg", "height_cm", "measured_at",
              "previous_weight_kg", "previous_measured_at", "provenance" },
  "diagnoses": [ { "code", "name", "status", "onset_date", "provenance" } ],
  "active_problems": [ { "code", "display_name", "status", "onset_date", "provenance" } ],
  "problems": [ … same as active_problems … ],
  "symptoms": "…", "assessment": "…", "proposed_plan": "…",
  "prescribed_treatment": [ { "prescription_id", "medication", "dose_amount",
                              "dose_unit", "route", "frequency",
                              "items": [ { "sequence_no", "medication",
                                           "dose_amount", "dose_unit" } ] } ],
  "latest_labs": [ … ],
  "active_prescriptions": [ { …, "items": [ … ] } ],
  "provenance": "synthetic"
}
```

Semantics of the added fields:

- `blood_type`: closed ABO/Rh vocabulary (`A+` … `O-`, `unknown`); `null` = not
  recorded. `age_years`: integer **derived from `date_of_birth` on every read**
  (never stored); `null` when no date of birth is recorded.
- `vitals`: the latest per-visit vitals snapshot (`weight_kg`/`height_cm` are
  encounter-level, they may drift between visits) plus the previous weight so
  a trend is visible; `null` when no visit recorded vitals. Per-visit values
  also appear on each `encounters[]` entry.
- `diagnoses`: the problem list, **active entries first**, then most recent;
  `active_problems`/`problems` are the active subset. `symptoms`/`assessment`/
  `proposed_plan` come from the **most recent clinical note** (`null` when the
  patient has no note) — proposed treatment is what the record says was
  proposed, never advice.
- `prescribed_treatment`: the **active** prescriptions rendered as prescribed
  treatment with their `items` (what was actually prescribed, e.g. taper
  steps); `active_prescriptions` keeps the raw rows. Decimal values serialize
  as JSON strings, timestamps as ISO-8601 UTC `Z`.

### Staff & roles (admin)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/staff` | `staff:read` | Paginated; never returns password hashes or tokens. |
| POST | `/api/v1/staff` | `staff:manage` | Create account. Body: `{email, display_name, password?}` (extra fields rejected). If `password` is omitted, a strong password is generated and returned **once** in `generated_password`. Duplicate email → 409. Roles are granted separately via the role endpoints below. |
| PATCH | `/api/v1/staff/{user_id}` | `staff:manage` | Display name, activate/deactivate. Deactivating the last **active** `clinical_admin` → 409 `conflict`. Deactivation revokes all of that account's sessions immediately. |
| POST | `/api/v1/staff/{user_id}/roles` | `role:assign` | `{ "role": "nurse" }`. Role keys: `doctor` \| `nurse` \| `clinical_admin`; any other key (including the removed `tech_admin`) → 400 `validation_error`. Self role assignment → 403. |
| DELETE | `/api/v1/staff/{user_id}/roles/{role}` | `role:assign` | Role keys as above (unknown key → 400). Removing `clinical_admin` from the last **active** holder → 409 `conflict` (at least one active Admin must remain). |

### Audit

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/audit` | `audit:read` | Paginated audit events: actor, action, resource ref, outcome, timestamp, request id. No clinical payloads, no secrets. |

### AI assistant (optional feature)

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/api/v1/ai/ask` | `patient:read` | Body `{ "patient_id", "question" }` (patient-scoped) or `{ "question" }` (general lookup). Response (both modes): `{ "answer", "used_tools", "citations", "model", "ai_generated": true, "disclaimer" }`. Rate limited (10/min per user, shared across both modes) and bounded (`question` 1–2000 chars, never blank). 401 anonymous; 403 without `patient:read`; 400 on malformed/oversized questions; 429 `rate_limited` with `Retry-After`; 503 `dependency_unavailable` when the LLM is disabled/unreachable. Every successful ask is **persisted to the caller's history** (below) in the same request lifecycle; the response shape is unchanged (no added keys). |
| GET | `/api/v1/ai/conversations` | `patient:read` | **Conversation history (new, wave V).** The caller's **own** persisted exchanges, newest first, in the standard pagination envelope `{ "items", "total", "limit", "offset" }` (`limit` 1–100 default 20, `offset` ≥ 0). Query: `patient_id` (optional UUID filter — patient-scoped exchanges only), `limit`, `offset`; unknown query parameters are `400 validation_error`. Each item: `{ "id", "patient_id", "question", "answer", "used_tools", "citations", "model", "created_at" }` where `patient_id` is `null` for general-lookup questions, `citations` are the `{type, id}` references as in the ask response, and `created_at` is ISO-8601 UTC `Z`. 401 anonymous; 403 without `patient:read`. Rows of other users are **never** returned (server-side `user_id` scoping; a foreign read is simply empty). |
| DELETE | `/api/v1/ai/conversations` | `patient:read` | Clear the caller's **entire own** history. `204` empty body. Audited as `ai.history.clear` (metadata only). CSRF-protected. |
| DELETE | `/api/v1/ai/conversations/{conversation_id}` | `patient:read` | Delete one **own** exchange. `204`; a foreign or unknown id is `404 not_found` (concealment) and changes nothing. Audited as `ai.history.delete` (metadata only). CSRF-protected. |

**Conversation history semantics** (storage: `admin.ai_conversation`, see
`backend/app/models/ai.py`): per-user **private** history of synthetic-demo
conversations, readable and deletable only by its owner. It is deliberately NOT
an audit table — audit keeps recording only `ai.ask`/`mcp.*` metadata as before
and never payloads — and deliberately NOT soft-deleted: users erase their own
history explicitly through the endpoints above. `patient_id` is a plain UUID
(`null` = general lookup), not a foreign key, so history survives clinical
record churn. A production build with real clinical content must add a
retention/erasure policy first (docs/BACKLOG.md). History endpoints are not
rate limited (the ask endpoint keeps its 10/min limit).

**Two modes, one endpoint** (the response shape is identical in both):

- **Patient-scoped** (`patient_id` present): one bounded question about one
  synthetic patient. Requires `patient:read` **and** patient access; an
  inaccessible or unknown patient is `404 not_found`, never 403 (concealment).
- **General lookup** (`patient_id` absent): a question across the synthetic
  patients the caller is authorized for (e.g. "who is on metformin?"). The
  model may search patients and read their records via the same four MCP tools
  under the same authorization; `citations` may reference multiple patients
  (`{type, id}` entries as in patient mode). Requires `patient:read` — users
  without clinical read access get `403`, exactly as for clinical reads.

Bounds differ only in the tool-round cap: patient-scoped questions allow
`LLM_MAX_TOOL_CALLS` tool calls (default 4), general-lookup questions
`LLM_MAX_TOOL_CALLS_GENERAL` (default 6, env-driven, clamped to 1–20) because a
general answer typically needs a search plus follow-up reads. Both modes keep
the per-tool result caps, per-call timeouts, and the total wall-clock budget.

Answers must distinguish tool-retrieved facts from general explanation; missing
facts are acknowledged, never invented. Multi-patient answers respect
authorization: only records the tools returned (i.e. records the caller may
read) may be reported, and each claim is cited to the synthetic records that
support it — the answer must list the records consulted. Record text is DATA,
never instructions. Output is labelled AI-generated and is not a diagnosis or
treatment recommendation. Every ask is audited (`ai.ask`, resource `patient`
with the patient id in patient mode, `ai_lookup` in general mode) together with
one `mcp.<tool>` audit event per tool call; prompts, questions, and clinical
payloads are never audited or logged. History mutations audit as
`ai.history.clear` / `ai.history.delete` (metadata only). Each successful ask
additionally persists its exchange (question, answer, used_tools, citations,
model, patient_id) to the caller's private history in the same commit as its
`ai.ask` audit event — one row per request, never double-written on a single
request lifecycle.

## Permission matrix (source of truth: `backend/app/authorization/policy.py`)

Three roles only (`tech_admin` was removed from the system entirely — owner
decision, 2026-10-26). `analytics:read` is declared now for the analytics page
a later wave builds; no endpoint requires it yet.

| Permission | doctor | nurse | clinical_admin (display label: **Admin**) |
|---|---|---|---|
| `patient:read` | ✔ | ✔ | ✔ |
| `lab:read` | ✔ | ✔ | ✔ |
| `prescription:read` | ✔ | ✔ | ✔ |
| `staff:read` | ✖ | ✖ | ✔ |
| `staff:manage` | ✖ | ✖ | ✔ |
| `role:assign` | ✖ | ✖ | ✔ |
| `audit:read` | ✖ | ✖ | ✔ |
| `analytics:read` | ✖ | ✖ | ✔ |

Deny by default. `nurse` is least-privilege clinical read (no staff/audit
administration and therefore no admin panel); `clinical_admin` (Admin) holds
**every** permission including clinical reads.

## MCP tool contract (internal, not a public HTTP API)

**Change record:** `get_my_recent_patients` added 2026-10-27 (wave W, doctor
panel tools); `get_patient_summary` extended the same day with the summary
payload fields documented above. Nothing else changed.

Tools: `search_authorized_patients(query, limit)`, `get_patient_summary(patient_id, limit)`,
`get_patient_lab_results(patient_id, limit)`, `get_recent_prescriptions(patient_id, limit)`,
`find_patients_by_lab(test_code, abnormal_only, limit)`,
`find_patients_by_medication(medication_name, active_only, limit)`,
`get_my_recent_patients(days, limit)`.

Signatures (typed, bounded; `limit` defaults are 10/10/20/20/25/25/25 and all
clamp to 1..25 or 1..50 — out-of-range `limit` is a controlled
`invalid_arguments` error at the registry, and the lookup/panel tools
additionally clamp 1..50 internally):

- `search_authorized_patients(query: str (≤100), limit: int (1-25))`
- `get_patient_summary(patient_id: uuid, limit: int (1-25))`
- `get_patient_lab_results(patient_id: uuid, limit: int (1-50))`
- `get_recent_prescriptions(patient_id: uuid, limit: int (1-50))`
- `find_patients_by_lab(test_code: str (≤32, exact known lab test code), abnormal_only: bool = true, limit: int (1-50))`
- `find_patients_by_medication(medication_name: str (≤100), active_only: bool = true, limit: int (1-50))`
- `get_my_recent_patients(days: int = 7 (1-365), limit: int = 25 (1-50))`

The two lookup tools serve general (cross-patient) questions in 1-2 calls and
return ONLY patient references — `[{patient_id, display_name, mrn, matched}]`,
one row per patient, most recent matching record first, strings truncated with
`[truncated]` — never a full clinical payload. Decisions:

- `find_patients_by_lab`: `test_code` must match a known lab test code exactly
  (exact match against the seeded lab catalog via a bounded parameterized
  query — no free text); an unknown code is a controlled `unknown_test_code`
  error that lists known codes most-used first (catalog data, never an echo of
  the submitted value).
  Abnormality is computed from `value_num` against `ref_low`/`ref_high`
  (strictly outside the range); the result's `status` field is the document
  state (e.g. `final`) and is **not** an abnormality flag — it is never used.
  Text-only results without a numeric value can never qualify as abnormal.
  `matched` is a short reason like `Potassium 5.8 mmol/L high, 2026-09-24`.
- `find_patients_by_medication`: case-insensitive substring match on the
  prescription's medication name or the linked catalog medication code, with
  LIKE wildcards escaped to literal (parameterized ORM only); `active_only`
  restricts to `status = active` prescriptions. `matched` looks like
  `Metformin 500 mg, active`.
- Visibility of every returned patient goes through the same
  `services.access.check_patient_access` seam as the REST API: the tool checks
  `patient:read` (plus `lab:read` / `prescription:read`, since `matched` carries
  record values) once per call, then runs the seam per returned patient (one
  cheap indexed check per candidate) and silently drops non-visible rows,
  paging past them within a bounded candidate scan so concealed rows never
  crowd out visible ones. A caller without the
  permissions gets an empty result (audited outcome `denied`).

`get_my_recent_patients(days, limit)` is the practitioner panel roundup ("which
patients went through me in the last N days"):

- **"Me" is the verified principal in the injected `ToolContext`
  (`principal.user_uuid`) — never a tool argument.** The LLM-facing schema
  exposes only `days` (int, 1..365, default 7) and `limit` (int, 1..50,
  default 25); smuggled identity arguments are ignored, and out-of-range
  values are a controlled `invalid_arguments` error at the registry (direct
  invocation clamps 1..365 / 1..50 as defense in depth).
- **"Went through me" definition (window = `[now − days, now]` UTC, inclusive
  both ends, one freezeable clock):** a patient qualifies when in the window
  they had (a) **primary** — an encounter whose `practitioner_id` is the
  caller, **or** (b) **union** — an active `patient_assignment` to the caller
  (`ended_at` NULL) **and** any encounter in the window (with anyone). When
  both match, (a) wins: the patient is represented by their most recent
  encounter *with* the caller (that encounter is cited and reported as
  `last_encounter_at`); assignment-only patients are represented by their most
  recent in-window encounter. Rows are ordered by the representing encounter,
  most recent first. The "mine" filter only ever **narrows** visibility.
- One compact row per patient, all strings bounded with `[truncated]`:
  `{patient_id, display_name, mrn, age_years, sex, blood_type,
  last_encounter_at, weight_kg, symptoms, diagnoses (names, active first,
  ≤3), key_results (≤5 "K 5.86 mmol/L high (2026-09-24)" strings — the most
  recent result per test code, abnormal first), proposed_plan,
  prescribed_treatment (active, ≤3 "Metformin 500 mg oral twice daily"
  strings)}`. `weight_kg` is the most recent recorded vitals snapshot;
  symptoms/proposed plan come from the most recent clinical note; `age_years`
  is derived (never stored). Data payload: `{window_days, window_start,
  patients, total, note?}` — rows are bounded by `limit` while `total` counts
  every **visible** patient (so the model can say "showing 10 of 23").
- Authorization: `patient:read` required (deny → empty result + note, outcome
  `denied`); the per-patient `check_patient_access` seam then runs on every
  candidate row (non-visible rows are dropped and paging continues past them
  within a bounded candidate scan), so concealed rows never occupy row slots.
  `total` counts only rows the caller may see.
- Audit: `mcp.get_my_recent_patients`, resource_type `patient_panel`,
  resource_id `None` — never search terms or clinical payloads. Citations:
  patient + representing encounter + the clinical_note/lab_result/prescription
  refs actually used in the rows.

General answers may report only what these tools returned (authorization is
re-executed per call); every ask is audited with one `mcp.<tool>` event per
tool call (`mcp.find_patients_by_lab` / `mcp.find_patients_by_medication` /
`mcp.get_my_recent_patients` included) — never a search term or clinical payload.

- Read-only, typed arguments, bounded results, explicit timeouts.
- Acting identity comes from the verified server-side session context — **never** a tool argument.
- Authorization is re-executed inside the tool layer on every call via the same policy module as the REST API.
- No `execute_sql`, no shell, no filesystem, no generic database access. Ever.
