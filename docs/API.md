# API Contract (frozen — integration backbone)

> This contract is frozen at Phase 1 so backend and frontend can be built in parallel.
> Any change must be recorded here first. Base path: **`/api/v1`**. Content type: `application/json`, except the voice transcription route (`multipart/form-data`).

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

### Idempotent mutations (`Idempotency-Key`, wave UI-B)

`POST /api/v1/staff` and `POST /api/v1/ai/ask` — the two POSTs whose
double-apply would hurt — accept an optional request header
`Idempotency-Key: <client-generated random token>` (1–200 chars from
`[A-Za-z0-9._~-]`; an invalid key is `400 validation_error`). Semantics
(same for both endpoints):

- **Scope:** `(user_id, endpoint, key)`. Another user replaying the same key
  executes normally — records never cross users or endpoints.
- **Replay:** the same key + the **same request payload** within the retention
  window (**24 hours**) returns the recorded response (original status code and
  body) with response header `Idempotency-Replayed: true` **without
  re-executing**: no second account, no second LLM call, no second history
  row, no second audit event. For `POST /ai/ask` the replay also skips the
  access check and the rate limiter (the original request already passed
  both).
- **Conflict:** the same key + a **different payload** is `409 conflict`
  (documented choice over 422: the key was already consumed by a different
  request of the same user — a state conflict, not a malformed body). The
  payload is never stored; a SHA-256 request fingerprint detects the mismatch.
- **Failures are not recorded:** only the first successful (2xx) response is
  recorded, so a retry after an error re-executes normally.
- **Missing header:** unchanged behaviour — fully backwards compatible.
- **Storage:** `admin.idempotency_record` (unique on `(user_id, endpoint,
  key)`), response body as JSON text. **Secrets are never stored:** the staff
  one-time `generated_password` is recorded and replayed as `null` (a generated
  password is shown exactly once). Expired rows are pruned opportunistically.
- **Concurrency:** the unique constraint is the arbiter — the second insert
  loses, is rolled back, and is served as a replay of the winner's record.

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
| GET | `/api/v1/patients` | `patient:read` | Paginated. `?q=` filters name/MRN. |
| POST | `/api/v1/patients` | `patient:write` | Creates a synthetic patient. Body `{given_name, family_name, display_name?, date_of_birth?, sex?, blood_type?}`; `sex` is `female|male|other|unknown` (default `unknown`), `blood_type` is the closed ABO/Rh vocabulary or `null`. MRN and `provenance` are server-assigned; the MRN is generated and unique. `201` with `PatientOut`; `400 validation_error` for blank names, future DOB, unknown enum values or extra fields. Audited `patient.create` (metadata only). Doctor/Admin only; Nurse `403`. |
| DELETE | `/api/v1/patients/{patient_id}` | `patient:delete` | **Admin-only soft delete.** `204` empty body; the patient row gets `deleted_at` and disappears from list/search/detail and appointment reads, while child clinical rows and audit history are retained internally. Unknown/already-deleted id is `404 not_found`. Audited `patient.delete` (metadata only). Doctor/Nurse `403`. |
| GET | `/api/v1/patients/{patient_id}` | `patient:read` | Demographics. |
| GET | `/api/v1/patients/{patient_id}/summary` | `patient:read` | Demographics, encounters, active problems, latest labs, active meds **plus** visit/vitals summaries and observed medication-response rows (additive extensions; latest 2026-11-11). |
| GET | `/api/v1/patients/{patient_id}/encounters` | `patient:read` | Paginated encounter history, newest first; `?limit=`/`?offset=` bounded 1–100; 404 concealment; additive extension, 2026-11-08. |
| GET | `/api/v1/patients/{patient_id}/labs` | `lab:read` | Paginated. `?limit=` bounded. |
| GET | `/api/v1/patients/{patient_id}/labs/trends` | `lab:read` | Bounded numeric lab trends; see the 2026-11-04 contract below. |
| GET | `/api/v1/patients/{patient_id}/prescriptions` | `prescription:read` | Paginated. |
| GET | `/api/v1/encounters/{encounter_id}` | `patient:read` | Returns 404 (not 403) when concealed. |

**Contract reconciliation, 2026-11-04:** the dataset remains synthetic internally,
but provenance is database-internal and is not serialized into clinical payloads.
By product decision, the UI has no visible demo/synthetic/disclaimer/provenance copy.

### Messages (Doctor/Admin internal)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/messages` | `message:read` | Own messages only. Query `box=inbox|sent` (default `inbox`), optional bounded search `q` (1–100 chars; sender/recipient display name, subject or body), and bounded `limit`/`offset`. Newest first. Response is the standard page envelope; each item contains sender/recipient ids and display names, subject, body, `read_at`, and timestamps. Search is always scoped to the caller's own mailbox. |
| GET | `/api/v1/messages/recipients` | `message:read` | Active, non-deleted Doctor/Admin recipients (the caller is excluded). Returns `{items: [{id, display_name, roles}]}`, bounded to 100. |
| GET | `/api/v1/messages/counts` | `message:read` | Caller-private counts: `{inbox_total, unread_total, sent_total}`. Never includes another user's mailbox. |
| POST | `/api/v1/messages` | `message:write` | Body `{recipient_id, subject?, body}`; subject ≤200 chars, body 1–5000 chars. Recipient must be an active Doctor/Admin and cannot be self. `201` with the message; audited `message.send` (metadata only — never subject/body). |
| POST | `/api/v1/messages/{message_id}/read` | `message:read` | Recipient-only mark-read; `204`, idempotent, another user's/unknown id is `404`. Audited `message.read` (metadata only). |

