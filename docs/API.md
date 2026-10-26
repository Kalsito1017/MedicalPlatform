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
| GET | `/api/v1/patients/{patient_id}/summary` | `patient:read` | Synthetic summary: encounters, active problems, latest labs, active meds. |
| GET | `/api/v1/patients/{patient_id}/labs` | `lab:read` | Paginated. `?limit=` bounded. |
| GET | `/api/v1/patients/{patient_id}/prescriptions` | `prescription:read` | Paginated. |
| GET | `/api/v1/encounters/{encounter_id}` | `patient:read` | Returns 404 (not 403) when concealed. |

Every clinical payload includes `"provenance": "synthetic"` and the UI renders a persistent
"Synthetic demo data — not for clinical use" notice.

### Staff & roles (admin)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/staff` | `staff:read` | Paginated; never returns password hashes or tokens. |
| POST | `/api/v1/staff` | `staff:manage` | Create account. Password set via seeded env or invite flow. |
| PATCH | `/api/v1/staff/{user_id}` | `staff:manage` | Display name, activate/deactivate. |
| POST | `/api/v1/staff/{user_id}/roles` | `role:assign` | `{ "role": "nurse" }`. Self-escalation → 403. |
| DELETE | `/api/v1/staff/{user_id}/roles/{role}` | `role:assign` | Last-admin protection → 409. |

### Audit

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/audit` | `audit:read` | Paginated audit events: actor, action, resource ref, outcome, timestamp, request id. No clinical payloads, no secrets. |

### AI assistant (optional feature)

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/api/v1/ai/ask` | `patient:read` + patient access | `{ "patient_id", "question" }` → `{ "answer", "used_tools", "citations", "model", "ai_generated": true, "disclaimer" }`. Rate limited and bounded. 503 `dependency_unavailable` when LLM is disabled/unreachable. |

Answers must distinguish tool-retrieved facts from general explanation; missing facts are
acknowledged, never invented. Output is labelled AI-generated and is not a diagnosis or
treatment recommendation.

## Permission matrix (source of truth: `backend/app/authorization/policy.py`)

| Permission | doctor | nurse | clinical_admin | tech_admin |
|---|---|---|---|---|
| `patient:read` | ✔ | ✔ | ✖ | ✖ |
| `lab:read` | ✔ | ✔ | ✖ | ✖ |
| `prescription:read` | ✔ | ✔ | ✖ | ✖ |
| `staff:read` | ✖ | ✔ | ✔ | ✔ |
| `staff:manage` | ✖ | ✖ | ✔ | ✔ |
| `role:assign` | ✖ | ✖ | ✔ | ✔ |
| `audit:read` | ✖ | ✖ | ✔ | ✔ |

Deny by default. Technical administration grants **no** clinical-data access.

## MCP tool contract (internal, not a public HTTP API)

Tools: `search_authorized_patients(query, limit)`, `get_patient_summary(patient_id)`,
`get_patient_lab_results(patient_id, limit)`, `get_recent_prescriptions(patient_id, limit)`.

- Read-only, typed arguments, bounded results, explicit timeouts.
- Acting identity comes from the verified server-side session context — **never** a tool argument.
- Authorization is re-executed inside the tool layer on every call via the same policy module as the REST API.
- No `execute_sql`, no shell, no filesystem, no generic database access. Ever.
