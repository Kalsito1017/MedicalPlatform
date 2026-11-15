# System Logs

Admin-only, real-time operational monitoring for MediCore. The page presents
the durable security audit trail and the bounded operational event stream as
one chronological chat-style activity feed, while keeping their storage, retention and purpose
separate. It is **read-only**: there is no shell, SQL console, command input,
container console or server-control surface anywhere in this feature.

## 1. Audit versus operational events

| | Audit events | Operational events |
|---|---|---|
| Table | `admin.audit_event` | `admin.system_event` |
| Purpose | accountability and security evidence | application/dependency monitoring |
| Written by | existing `write_audit_event` in the caller's transaction | `record_operational_event` after the clinical operation |
| Commit behaviour | committed with the business change; a failed audit write is not silently swallowed | best-effort separate transaction; failure logs a server warning and never fails the clinical request |
| Retention | demo lifetime; no automated deletion | **7 days**, pruned best-effort when an operational event is written |
| Live delivery | published to the in-process bus only **after commit** | published after its own commit |
| Access | `audit:read` (existing Audit Logs page) | `logs:read` (System Logs) |
| Duplication | never copied into `system_event` | never written into `audit_event` |

The unified read model merges both sources at query time. It does not create a
second copy of an audit row, and it does not weaken the existing audit API.

## 2. Authorization

- `logs:read` is a separate Admin-only permission. It was added because the
  broader operational stream is not the same assurance surface as
  `audit:read`; the durable audit page remains independently protected.
- Role matrix: Doctor ✖, Nurse ✖, Admin (`clinical_admin`) ✔. Deny by default.
- `GET /api/v1/admin/logs` and `GET /api/v1/admin/logs/stream` both enforce the
  permission server-side: anonymous → `401`, authenticated non-Admin → `403`.
  Hidden sidebar navigation is not a control.
- The SSE generator re-resolves the session and permission periodically (at
  least every heartbeat interval, and also on busy streams). Revocation,
  expiry, deactivation or permission removal produces
  `event: auth_expired` and closes the connection.
- The frontend additionally hides the page from non-permitted users and renders
  an honest denial panel, but every request would still be rejected by the
  backend.

## 3. Event catalogue

The registry is `backend/app/logs/registry.py`. Only approved event types are
accepted; arbitrary names raise before persistence. Severity/category come from
the registry, not from callers.

### Audit-derived events

These map existing audit actions into log categories. Denied outcomes become
`security`; error outcomes become `error`. An unknown audit action maps to the
generic `system.audit_event` with a bounded action string.

| Event type | Category | Event types included |
|---|---|---|
| Authentication | `auth` / `security` | `auth.login.success`, `auth.login.failure`, `auth.logout` |
| Security | `security` | `authz.denied`, `auth.login.failure`, `appointment.denied`, `mcp.unknown_tool`, `ai.ask` denied |
| Patients | `patients` | `patient.read`, `patient.suggest`, `patient.summary.read`, `patient.create`, `patient.delete` |
| Encounters | `encounters` | `patient.read` when the audited resource type is `encounter` (event type remains `patient.read`; description is “Encounter accessed”) |
| Laboratories | `labs` | `labs.read` |
| Prescriptions | `prescriptions` | `prescriptions.read` |
| Appointments | `appointments` | `appointment.create`, `appointment.update`, `appointment.reschedule`, `appointment.cancel`, `appointment.availability.update`, `appointment.ai.propose`, `appointment.remove_from_calendar` |
| AI / MCP | `ai` / `mcp` | `ai.ask`, `ai.history.clear`, `ai.history.delete`, `mcp.<tool>` → `mcp.tool.call`, `mcp.unknown_tool` |
| Administration | `administration` | `staff.create`, `staff.update`, `staff.deactivate`, `role.grant`, `role.revoke`, `session.revoke_all`, `analytics.read`, `message.send`, `message.read`, `message.attachment.download` |

The existing audit inventory is covered by this mapping; the system does not
claim that every application code path has an audit event. In particular,
successful voice transcription is intentionally **not** audited; its safe
outcome metadata is operational only.

### Operational events

| Event type | Category | Severity | Emitted when |
|---|---|---|---|
| `ai.request.started` | `ai` | info | AI question passed auth/rate limiting and provider work starts |
| `ai.request.completed` | `ai` | success | AI response returned; duration/model/tool count only |
| `ai.request.failed` | `ai` | error | guardrail, provider rejection, tool-loop or internal failure |
| `ai.request.timeout` | `ai` | error | provider timeout |
| `ai.request.unavailable` | `ai` | error | provider unreachable/disabled/unavailable |
| `ai.request.rate_limited` | `ai` | warning | provider rate limit |
| `voice.transcription.started` | `voice` | info | valid transcription request begins |
| `voice.transcription.completed` | `voice` | success | transcription returned; language/duration/model only |
| `voice.transcription.failed` | `voice` | error | internal transcription failure |
| `voice.transcription.unavailable` | `voice` | error | speech service unreachable/not configured/rejected |
| `voice.transcription.timeout` | `voice` | error | speech service did not respond before its configured timeout |
| `voice.transcription.validation_failed` | `voice` | warning | audio validation/conversion failed |
| `voice.transcription.rate_limited` | `voice` | warning | per-user voice rate limit |
| `appointment.conflict` | `appointments` | warning | create/update rejected by a scheduling conflict or closed appointment |
| `appointment.create_rejected` | `appointments` | warning | create rejected by validation (past/outside hours/duration/etc.) |
| `system.startup` | `system` | info | application lifespan starts (best-effort background write) |
| `system.api_error` | `system` | error | unhandled 5xx or application 5xx response; method/route/status/error code only |
| `system.rate_limited` | `system` | warning | API-level `429` (including login/AI/voice limiters) |
| `system.dependency_unavailable` | `system` | error | reserved for future dependency monitoring; not currently emitted |