Messages are internal staff-to-staff records: no attachments, no patient payload validation, and no raw message content in audit events. Nurse has no message permissions (`403`). Body/subject are rendered as plain text in the UI.

### Synthetic patient summary payload (`GET /patients/{patient_id}/summary`)

**Change record:** additive extension, 2026-10-27 (wave W, doctor-panel tools);
2026-11-11 adds per-visit BP/vitals, `last_visit`, `visit_count` and bounded
`medication_responses` (derived observed changes, no new outcome table).
No existing key was removed or renamed; every list stays bounded by
`app.services.patients` constants. The MCP `get_patient_summary` tool returns
the **same keys** (its text strings additionally carry `[truncated]` markers at
the tool bounds) so the UI and the AI report identical facts.

```json
{
  "patient": { "id", "mrn", "display_name", "given_name", "family_name",
               "date_of_birth", "sex", "blood_type", "age_years",
               "created_at", "updated_at" },
  "encounters": [ { …, "weight_kg", "height_cm", "systolic_bp",
                    "diastolic_bp", "heart_rate", "temperature_c",
                    "oxygen_saturation" } ],
  "vitals": { "weight_kg", "height_cm", "measured_at",
              "previous_weight_kg", "previous_measured_at",
              "systolic_bp", "diastolic_bp", "heart_rate",
              "temperature_c", "oxygen_saturation",
              "previous_systolic_bp", "previous_diastolic_bp",
              "previous_heart_rate" },
  "diagnoses": [ { "code", "name", "status", "onset_date" } ],
  "active_problems": [ { "code", "display_name", "status", "onset_date" } ],
  "problems": [ … same as active_problems … ],
  "symptoms": "…", "assessment": "…", "proposed_plan": "…",
  "prescribed_treatment": [ { "prescription_id", "medication", "dose_amount",
                              "dose_unit", "route", "frequency",
                              "items": [ { "sequence_no", "medication",
                                           "dose_amount", "dose_unit" } ] } ],
  "latest_labs": [ … ],
  "active_prescriptions": [ { …, "items": [ … ] } ],
  "last_visit": { "id", "occurred_at", "encounter_type", "summary", "weight_kg",
                  "height_cm", "systolic_bp", "diastolic_bp", "heart_rate",
                  "temperature_c", "oxygen_saturation" },
  "visit_count": 8,
  "medication_responses": [ { "prescription_id", "medication", "status",
                               "started_on", "metric_code", "metric_name", "unit",
                               "baseline": { "measured_at", "value" },
                               "follow_up": { "measured_at", "value" },
                               "current": { "measured_at", "value" },
                               "delta", "trend", "points": [ … ] } ]
}
```

Semantics of the added fields:

- `blood_type`: closed ABO/Rh vocabulary (`A+` … `O-`, `unknown`); `null` = not
  recorded. `age_years`: integer **derived from `date_of_birth` on every read**
  (never stored); `null` when no date of birth is recorded.
- `vitals`: the latest per-visit vitals snapshot (`weight_kg`, `height_cm`,
  `systolic_bp`, `diastolic_bp`, `heart_rate`, `temperature_c`,
  `oxygen_saturation`) plus previous weight/BP/HR readings. Values are
  encounter-level and may drift between visits; `null` when no visit recorded
  vitals. Per-visit values also appear on each `encounters[]` entry.
- `last_visit` / `visit_count`: the newest encounter preview and total
  non-deleted encounter count. `encounters` is bounded to the newest 8 rows.
- `medication_responses`: bounded observed before/after values derived from
  prescriptions plus recorded labs or BP. `trend` is one of `improved`,
  `worsened`, `stable`, `monitor`, `insufficient_data`; it describes the
  observed synthetic-data direction and is **not a causal claim**. No new
  stored outcome table exists.
- `diagnoses`: the problem list, **active entries first**, then most recent;
  `active_problems`/`problems` are the active subset. `symptoms`/`assessment`/
  `proposed_plan` come from the **most recent clinical note** (`null` when the
  patient has no note) — proposed treatment is what the record says was
  proposed, never advice.
- `prescribed_treatment`: the **active** prescriptions rendered as prescribed
  treatment with their `items` (what was actually prescribed, e.g. taper
  steps); `active_prescriptions` keeps the raw rows. Decimal values serialize
  as JSON strings, timestamps as ISO-8601 UTC `Z`.
- The MCP `get_patient_summary` response carries the same clinical facts plus
  the compact `last_visit`, `visit_count` and up to three
  `medication_responses`; it does not add a new MCP tool.

### Encounter history (`GET /patients/{patient_id}/encounters`)

**Additive extension, 2026-11-08.** Returns the complete paginated encounter
history for one authorized patient in the standard `Page` envelope
`{ "items", "total", "limit", "offset" }`. `limit` is 1–100 (default 20),
`offset` ≥ 0; unknown query parameters are `400 validation_error`. Encounters
are newest first (`occurred_at DESC, id`), soft-deleted rows are excluded, and
each item is the existing `EncounterOut` shape plus the 2026-11-11 vitals
extension (`systolic_bp`, `diastolic_bp`, `heart_rate`, `temperature_c`,
`oxygen_saturation`; all nullable for older rows). Unknown, soft-deleted, and
inaccessible patient ids are `404 not_found` (concealment).
Requires `patient:read`; each read is audited with the existing `patient.read`
action and resource type `patient`.

