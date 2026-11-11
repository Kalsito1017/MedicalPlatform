# Troubleshooting

**Scope:** common failures in local development and in the deployed demo, with symptoms, likely causes, and fixes — plus how to gather diagnostics without leaking secrets.
**Status:** living document, updated as phases land. Written at Phase 1; entries are based on the behavior defined in `docs/API.md` and `.env.example`. Commands previously marked *(unverified until Phase 13)* are verified against the local Docker stack as of Phase 13.

---

## Quick triage

| Symptom | Jump to |
|---|---|
| Backend exits immediately at startup | [Missing environment variable](#1-missing-environment-variable-at-startup) |
| `alembic` errors | [Migrations fail](#2-migrations-fail) |
| Seed script creates duplicates | [Seed duplicates](#3-seed-duplicates) |
| Login fails with 401 or 403 | [Login 401/403 confusion](#4-login-401403-confusion) |
| Browser console: CORS / blocked response | [CORS errors](#5-cors-errors) |
| Requests seem unauthenticated despite logging in | [Cookie not sent](#6-cookie-not-sent) |
| Site "down" after being idle | [Cold start](#7-render-cold-start-looks-like-downtime) / [Autosuspend](#8-neon-autosuspend-latency) |
| AI feature returns 403 or 503 | [LLM key/plan/quota](#9-llm-401402403429-key-plan-quota-or-model) / [LLM 503](#10-llm-503-when-disabled-or-unreachable); a generic `403 forbidden` immediately after submission can also be an intentional AI guardrail denial — do not retry with a broader prompt or different patient id |
| `docker compose up` fails | [Port conflicts](#11-docker-compose-port-conflicts) |
| `uv sync` / import errors | [Python version mismatch](#12-uvpython-version-mismatch) |

---

## 1. Missing environment variable at startup

**Symptoms:** backend refuses to start; log line naming a missing/invalid setting (by
name, never by value); readiness never becomes healthy.

**Cause:** required env var unset or still a placeholder. Startup validation
(`backend/app/config.py`, Phase 2) fails fast on purpose.

**Fix:**
1. Compare your `.env` (local) or Render dashboard env (deployed) against `.env.example`.
2. Set every required variable. Placeholder values like `change-me-…` are detected and rejected.
3. Restart the service. Locally: `cd backend && uv run uvicorn app.main:app --reload`
   (or `docker compose restart app`, which re-runs the entrypoint first).
4. Check `/health/ready` — it must return 200 once config and DB are OK.

Never paste real values into an issue or chat. Report the **variable name** only.

## 2. Migrations fail

**Symptoms:** `uv run alembic upgrade head` errors — version conflict, "relation already
exists", or connection/authentication failure.

**Fix:**
1. Connection problem? Verify `DATABASE_URL` / `MIGRATION_DATABASE_URL` (TLS required for
   Neon; the URL must be the `postgresql+asyncpg://` form). Confirm the Neon project is
   not suspended (first connection after idle can be slow — retry once).
   In Docker the entrypoint runs `alembic upgrade head` on every container start; the
   one-shot equivalent is `docker compose run --rm app python -m alembic -c alembic.ini
   upgrade head`.
2. "Already exists": the database was partially migrated. Inspect
   `alembic current` vs `alembic heads`. For synthetic demo
   data the simplest fix is a **database reset** (drop schemas → `alembic upgrade head` →
   reseed; see `docs/DEPLOYMENT.md` §9).
3. Permission denied: migrations may need the `migrator` role
   (`MIGRATION_DATABASE_URL`), not the least-privilege app role.
4. Never run migrations as the database superuser.

## 3. Seed duplicates

**Symptoms:** running `scripts/seed_demo_data.py` twice creates duplicate patients/users.

**Fix:** the seed must be idempotent or support an explicit reset flag *(documented by
the script when it lands in Phase 3 — check `--help`)*. If you hit duplicates anyway:
reset the database (drop schemas → migrations → seed) per `docs/DEPLOYMENT.md` §9.
Report the non-idempotent behavior as a bug with the script's `--help` output.

## 4. Login 401/403 confusion

Know the difference (`docs/API.md`):

- **`401 unauthenticated`** — no session, or the session expired (`SESSION_TTL_HOURS`,
  default 8). Login again. A stale tab after logout/expire is the usual cause.
- **`403 forbidden`** — you are logged in but the role lacks the permission (see the
  matrix in `docs/SECURITY.md`), **or** the request failed CSRF validation (missing
  `X-CSRF-Token` on a non-GET request), **or** it is the self-escalation guard.
- Login endpoint itself returns a **uniform** failure for unknown email vs wrong
  password by design — that is not a bug. Rate limiting returns `429 rate_limited`;
  wait for `Retry-After` seconds.

**Fix:** call `GET /api/v1/auth/me` to see the current user's roles and effective
permissions. If roles look wrong, check `admin.user_role` (or ask an admin).

## 5. CORS errors

**Symptoms:** browser console: "blocked by CORS policy"; the API works via `curl` but not
from the UI.

**Fix:**
1. `ALLOWED_ORIGINS` on the backend must contain **exactly** the origin the browser is
   on — scheme + host + port must match (`https://<frontend-domain>` in production;
   `http://localhost:5173` or `http://localhost:8000` locally per `.env.example`).
2. After changing it, restart the backend (env changes need a restart).
3. No wildcards (`*`) in production; no trailing slash or path in origin values.
4. Preflight (`OPTIONS`) failures usually mean a custom header (e.g. `X-CSRF-Token`) is
   not in the allowed headers — the backend configures this; report it as a bug if
   preflight fails with the correct origin.

## 6. Cookie not sent

**Symptoms:** every request returns `401` immediately after a successful login; the
session cookie seems present in devtools on the login response but not on later requests.

**Causes and fixes:**
- **`Secure` attribute:** cookies are only sent over HTTPS (or `http://localhost`).
  If you serve the frontend over plain HTTP on a non-localhost host (e.g.
  `http://192.168.x.x:5173` from a phone), the cookie is dropped. Use HTTPS or
  `localhost` tunneling.
- **`SameSite=Lax`:** cross-site requests do not carry the cookie. If the frontend
  origin differs from the API origin *and* is treated as a different site (different
  registrable domain), the browser will not send it. Production is one frontend origin
  + one API origin over HTTPS; ensure `PUBLIC_ORIGIN` and `ALLOWED_ORIGINS` are set to
  the real frontend origin.
- **Origin mismatch:** logged in against `localhost` but browsing `127.0.0.1` (or vice
  versa) — different origins, cookie path/domain mismatch. Pick one hostname and stick
  to it.
- **CSRF:** the cookie may be sent fine but the missing `X-CSRF-Token` header on a
  non-GET request yields `403`. Check the request headers.

## 7. Render cold start looks like downtime

**Symptoms:** the site/API hangs for ~30–60 s after being idle, then works normally.
No error responses.

**Cause:** Render free web services sleep after inactivity. This is expected and
documented; it is not an outage.

**Fix/wait:** reload after the first slow request. Do not "fix" this by adding
complexity; if it matters, a paid tier or an external pinger (with the caveats noted in
`docs/BACKLOG.md`) is the answer. Record observed cold-start time in the deployment log.

## 8. Neon autosuspend latency

**Symptoms:** first request after idle is slow or times out; subsequent requests are
fast; `/health/ready` may briefly fail while the database wakes.

**Cause:** Neon free tier suspends the compute after inactivity.

**Fix:** retry once. For the readiness probe, allow a retry/warm-up window. If timeouts
persist, check the Neon dashboard for compute state and quota exhaustion.

## 9. LLM 401/402/403/429: key, plan, quota, or model

**Symptoms:** `POST /api/v1/ai/ask` or `/api/v1/appointments/interpret` returns
`503 dependency_unavailable` (or the UI shows the AI-unavailable state). The API
message now identifies the account problem; provider response bodies and keys are
never echoed.

- **401 invalid key** — `LLM_API_KEY` is rejected. Put the service-account key in
  `backend/.env` (Docker) and restart/recreate the app container.
- **402 insufficient funds** — the standard `https://opencode.ai/inference` account
  has no credit. Add credit or use a free-quota model.
- **403 Go subscription required** — `LLM_API_BASE_URL` points at
  `https://opencode.ai/inference/go` without an active Go plan. Either activate Go
  or use the standard endpoint with a funded model.
- **429 free usage limit** — free models have a usage cap. Use a paid model with
  credit or an active Go plan; this is not a transient rate limit.
- **404/400 model or routing error** — check `LLM_MODEL` and use the standard base
  URL; the provider always sends an `x-opencode-session` header.

Key-only setup: with `LLM_API_KEY` present, the LLM auto-enables even if
`LLM_ENABLED` is omitted. `LLM_ENABLED=false` is the explicit kill switch; a
placeholder/blank key is treated as unconfigured. The voice path is separate:
Whisper transcribes audio locally, then the editable transcript is sent to
`/appointments/interpret` only when you press **Propose appointment**.

## 10. LLM 503 when disabled or unreachable

**Symptoms:** `POST /api/v1/ai/ask` returns `503 dependency_unavailable`; the UI shows
the "AI unavailable" state; everything else works.

**This is correct behavior**, not an outage: the LLM is an optional dependency and never
gates readiness (`/health/ready` stays 200). Check `LLM_API_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY`, and provider account status. The endpoint is also rate limited (`429`),
bounded, and time-limited by design.

## 11. docker compose port conflicts

**Symptoms:** `docker compose up` fails with "port is already allocated" (Postgres
default `5432`, or the app port `8000`/`5173`).

**Fix:**
1. Find the occupant: `ss -ltnp | grep 5432` (or the conflicting port).
2. Stop the conflicting local Postgres/service, or change the published port mapping in
   `docker-compose.yml` locally (e.g. `5433:5432`) and update `DATABASE_URL` to match
   (`POSTGRES_PORT` and `APP_PORT` control the published ports).
3. Compose service names are `db` and `app`; the app connects to the database as the
   `POSTGRES_*` role on host `db` (see `docker-compose.yml`). Keep the host-side
   `DATABASE_URL` in `backend/.env` (used by `uv run`, host `localhost`) in step with
   those credentials.

## 12. uv/Python version mismatch

**Symptoms:** `uv sync` resolves oddly, native wheels build from source (slow/failing on
`asyncpg`, `greenlet`, `argon2-cffi`), or imports fail with syntax/version errors.

**Fix:**
1. The project targets **Python 3.12** (Docker image `python:3.12-slim`). Check
   `uv python list` and let `uv` manage 3.12: `uv python install 3.12`.
2. Re-sync: `cd backend && uv sync`.
3. Avoid system Python 3.11/3.13/3.14 mismatches; `uv` pins the interpreter per
   project.
4. In Docker, rebuild: `docker compose build --no-cache`.

---

## 13. Voice button is missing, unsupported, or microphone denied

1. The panel is shown only with `appointment:write` (Doctor/Admin). A nurse gets
   `403` from the API by design.
2. `getUserMedia`/`MediaRecorder` require a secure context: `https://` or
   `http://localhost`. A plain-HTTP LAN address will not prompt.
3. If the browser says the feature is unavailable, use the transcript textarea;
   manual scheduling never depends on the microphone.
4. If permission was denied, reset it in the browser's site settings. The app
   cannot re-prompt after a hard denial.
5. Confirm the edge/proxy preserves `Permissions-Policy: microphone=(self)`.
   Cloudflare Transform Rules or a custom `_headers` file can silently override
   the backend header on Pages-hosted pages.
6. Check that no other tab/app is holding the microphone and that the OS privacy
   setting allows the browser.

## 14. Voice transcription returns 503

- `WHISPER_BASE_URL` is empty or the speech container is not running. Set it to
  the private service origin (local compose: `http://speech:8080`) and restart
  the backend.
- From inside the compose network:
  `docker compose exec speech curl -fsS http://127.0.0.1:8080/ >/dev/null`.
- `WHISPER_AUTH_MODE=bearer` without a non-empty `WHISPER_BEARER_TOKEN`, or
  `gcp_iam` without `WHISPER_AUDIENCE`, fails startup validation. Fix the env
  and restart; never paste the token into an issue.
- A timeout is a `503` by design. Check CPU/threads; Tiny on CPU is small but
  the first request loads the model.
- The speech port is intentionally not published; do not expect
  `http://localhost:8080` to answer from the host.

## 15. Voice transcription returns 400

- Unsupported/empty/corrupt audio: record again or use a WAV/WebM/OGG/MP4 file.
- Longer than `VOICE_MAX_DURATION_SECONDS` (default 60): record a shorter
  request or type it.
- Over the exact-route allowance (`VOICE_MAX_UPLOAD_BYTES`, default 4 MiB; hard
  cap 8 MiB): the global 1 MiB limit still applies to every other route.
- FFmpeg/FFprobe missing outside Docker: install FFmpeg on the host, or run the
  backend image (it installs them). The error stays generic on purpose.

## 16. Appointment create returns 409 or 400

- `409 conflict`: the doctor or the patient already has an overlapping
  appointment, or the doctor has time off. Refresh slots; the calendar may be
  stale.
- `400 validation_error`: outside working hours, in the past, duration outside
  5–480, crossing the local day boundary, or a DST hole/fold in
  `Europe/Sofia`.
- Availability looks duplicated: re-run the seed? It is idempotent and will not
  remove manually created intervals. Use the Availability dialog to replace the
  day's intervals; the API replaces the whole schedule atomically.
- A doctor must not send another doctor's `doctor_id`; that is `403`. An Admin
  must send one for scoped availability/slots/interpret.

---

## 17. System Logs stays Disconnected / keeps reconnecting

- **401/403:** confirm you are signed in as Admin with `logs:read`; Doctor
  and Nurse are denied by design. A revoked/deactivated session produces
  `event: auth_expired` and closes the stream — reload after signing in again.
- **Proxy buffering:** SSE needs an unbuffered response. The API sends
  `Cache-Control: no-cache` and `X-Accel-Buffering: no`; verify any proxy/CDN
  in front of the app does not buffer or compress the stream. Local Docker
  Compose serves the API directly.
- **Migration not applied:** apply `alembic upgrade head` (System Logs adds
  `admin.system_event` and the `logs:read` permission). Sync the backend image
  and restart the app after a schema change.
- **Backend restarted:** the in-process bus is intentionally not durable. The
  page reconnects with its last event id and replays from the database; events
  written while the server was down are still visible after reconnect if they
  fall inside the selected 7-day window.
- **Multiple workers/instances:** live events are published only inside the
  worker process that handled them. The current deployment is single-process.
  Multi-worker deployments need PostgreSQL `LISTEN/NOTIFY` or another bounded
  broker; historical reads still work.

## 18. System Logs shows zero events or unexpected counters

- The default period is **last 15 minutes**. Choose a wider period or a custom
  range (maximum 7 days).
- Counters describe **all backend events matching the selected period and
  filters**, not just the visible page. Clearing the view or exporting does not
  change them.
- Audit events (login, patient/lab access, staff changes) and operational
  events (AI/voice, appointment conflicts, API errors, startup) share the
  stream but have separate tables and retention. A fresh install has no
  historical rows until activity occurs.
- The search box matches event type, resource type/reference, request id and
  approved flat metadata values only; it does not search
  clinical text, bodies, transcripts or arbitrary payloads.
- If history loads but the stream is disconnected, the page is showing the
  historical snapshot; do not treat it as live. Fix the connection first.

## 19. System Logs export or copy is empty

- **Export CSV** exports the rows currently loaded in the browser after the
  current filters, not the entire server-side history. Apply a wider period or
  load more history first. Exports contain only displayed safe fields.
- **Copy visible** requires a visible chat message and browser clipboard
  permission; HTTP pages may block clipboard access outside localhost.
- **Clear View** only empties the browser display/buffer. It never deletes
  backend audit or operational rows.

## 20. A clinical action succeeded but no System Logs entry exists

- Operational logging is best-effort: a failed operational write must not fail
  a clinical request. Check backend logs for an operational-event write warning.
- Coverage is explicit, not universal: authentication/authorization, patient,
  lab, prescription, staff, role and appointment *audit* actions are mapped to
  the stream; AI/voice/MCP, appointment conflicts/rejections, API 5xx/429s and
  startup have operational instrumentation. There is no continuous dependency
  health prober or background-job monitor in this demo. Do not infer service
  health from the absence of an error event.

## Getting diagnostics safely

- **Log locations:**
  - Local backend: terminal running `uv run uvicorn …`, or `docker compose logs -f app`.
  - Local containers: `docker compose logs -f app db`.
  - Render: dashboard → service → Logs.
  - Frontend (Cloudflare Pages): dashboard → Pages → deployment logs (static hosting;
  the interesting logs are browser console + backend).
- **Request correlation:** every API response carries `X-Request-Id`; the same id
  appears in server logs and in `error.request_id` of the JSON error envelope. Include
  this id in bug reports — it is the fastest path to the matching log lines.
  When reporting, also include: timestamp (with timezone), endpoint + method, HTTP
  status, and the error `code` from the envelope.
- **What must be redacted before sharing anything:**
  - Passwords, password hashes, `DEMO_*` values.
  - Session cookies, CSRF tokens, `Authorization` headers.
  - LLM API keys (`oc_sk_…`) and database connection strings (usernames/hosts included).
  - Full clinical payloads — even synthetic ones, if logs are being posted publicly,
  share only the smallest relevant excerpt.
  - Environment variable **values**; names are fine.
- Structured logs are redacted by the application (Phase 11), but treat anything you
  export manually as sensitive and scrub it yourself before pasting.