There is **no continuous dependency health prober** and no background-job
runner in this demo. The absence of an error event is not proof that a
dependency is healthy. AI/voice availability is observed only when a request
actually exercises the dependency.

### Categories and severities

Categories: `auth`, `security`, `patients`, `encounters`, `labs`,
`prescriptions`, `appointments`, `ai`, `mcp`, `voice`, `administration`,
`system`. All twelve are backed by at least one registry event.

Severities: `info`, `success`, `warning`, `error`, `security`. The UI uses
neutral/blue for info, green for success, amber for warning, red for error and
purple/violet for security; the stored value is authoritative.

## 4. SSE implementation

Endpoint: `GET /api/v1/admin/logs/stream` (same filters as history).

1. The route authorizes `logs:read` before returning a `StreamingResponse`.
2. The generator subscribes to the bounded in-process bus **before** running the
   replay query, so an event committed during replay is not lost.
3. Replay reads persisted audit + operational rows after the cursor
   (`last_event_id` query parameter or `Last-Event-ID` header), bounded to 200
   matching events, delivered chronologically. No cursor → the most recent events in the selected
   window.
4. Live frames use:
   ```text
   id: op:<uuid>
   event: system
   data: {"id":"op:…","timestamp":"…","severity":"info", …}

   ```
   Heartbeats are `: heartbeat` comments every 15 seconds.
   `event: auth_expired` carries `{"reason":"session_expired"}` and closes.
5. Exact event IDs are deduplicated across replay/live. Ordering is
   `(timestamp, source-prefixed event id)`; history is newest first and replay
   is oldest first. The client keeps the chat feed sorted by the same key.
6. Subscribers have a bounded queue (500 events, oldest dropped). Slow or
   disconnected browsers cannot grow memory without bound; the database remains
   the source of truth.
7. The generator cleans up its subscription in `finally`. It never holds a
   database session open: replay and periodic auth checks each use a short-lived
   session.
8. Multiple Admin browser sessions each get an independent subscription and
   receive the same events.

Browser reconnection: the client keeps the last received event id, closes a
failed `EventSource`, and reconnects with exponential backoff (1 s → 30 s
cap), passing `last_event_id`; deduplication prevents repeated lines. When the
page is paused or scrolled up, incoming events continue to arrive and are
buffered in the browser (max 500), with a “N new events — Resume” indicator.

## 5. Historical queries and filters

`GET /api/v1/admin/logs` supports:

- `start` and `end`: timezone-aware ISO-8601, both or neither. Default last 15
  minutes; maximum span 7 days.
- `severity`: `info` | `success` | `warning` | `error` | `security`.
- `category`: one of the twelve categories.
- `event_type`: an approved registry type (history and stream reject unknown
  values).
- `actor_id`: staff user UUID.
- `request_id`: correlation id.
- `outcome`: `success` | `denied` | `error` | `info` | `warning`.
- `q`: bounded search over event type, resource type/reference, request id and
  approved flat metadata values (`language`, `model`,
  `provider`, `status`, `error_code`, `tool`, `duration_ms`, etc.).
- `limit` 1–100 (default 50), `offset` 0–500.

Counters (`total`, `info`, `success`, `warning`, `error`, `security`) come from
grouped SQL over both sources for the **whole selected filtered period**, not
from the visible page and not from client-side arithmetic. The API returns the
configured clinic timezone; timestamps remain UTC on the wire and the chat
feed renders them in `CLINIC_TIMEZONE` (UTC if omitted).

## 6. Metadata schema and redaction

Operational metadata is a flat JSONB object. Only these keys survive validation;
at most 12 keys, strings truncated to 120 characters, finite numbers rounded to
3 decimals:

`action`, `language`, `duration_ms`, `audio_duration_seconds`, `model`,
`provider`, `status`, `error_code`, `tool`, `mode`, `tool_calls`, `permission`,
`reason`, `method`, `route`, `http_status`, `count`.

Unknown keys, nested objects/lists, non-finite floats and oversized strings are
dropped. Event types must be registry-approved; categories/severities are
derived from the registry.

Never recorded or displayed:

- passwords/hashes, access/refresh tokens, session cookies, CSRF tokens, API
  keys, connection strings, authentication headers;
- patient names, MRNs, dates of birth, full records, lab values, prescription
  doses/instructions, clinical notes;
- AI questions/answers, prompts/completions, voice transcripts, raw audio;
- full request/response bodies or raw exception text/stack traces.

Operational error events store safe codes and correlation IDs, not payloads.
Audit events that target an email resource hide the email reference in the
System Logs read model (`resource_reference: null`). The UI renders all values
as text nodes — never `innerHTML` — and remains under the existing CSP.

## 7. Frontend

`frontend/system-logs.html` + `frontend/assets/js/system-logs.js`:

- connection badge: Connected / Reconnecting / Disconnected (never claims live
  while disconnected), last connected and last event times;
- compact counters for the selected filtered period;
- severity/category/period/custom-range/actor/event-name/request-id/outcome
  filters and approved-metadata search;
- chat bubbles with timestamp (milliseconds), severity, event type, description,
  sender display name/role, request id, resource and safe metadata chips;
- sender avatars resolved from the Admin staff directory: the dedicated seeded
  Admin/Doctor/Nurse demo portraits, existing demo doctor portraits for other
  doctors, colored initials for other staff, and a system avatar for events
  without an actor;
- day separators (Today / Yesterday / full date) and grouped message styling;
- chronological insertion, restrained arrival behavior and reduced-motion
  support via the shared CSS;
- Pause/Resume and Auto-scroll; manual upward scroll disables forced follow;
- Clear View (browser-only, behind an “Are you sure?” confirmation that states
  persisted records are not deleted), Copy visible, bounded CSV export of the
  currently loaded filtered rows, and event details dialog;
- bounded client buffer (500 events) and an accessible event-details view.

The page is a normal messaging-style activity conversation, matching the
MediCore light/dark theme rather than a fixed dark terminal. It behaves like a
group chat: sender profile pictures, names, bubbles, timestamps and system
notices. Times are labelled with the configured clinic timezone.

## 8. Retention and storage

- **Audit:** retained for the lifetime of the demo database; no automated
  deletion. Resetting demo data clears it as documented in
  `docs/DEPLOYMENT.md`. This change does not silently delete existing audit
  history.
- **Operational:** 7 days. Expired rows are deleted best-effort during an
  operational write. If no operational writes occur, expired rows can remain
  physically present briefly, but the API never reads outside its 7-day maximum
  window. A future deployment should add a scheduled maintenance job.
- Both tables are indexed on `created_at`; operational events also have
  severity/category/event_type/actor composite indexes. Query windows, row
  limits and offsets are bounded.
- The live bus is memory-only and not a retention mechanism.

## 9. Local development and verification

```bash
# from the repository root
cd backend && uv run alembic upgrade head
cd .. && uv run python scripts/seed_demo_data.py
cd backend && uv run uvicorn app.main:app --reload --port 8000
# serve frontend/ statically (same origin or the configured CORS origin)
```

Open `system-logs.html` as the demo Admin. Trigger activity (sign in/out,
patient read, AI question, appointment create/cancel, voice transcription) and
watch it arrive without refreshing.

Useful tests:

```bash
cd backend && uv run pytest -q tests/test_system_logs.py \
  tests/test_system_log_bus.py tests/test_system_logs_sse.py
cd frontend && node tests/smoke.mjs && node tests/render-smoke.mjs
```

The SSE tests drive the real route generator because `httpx.ASGITransport`
buffers complete response bodies and cannot consume an infinite stream. HTTP
tests still cover 401/403 for both endpoints.

## 10. Known limitations

- **Single process only:** the live bus is an in-process `asyncio.Queue` set.
  Multi-worker/multi-instance deployments would miss events published by other
  workers. Historical reads still work; live fan-out needs PostgreSQL
  `LISTEN/NOTIFY` or another bounded broker.
- **Replay bound:** at most 200 events after a cursor; a client disconnected
  long enough to miss more should reload history (the UI reloads on filter
  changes and on a fresh page load).
- **Operational retention is fixed at 7 days** and cleanup is write-triggered,
  not a scheduled job.
- **Instrumentation is explicit, not universal.** There is no continuous
  dependency prober, database connection monitor or background-job runner; API
  errors are logged for handled 5xx/429 failures and unhandled exceptions, not
  every 4xx. Successful voice transcription has no audit row by design.
- **CSV export** covers the rows currently loaded in the browser (up to the
  bounded client buffer), not an unbounded server-side export. The page loads
  the newest 100 history rows per filter change; the API supports `limit`/
  `offset` for deeper historical paging.
- **Sender identity** is resolved from the bounded staff directory request
  (100 accounts). Actors outside that page or deleted accounts fall back to
  “Staff member” with initials; email addresses are never displayed.
- **No shell, by design.** Monitoring only — the feature cannot execute
  commands, query SQL, read files or inspect environment variables.
- A future real-patient deployment requires a new privacy/security/regulatory
  review before this operational stream is enabled against real data.