### Numeric lab trends (`GET /patients/{patient_id}/labs/trends`)

**Change record:** additive endpoint and response contract, **2026-11-04**.
No existing endpoint, key, default, or MCP tool changed.

Example: `GET /api/v1/patients/{patient_id}/labs/trends?days=365&tests=K,GLU`.

| Query parameter | Contract |
|---|---|
| `days` | Optional integer, **1..365**, default **365**. Selects the trailing UTC window ending at request evaluation time. |
| `tests` | Optional comma-separated list of at most **5** exact lab-test codes present in the patient's eligible trend data. Unknown codes, an empty/invalid list, or more than five codes are `400 validation_error`. When omitted, the endpoint auto-selects up to five tests with the most recent numeric results. |

Any other query parameter is `400 validation_error`. The endpoint requires
`lab:read` and passes the same `check_patient_access` seam as other patient
reads. A missing or inaccessible patient is deliberately indistinguishable:
`404 not_found`, never an existence-revealing `403`. The read writes exactly
one `labs.read` audit event with outcome metadata only (no test list or lab
values).

Only **non-deleted labs with a numeric `value_num`** are eligible; text-only
results are excluded. At most **5 series** are returned, one per selected test
code. For each series the **50 most recent** eligible points in the window are
selected and then returned in **ascending `collected_at` order**. `truncated`
is `true` when that per-series cap omitted older eligible points or the
automatic selection omitted additional test series, otherwise `false`.

```json
{
  "patient_id": "7d3106f4-42a7-4fb6-a42f-0eb4f8a5f20a",
  "window_days": 365,
  "series": [
    {
      "test_code": "K",
      "test_name": "Potassium",
      "unit": "mmol/L",
      "ref_low": "3.5",
      "ref_high": "5.1",
      "points": [
        {
          "collected_at": "2026-09-24T08:30:00Z",
          "value_num": "4.8",
          "flag": "normal"
        }
      ],
      "abnormal_count": 0
    }
  ],
  "truncated": false
}
```

Decimals (`value_num`, `ref_low`, `ref_high`) are **JSON strings**, never binary
floats; unavailable units/bounds are `null`. `flag` is computed for each point
strictly from **that result's own recorded reference bounds**, never from the
lab document `status` or a catalog fallback:

- `high`: `value_num > ref_high` when that result has `ref_high`;
- `low`: `value_num < ref_low` when that result has `ref_low`;
- `normal`: at least one own bound exists and neither strict comparison fires;
- `unclassified`: both own bounds are absent.

`abnormal_count` counts returned points flagged `high` or `low`. The only
patient reference is the already-authorized opaque `patient_id`. The response
contains no name, MRN, date of birth, encounter/lab-result ID, `value_text`,
clinical note or other free text, provenance field, or disclaimer field.

### Staff & roles (admin)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/staff` | `staff:read` | Paginated; never returns password hashes or tokens. |
| POST | `/api/v1/staff` | `staff:manage` | Create account. Body: `{email, display_name, password?}` (extra fields rejected). If `password` is omitted, a strong password is generated and returned **once** in `generated_password`. Duplicate email → 409. Roles are granted separately via the role endpoints below. Supports `Idempotency-Key` (see "Idempotent mutations"): a replay returns the recorded response with `generated_password: null` and creates no second account. |
| PATCH | `/api/v1/staff/{user_id}` | `staff:manage` | Display name, activate/deactivate. Deactivating the last **active** `clinical_admin` → 409 `conflict`. Deactivation revokes all of that account's sessions immediately. |
| POST | `/api/v1/staff/{user_id}/roles` | `role:assign` | `{ "role": "nurse" }`. Role keys: `doctor` \| `nurse` \| `clinical_admin`; any other key (including the removed `tech_admin`) → 400 `validation_error`. Self role assignment → 403. |
| DELETE | `/api/v1/staff/{user_id}/roles/{role}` | `role:assign` | Role keys as above (unknown key → 400). Removing `clinical_admin` from the last **active** holder → 409 `conflict` (at least one active Admin must remain). |

### Audit

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/audit` | `audit:read` | Paginated audit events: actor, action, resource ref, outcome, timestamp, request id. No clinical payloads, no secrets. |

### System Logs (Admin, read-only)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/admin/logs` | `logs:read` | Unified historical page over durable audit rows (`admin.audit_event`) and bounded operational rows (`admin.system_event`). Defaults to the last 15 minutes. `start`/`end` are ISO-8601 timestamps with a timezone and must be provided together; the maximum span is 7 days. Filters: `severity`, `category`, `event_type`, `actor_id`, `request_id`, `outcome`, `q` (approved indexed fields and safe metadata only). `limit` 1–100 (default 50), `offset` 0–500. Unknown parameters/event types/outcomes are `400 validation_error`. 401 anonymous; 403 without `logs:read`. |
| GET | `/api/v1/admin/logs/stream` | `logs:read` | Server-Sent Events stream. Same filters as history. Optional `last_event_id` (or `Last-Event-ID`) replays up to 200 matching events after that cursor, delivered chronologically, before continuing live. Sends `event: system` frames with `id:` and JSON data, `: heartbeat` comments every 15 seconds, and `event: auth_expired` when the session is revoked/expired or the account loses `logs:read`; the connection then closes. 401 anonymous; 403 without `logs:read`. |

`GET /api/v1/admin/logs` response:

```json
{
  "items": [{
    "id": "audit:6a1d…",
    "timestamp": "2026-11-09T12:34:56.123456Z",
    "severity": "info",
    "category": "patients",
    "event_type": "patient.read",
    "description": "Patient record accessed",
    "actor_id": "…",
    "resource_type": "patient",
    "resource_reference": "…",
    "request_id": "…",
    "outcome": "success",
    "metadata": {}
  }],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "counts": {"total": 1, "info": 1, "success": 0, "warning": 0, "error": 0, "security": 0},
  "start": "2026-11-09T12:19:56Z",
  "end": "2026-11-09T12:34:56Z",
  "timezone": "Europe/Sofia"
}
```

`counts` always describes **all events matching the selected period and
filters**, not only the returned page. Items are ordered newest first by
`(timestamp, event_id)`; stream replay is the same order reversed (oldest
first). Event IDs are source-prefixed (`audit:` or `op:`) and are the cursor.
Timestamps are UTC on the wire; the page displays them in the configured
`CLINIC_TIMEZONE` returned as `timezone` (the chat header names the zone).
The stream deliberately does not hold a database session/transaction open:
history/replay uses a short-lived session, then live delivery uses the bounded
in-process bus. Audit rows remain the durable accountability record; this API
never mutates or deletes them. No shell, SQL console, command input or server
administration exists on these endpoints.

### Analytics (admin)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/analytics/summary` | `analytics:read` | One bounded operational overview (Admin only). Optional inclusive UTC dates `start` and `end` (`YYYY-MM-DD`, both or neither). Default: last **30** UTC days including today. Maximum span: **366** days. `start > end`, a partial boundary, or an unknown query parameter is `400 validation_error`. 401 anonymous; 403 without `analytics:read`. Every read is audited as `analytics.read` (metadata only — never a clinical payload). |

**Change record:** added 2026-10-27 (wave U); expanded 2026-11-09 with
validated date ranges, previous-period comparisons, appointments, patient and
encounter operations, expanded lab/prescription/security/AI metrics, data
quality and service configuration. The analytics response shape was
reorganized (the old flat `last_30_days`/top-level lab blocks were replaced by
the range-aware `activity`/`labs` blocks) and the admin page was updated in
the same change. No database schema/table changes. Export/print analytics was
deliberately deferred (see below).

```json
{
  "generated_at": "2026-11-09T12:00:00Z",
  "range": { "start": "2026-10-11", "end": "2026-11-09", "days": 30,
             "previous_start": "2026-09-11", "previous_end": "2026-10-10",
             "timezone": "UTC", "appointment_timezone": "Europe/Sofia" },
  "counts": { "patients", "encounters", "labs", "prescriptions",
              "active_prescriptions", "diagnoses_active", "staff_active",
              "staff_inactive", "ai_conversations" },
  "activity": { "new_patients", "encounters", "lab_results",
                "prescriptions_written", "appointments", "ai_requests",
                "successful_signins", "failed_signins", "permission_denials" },
  "previous_activity": { "...same keys as activity..." },
  "comparisons": { "<metric>": { "current", "previous", "change_pct" } },
  "patients": { "new_patients", "returning_patients", "active_patients",
                "encounters", "avg_encounters_per_day",
                "by_encounter_type": [ { "encounter_type", "count" } ],
                "trend": [ { "day", "encounters" } ], "timezone": "UTC" },
  "appointments": { "total",
                    "by_status": { "scheduled", "confirmed", "completed",
                                   "cancelled", "no_show" },
                    "by_visit_type": [ { "visit_type", "count" } ],
                    "by_weekday": [ { "weekday", "label", "count" } ],
                    "by_hour": [ { "hour", "count" } ],
                    "trend": [ { "day", "total", "completed",
                                 "cancelled", "no_show" } ],
                    "utilization": { "booked_minutes", "available_minutes",
                                     "utilization_pct", "doctors_with_schedule" },
                    "timezone": "Europe/Sofia" },
  "labs": { "total",
            "status_mix": { "final", "preliminary", "pending" },
            "abnormal_rate": { "abnormal", "total", "pct" },
            "classification": { "normal", "high", "low", "unclassified",
                                "missing_reference_ranges",
                                "reference_coverage_pct" },
            "top_abnormal_tests": [ { "test_code", "test_name",
                                      "abnormal_count" } ],
            "trend": [ { "day", "total", "evaluable", "abnormal" } ],
            "timezone": "UTC" },
  "prescriptions": { "total",
                     "by_status": { "active", "completed", "stopped", "cancelled" },
                     "lifetime_by_status": { "...same keys..." },
                     "trend": [ { "day", "prescriptions" } ],
                     "timezone": "UTC", "date_basis": "start_date" },
  "security": { "totals": { "successful_signins", "failed_signins",
                            "permission_denials", "patient_reads",
                            "lab_reads", "prescription_reads", "ai_requests" },
                "trend": [ { "day", "...same keys as totals..." } ],
                "timezone": "UTC" },
  "ai_usage": { "total_requests", "successful", "failed", "denied",
                "success_rate",
                "top_tools": [ { "tool", "calls", "failures" } ],
                "latency": { "available": false, "average_ms": null, "reason" },
                "voice": { "available": false, "reason" },
                "timezone": "UTC" },
  "data_quality": { "checks": [ { "key", "label", "count", "denominator",
                                  "scope", "kind", "note" } ] },
  "system_health": { "backend": "operational", "database": "connected",
                     "api_error_rate": { "available": false, "rate_pct": null,
                                         "reason" },
                     "llm": { "configured", "availability", "note" },
                     "voice": { "configured", "availability", "note" } },
  "staff": { "active_total", "inactive_total",
             "by_role": [ { "role", "count" } ] }
}
```

Semantics (all timestamps UTC; every count is an integer; no patient names,
MRNs, notes, AI questions/answers, audio, transcripts or secrets appear):

- **Range.** `start`/`end` are inclusive UTC calendar dates; the SQL window is
  `[start 00:00Z, end+1 00:00Z)`. The previous period is the equally long
  window immediately before `start`. Daily clinical/audit series
  (`patients.trend`, `labs.trend`, `prescriptions.trend`, `security.trend`)
  bucket by **UTC day** and are zero-filled for exactly `range.days`.
  Appointment day/hour/weekday buckets use the configured
  **`CLINIC_TIMEZONE`** because schedules are local wall-clock values; the
  appointment trend can include one partial local edge day, and its series
  always sums to `appointments.total`.
- **Counts.** `counts` are current lifetime row counts of the bounded demo
  tables (soft-deleted rows excluded immediately; inactive staff counted
  separately). `counts.ai_conversations` is the `admin.ai_conversation`
  history table — deliberately a different source from `ai_usage`: clearing
  personal history shrinks the history table but never erases what the system
  was actually asked.
- **Activity.** `activity` uses the record's own event timestamp in the
  selected window: patients by `created_at`, encounters by `occurred_at`,
  labs by `collected_at`, prescriptions by `start_date`, appointments by
  `start_at`, and audit events by `created_at`. `ai_requests` counts **all**
  `ai.ask` outcomes. `failed_signins` counts `auth.login.failure`.
  `permission_denials` counts `authz.denied`.
- **Comparisons.** `change_pct = (current − previous) / previous × 100`,
  rounded half-up to one decimal; **null when `previous == 0`** (a percentage
  against zero is undefined and is never fabricated). The denominator is the
  equally long preceding period, not a calendar month or year.
- **Patients/encounters.** `new_patients` = patients created in the window;
  `returning_patients` = distinct patients with an encounter in the window
  whose patient row predates it; `active_patients` = every distinct patient
  with an encounter in the window; `avg_encounters_per_day = encounters /
  range.days` rounded to one decimal. `by_encounter_type` is bounded to 20
  groups (count desc).
- **Appointments.** Statuses come from the closed `scheduling` vocabulary:
  `scheduled`, `confirmed`, `completed`, `cancelled`, `no_show`. `by_visit_type`
  is bounded to 20 groups; `by_weekday` is exactly 7 clinic-local weekdays
  (0 = Monday); `by_hour` is exactly 24 clinic-local hours.
  `booked_minutes` sums appointment minutes for doctors with recorded
  working hours overlapping the window, **excluding `cancelled`** (scheduled,
  confirmed, completed and no-show occupied the slot). `available_minutes` is generated **only** from recorded
  `scheduling.working_hours` for the local weekdays, clipped to the UTC window,
  minus overlapping `scheduling.time_off`; doctors without working-hour rows
  contribute no availability. `utilization_pct = booked / available × 100`
  rounded to one decimal, or **null** when no availability is recorded.
  `doctors_with_schedule` is the number of active doctors with at least one
  working interval intersecting the window. No per-doctor ranking is returned.
- **Laboratory rule — the SAME rule the MCP lookup tools implement** (see
  "MCP tool contract" above): a result is abnormal when `value_num` is
  strictly outside the recorded `[ref_low, ref_high]` (a missing bound is
  never exceeded). `status` is the document state and is **never** an
  abnormality flag; text-only results never qualify. Only *evaluable* results
  (numeric value with at least one reference bound) enter
  `abnormal_rate.total`. `classification.normal = evaluable − abnormal`;
  `high` and `low` partition `abnormal` (high wins if a malformed range
  satisfies both); `unclassified = total − evaluable` (text-only or no usable
  bound). `normal + high + low + unclassified == total`.
  `missing_reference_ranges` counts results with neither bound.
  `reference_coverage_pct = evaluable / total × 100` rounded to one decimal.
  Missing/invalid ranges are never silently treated as normal.
  `abnormal_rate.pct` is a rounded **display** number, not a clinical value.
  `top_abnormal_tests` is at most **8** (count desc, code/name asc); the
  status mix and classification vocabularies are always fully listed
  (zero-filled).
- **Prescriptions.** `total` and `by_status` cover course starts in the
  window (`start_date`); `lifetime_by_status` covers all non-deleted
  prescriptions. The lifecycle vocabulary is exactly `active`, `completed`,
  `stopped`, `cancelled`; `stopped` is the recorded value displayed as
  "discontinued". No prescribing-quality judgement is made.
- **Access/security.** `security.totals` aggregates
  `auth.login.success`, `auth.login.failure`, `authz.denied`, the patient-read
  actions (`patient.read`, `patient.summary.read`), `labs.read`,
  `prescriptions.read`, and all `ai.ask` events. `security.trend` is
  zero-filled by UTC day. Analytics remains aggregate-only: no raw audit
  metadata, IPs, user agents, patient identifiers, payloads or secrets.
- **AI usage.** `successful`/`failed`/`denied` partition `total_requests` by
  the audited `ai.ask` outcome. `success_rate = successful / (successful +
  failed) × 100` rounded to one decimal, excluding denied asks; null when that
  denominator is zero. `top_tools` (at most **6**) counts real
  `mcp.<tool>` calls in the window; `failures` counts outcomes other than
  `success`; `mcp.unknown_tool` is a rejection marker, not a tool, and is
  excluded. **Per-request AI latency is not persisted** — `latency.available`
  is `false`, `average_ms` is `null`, and a reason is included. **Voice
  transcription is intentionally ephemeral and never audited/persisted**, so
  `voice.available` is `false` and no volume/language/duration/latency number
  exists. No audio, transcript, question or answer is collected.
- **Data quality / system health.** `data_quality.checks` lists only real
  recorded gaps with a denominator and scope. `kind: optional_metadata` means
  the field is nullable by design and is **not** an error; classification
  checks state why a result cannot be evaluated. `system_health.backend` and
  `database` reflect this successful response; `api_error_rate` is explicitly
  unavailable because API errors are not persisted in the audit stream.
  `llm`/`voice` expose only configuration plus `availability:
  "not_monitored"`; no synthetic probe is created.
- **Staff.** `active_total` and `inactive_total` are current non-deleted
  accounts; `by_role` lists the three policy roles with distinct active
  accounts (an account with two roles is counted under both). No individual
  performance or surveillance metric is returned.
- **Bounds/efficiency.** Lists are bounded as noted; trends are no longer than
  366 daily buckets (appointments may add one partial local edge day).
  Aggregates are computed with a bounded set of grouped/scalar-subquery
  SELECTs (no N+1; statement count and latency asserted in tests). The
  endpoint is one request per filter change and does not download clinical
  datasets to JavaScript.
- **Deferred exports.** CSV/print analytics was not added in this phase. The
  existing print-record feature is patient-record-specific; adding an
  analytics export would need its own authorization, audit and aggregate-only
  redaction review. The API remains the authorized aggregate source and the
  page exposes accessible data tables instead.

### Appointments, availability, and voice scheduling

Appointments are scheduling metadata, not clinical records. All request and
response timestamps are UTC ISO-8601 with `Z`; display and working-hour
interpretation use `CLINIC_TIMEZONE` (**`Europe/Sofia`** in the demo). Working
hours are clinic-local `TIME` values; time off is UTC. DST holes and ambiguous
fall-back times are rejected with `400 validation_error`.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/appointments/config` | `appointment:read` | `{timezone, max_duration_minutes: 480, max_range_days: 62}`. |
| GET | `/api/v1/appointments` | `appointment:read` | Required query `start`, `end` (aware ISO-8601); optional `doctor_id`, `patient_id`, `status`, `limit`, `offset`. Range ≤ **62 days**; `end > start`. A Doctor is always scoped to their own calendar server-side; an Admin may list all doctors or filter by `doctor_id`; a Nurse has a **read-only clinic-wide** view (optional `doctor_id` filter) and cannot write. Response items include display patient name/MRN and doctor name (calendar display fields), duration, status, type, notes, timestamps. Cancelled entries are calendar-visible for **5 minutes** after cancellation and are then omitted from list responses; rows explicitly removed via the admin endpoint are omitted immediately. Records and audit history are retained. The patient filter is concealment-checked (`404` for inaccessible patients). |
| POST | `/api/v1/appointments` | `appointment:write` | Body `{patient_id, start_at, duration_minutes (5–480), appointment_type?, notes?, status?}`. A Doctor may omit `doctor_id` (resolved to the caller) and may not name another doctor; an Admin must name one. `201` on success. The time must be in the future, inside the doctor's working hours, outside time off, and non-overlapping for both doctor and patient. Conflicts are `409`. Patient access failure is `404`. Concurrent bookings for the same doctor or patient are serialized with PostgreSQL transaction advisory locks and re-checked inside the transaction. Appointment mutations are audited (`appointment.create`); the AI never calls this endpoint. |
| GET | `/api/v1/appointments/{appointment_id}` | `appointment:read` | A Doctor sees only their own; another doctor's id is concealed as `404` and audited `appointment.denied`. Admin and read-only Nurse may read any appointment; all write actions stay forbidden for Nurse. |
| PATCH | `/api/v1/appointments/{appointment_id}` | `appointment:write` | Reschedule and/or edit `start_at`, `duration_minutes`, `appointment_type`, `notes`, or close with `status` (`completed` or `no_show`). Cancellation has its own endpoint. A cancelled/completed/no-show appointment is `409` and cannot be modified. Rescheduling runs the same availability/conflict checks. Audited `appointment.update` or `appointment.reschedule`. |
| POST | `/api/v1/appointments/{appointment_id}/cancel` | `appointment:write` | Sets status `cancelled`; idempotent for an already-cancelled row. A completed/no-show row is `409`. The cancelled entry remains in calendar list responses for **5 minutes**, then is omitted automatically. Audited `appointment.cancel`. |
| POST | `/api/v1/appointments/{appointment_id}/remove-from-calendar` | `appointment:write` + **Admin role** | Removes a cancelled appointment from calendar list responses immediately; `204`, idempotent, `409` if the appointment is not cancelled, `404` if missing. This is a visibility change, not a record deletion: the scheduling row, cancellation status and audit history remain, and `GET /appointments/{id}` still returns the record. Body is empty. Audited `appointment.remove_from_calendar`. Doctors and Nurses get `403`. |
| GET | `/api/v1/appointments/availability` | `appointment:read` | Optional `doctor_id` (required for Admin and read-only Nurse; a Doctor may omit or name only self). Returns `{doctor_id, timezone, default_duration_minutes, slot_increment_minutes, working_hours: [{weekday 0=Mon..6=Sun, start_time, end_time}], time_off: [{id, start_at, end_at, reason}]}`. A missing schedule returns defaults and empty hours. |
| PUT | `/api/v1/appointments/availability` | `appointment:manage_availability` | Replaces the doctor's schedule. Body `{default_duration_minutes (5–240), slot_increment_minutes (5–60), working_hours ≤21 intervals, time_off ≤100 rows}`. Intervals for one weekday must not overlap; end must be after start. Time off is UTC with an optional ≤200-char reason. Audited `appointment.availability.update`. |
| GET | `/api/v1/appointments/slots` | `appointment:read` | Required `date=YYYY-MM-DD` (today … +366), optional `doctor_id` (required for Admin and read-only Nurse). Returns slots at the configured increment across the day's working intervals: `{start_at, end_at, local_start, local_end, available, reason: available|past|booked|time_off|outside_hours}`. Slots are display/booking suggestions; `POST /appointments` revalidates. |
| POST | `/api/v1/appointments/interpret` | `appointment:write` | Optional `doctor_id` (required for Admin). Body `{text}` (3–2000 chars). Calls the LLM **with `tools=[]`** and returns a **non-persisted proposal**: resolved/ambiguous patient candidates, clinic-local `date`/`start_time`, UTC `start_at`/`end_at`, duration, type, notes, `missing_fields`, `warnings`, `can_confirm`, `model`, `ai_generated: true`. The server resolves patients through the existing read-only MCP tool `search_authorized_patients`, converts local time to UTC, and runs the same slot/conflict checks. It NEVER creates or mutates an appointment; confirmation is a separate `POST /appointments` with explicit doctor action and full revalidation. Rate limited 10/min per user. `503` when the LLM is disabled/unreachable; `403` on guardrail block; `400` for malformed text. Audited as `appointment.ai.propose` (metadata only, never the text). |
| POST | `/api/v1/voice/transcriptions` | `appointment:write` | `multipart/form-data` with `file` (audio) and `language` (`en` or `bg`). The global 1 MiB body limit is raised **only for this exact route** to `VOICE_MAX_UPLOAD_BYTES` (default 4 MiB; hard ceiling 8 MiB). Audio is streamed to a private temp directory, checked with FFprobe, converted with FFmpeg to 16 kHz mono PCM WAV, and sent to the private whisper.cpp service; `VOICE_MAX_DURATION_SECONDS` (default 60) bounds duration. Response `{text, language, model, duration_seconds}`. Audio and transcripts are never persisted, logged, or audited. `400` for unsupported/empty/corrupt/too-long audio; `429` when the per-user transcription limiter is saturated; `503` when speech recognition is not configured/unreachable. `text` is editable in the browser; final scheduling still requires an explicit appointment create. |

**Visit catalogue.** The appointment form offers 19 suggested visit types
(New Patient Consultation 45 min, General Consultation 30, Follow-up Visit 20,
Routine Check-up 30, Annual Physical Examination 45, Urgent Consultation 20,
Specialist Consultation 30, Preventive Care Visit 30, Chronic Disease
Management 30, Laboratory Results Review 15, Medication Review 20, Diagnostic
Evaluation 30, Vaccination 15, Medical Procedure 30, Pre-operative Assessment
30, Post-operative Follow-up 30, Telemedicine Consultation 20, Mental Health
Consultation 45, Other 30). The selected label is stored in the existing
`appointment_type` field; selecting **Other** reveals a required custom label
(≤100 chars), so the API remains a bounded free-text string rather than an
enum. The frontend updates the suggested duration when a type is selected.

**Voice/AI boundary.** Voice recognition and appointment interpretation are
separate steps. Speech recognition only turns audio into text and stays on the
private speech service; the interpreter only proposes fields and, when the LLM
is enabled, sends the edited transcript to the configured provider. With no
configured key or an explicit `LLM_ENABLED=false`, the interpreter fails closed
with `503` and makes no external call. Neither step can create, reschedule, or
cancel an appointment. The frontend uses `MediaRecorder` when available, stops
microphone tracks immediately, keeps audio in memory only, and always offers a
manual transcript editor. The model never receives a patient id, doctor id, or
UTC timestamp as an input it can choose.

### AI assistant (optional feature)

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/api/v1/ai/ask` | `patient:read` | Body `{ "patient_id", "question" }` (patient-scoped) or `{ "question" }` (general lookup). Response (both modes): `{ "answer", "used_tools", "citations", "model", "ai_generated": true }`. Rate limited (10/min per user, shared across both modes) and bounded (`question` 1–2000 chars, never blank). 401 anonymous; 403 without `patient:read`; 400 on malformed/oversized questions; 429 `rate_limited` with `Retry-After`; 503 `dependency_unavailable` when the LLM is disabled/unreachable. Every successful ask is **persisted to the caller's history** (below) in the same request lifecycle; the response shape is unchanged (no added keys). Supports `Idempotency-Key` (see "Idempotent mutations"): a replay returns the recorded answer with `Idempotency-Replayed: true` and does **not** call the LLM again. |
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
  patient. Requires `patient:read` **and** patient access; an inaccessible or
  unknown patient is `404 not_found`, never 403 (concealment). The model may
  execute only `get_patient_summary`, `get_patient_lab_results`, and
  `get_recent_prescriptions`, each bound to exactly the verified `patient_id`.
  Cross-patient lookup/panel tools and any mismatched patient argument are
  rejected by the server-side tool-scope guardrail before MCP execution.
- **General lookup** (`patient_id` absent): a question across the patients the
  caller is authorized for (e.g. "who is on metformin?"). The model may search
  patients and read their records via the same seven MCP tools under the same
  authorization; `citations` may reference multiple patients (`{type, id}`
  entries as in patient mode). Requires `patient:read` — users without clinical
  read access get `403`, exactly as for clinical reads.

Bounds differ only in the tool-round cap: patient-scoped questions allow
`LLM_MAX_TOOL_CALLS` tool calls (default 4), general-lookup questions
`LLM_MAX_TOOL_CALLS_GENERAL` (default **10**, env-driven, clamped to 1–20)
because a general answer typically needs a search plus follow-up reads. Both
modes keep the per-tool result caps, per-call timeouts, and the total wall-clock
budget.

Answers must distinguish tool-retrieved facts from general explanation; missing
facts are acknowledged, never invented. Multi-patient answers respect
authorization: only records the tools returned (i.e. records the caller may
read) may be reported, and each claim is cited to the records that support it —
the answer must list the records consulted. Record text is DATA, never
instructions. Output is labelled AI-generated. Every ask is audited (`ai.ask`,
resource `patient` with the patient id in patient mode, `ai_lookup` in general
mode) together with one `mcp.<tool>` audit event per tool call; prompts,
questions, and clinical payloads are never audited or logged. History mutations
audit as `ai.history.clear` / `ai.history.delete` (metadata only). Each
successful ask additionally persists its exchange (question, answer,
used_tools, citations, model, patient_id) to the caller's private history in the
same commit as its `ai.ask` audit event — one row per request, never
double-written on a single request lifecycle.

**Deterministic guardrails (2026-11-04).** In addition to the trusted
authorization boundary, `app/llm/guardrails.py` applies fail-closed checks:

- unsafe input (requests for system prompts, credentials/DSNs, SQL, shell,
  filesystem/database access, authorization bypass, or out-of-scope patients)
  is rejected before the provider is called;
- patient-scoped tool calls are restricted to the three single-patient tools
  and must carry exactly the verified `patient_id`;
- tool payloads are minimized before prompt insertion (record/citation IDs and
  clinically necessary fields retained; MRNs, raw dates of birth, and
  unnecessary foreign keys dropped from model context);
- output containing secret/DSN/token/private-key patterns, external URLs,
  raw HTML/script/event-handler payloads, internal prompt/config text, or
  citation IDs absent from authorized tool results is blocked.

A guardrail block is a generic `403 forbidden` (never a stack trace or echoed
input), writes only a metadata `ai.ask` denial, and does not persist the
exchange to history or idempotency storage.

**Answer format (Markdown subset, Bulgarian by default).** The `answer` string
is a small Markdown subset (headings `##`/`###`, **bold**/*italic*, lists,
pipe tables with a header separator row, and a trailing
`### Records consulted` section that the frontend folds into a collapsible
disclosure). The frontend renders it with a safe DOM-only Markdown renderer —
it never parses HTML, so any HTML in an answer is displayed as inert text.
Answers are in **Bulgarian by default** (клиничните данни са на български);
when the question is clearly in another language the answer follows the
question. Clinical flags appear in the answer's language (e.g. Bulgarian
"високо"/"ниско" with ↑/↓) while lab units and codes stay exactly as recorded.

## Permission matrix (source of truth: `backend/app/authorization/policy.py`)

Three roles only (`tech_admin` was removed from the system entirely — owner
decision, 2026-10-26). `analytics:read` was declared in advance of the
analytics page and is now enforced by `GET /api/v1/analytics/summary` (see
"Analytics", wave U 2026-10-27, expanded 2026-11-09).

| Permission | doctor | nurse | clinical_admin (display label: **Admin**) |
|---|---|---|---|
| `patient:read` | ✔ | ✔ | ✔ |
| `patient:write` | ✔ | ✖ | ✔ |
| `patient:delete` | ✖ | ✖ | ✔ |
| `message:read` | ✔ | ✖ | ✔ |
| `message:write` | ✔ | ✖ | ✔ |
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

Deny by default. `nurse` is least-privilege clinical read with **read-only**
clinic-wide appointments (`appointment:read` only; no staff/audit
administration, no appointment writes or availability management).
`clinical_admin` (Admin) holds **every** permission including clinical reads.
Doctors may read/write only their own calendar; the Admin may act across
doctors and must name `doctor_id` for scoped appointment actions. Nurses may
read the clinic-wide schedule and, for availability/slots, must name a doctor;
their write/availability/voice requests remain `403`. There are now **16**
permission keys (the seed/tests assert 16).

The MCP server remains at exactly **seven read-only tools**. Appointment
scheduling is deliberately not exposed as an MCP tool: the AI interpreter uses
`search_authorized_patients` only for patient resolution, and every mutation
goes through the permission-checked REST endpoints after explicit confirmation.

## MCP tool contract (internal, not a public HTTP API)

**Change record:** `get_my_recent_patients` added 2026-10-27 (wave W, doctor
panel tools); `get_patient_summary` extended the same day with the summary
payload fields documented above. Nothing else changed. Appointment scheduling
(2026-11-09) added **no** MCP tools — the interpreter reuses
`search_authorized_patients` for patient resolution only; the count stays seven.

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
