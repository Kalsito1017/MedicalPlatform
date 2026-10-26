#!/usr/bin/env bash
# End-to-end smoke test for the Clinical AI Platform (synthetic-data demo).
#
# Validates the full workflow against a running stack (Docker or local
# `uv run`): health endpoints, single-origin frontend + security headers/CSP,
# login with the cookie + CSRF flow, clinical reads, the documented
# AI-unavailable path (LLM disabled -> 503 dependency_unavailable), role
# boundaries, logout -> 401, the audit trail, a container-log secret scan, and
# data persistence across `docker compose restart app`.
#
# Usage:
#   scripts/e2e_smoke.sh                      # http://localhost:8000, docker steps on
#   scripts/e2e_smoke.sh --url http://localhost:8000 --no-docker
#
# Environment:
#   E2E_BASE_URL    base URL of the app origin (default http://localhost:8000)
#   E2E_ENV_FILE    env file holding the DEMO_* credentials (default backend/.env)
#   E2E_COMPOSE     compose command for docker steps (default "docker compose")
#   E2E_NO_DOCKER   set to 1 to skip the docker-only steps (log scan, restart)
#
# Exit code 0 only when every step passes. Credentials are read from the env
# file and never printed; response bodies that could carry a generated password
# are parsed, not echoed.
set -uo pipefail

BASE_URL="${E2E_BASE_URL:-http://localhost:8000}"
ENV_FILE="${E2E_ENV_FILE:-backend/.env}"
COMPOSE_CMD="${E2E_COMPOSE:-docker compose}"
DOCKER_STEPS=1
[ "${E2E_NO_DOCKER:-0}" = "1" ] && DOCKER_STEPS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --url) BASE_URL="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --compose) COMPOSE_CMD="$2"; shift 2 ;;
    --no-docker) DOCKER_STEPS=0; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

FAILURES=0
pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; FAILURES=$((FAILURES + 1)); }

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# --- helpers ---------------------------------------------------------------

# json_eval '<python expression over d>'  (JSON on stdin; prints one value)
json_eval() {
  python3 -c "import json, sys
d = json.load(sys.stdin)
print($1)"
}

# env_value KEY -> value from the env file (quotes stripped; never echoed here)
env_value() {
  sed -n "s/^${1}=//p" "$ENV_FILE" | tail -n 1 | sed -e "s/^['\"]//" -e "s/['\"][[:space:]]*$//" -e "s/[[:space:]]*$//"
}

# csrf_token JAR -> the csrf_token cookie value held in a curl cookie jar
csrf_token() {
  [ -f "$1" ] || return 0
  awk '$6 == "csrf_token" { value = $7 } END { print value }' "$1"
}

# login JAR EMAIL PASSWORD_VAR -> status code (body saved to $WORKDIR/last_login)
# PASSWORD_VAR is an env var NAME, so the password never appears on argv.
login() {
  local jar="$1" email="$2" password_var="$3" status
  E2E_LOGIN_EMAIL="$email" E2E_LOGIN_PASSWORD="${!password_var}" python3 -c \
    'import json, os; print(json.dumps({"email": os.environ["E2E_LOGIN_EMAIL"], "password": os.environ["E2E_LOGIN_PASSWORD"]}))' \
    > "$WORKDIR/login_body.json"
  status=$(curl -s -o "$WORKDIR/last_login" -w '%{http_code}' \
    -X POST "$BASE_URL/api/v1/auth/login" \
    -b "$jar" -c "$jar" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_token "$jar")" \
    --data-binary @"$WORKDIR/login_body.json")
  echo "$status"
}

# api_status JAR METHOD PATH [OUTPUT_FILE] -> status code
api_status() {
  local jar="$1" method="$2" path="$3" out="${4:-/dev/null}" status
  status=$(curl -s -o "$out" -w '%{http_code}' -X "$method" "$BASE_URL$path" \
    -b "$jar" -c "$jar" -H "X-CSRF-Token: $(csrf_token "$jar")")
  echo "$status"
}

echo "== Clinical AI Platform E2E smoke =="
echo "target: $BASE_URL   env file: $ENV_FILE   docker steps: $DOCKER_STEPS"

# --- 1. health endpoints ----------------------------------------------------
echo "-- step 1: health"
if curl -fsS "$BASE_URL/health/live" | grep -q '"live"'; then
  pass "/health/live -> 200"
else
  fail "/health/live"
fi
if curl -fsS "$BASE_URL/health/ready" | grep -q '"ready"'; then
  pass "/health/ready -> 200"
else
  fail "/health/ready"
  echo "backend is not ready; aborting"; exit 1
fi

# --- 2. frontend at the app origin ------------------------------------------
echo "-- step 2: frontend at the app origin"
curl -s -D "$WORKDIR/index_headers" -o "$WORKDIR/index.html" "$BASE_URL/"
index_status=$(awk 'NR == 1 { print $2 }' "$WORKDIR/index_headers")
index_type=$(grep -i '^content-type:' "$WORKDIR/index_headers" | tr -d '\r' | awk '{ print $2 }')
if [ "$index_status" = "200" ] && [[ "$index_type" == text/html* ]]; then
  pass "/ -> 200 text/html"
else
  fail "/ -> $index_status $index_type"
fi
csp=$(grep -i '^content-security-policy:' "$WORKDIR/index_headers" | tr -d '\r' | cut -d' ' -f2-)
if [[ "$csp" == *"frame-ancestors 'none'"* ]] && [[ "$csp" == *"script-src 'self'"* ]] \
   && [[ "$csp" != *'unsafe-eval'* ]] && [[ "$csp" != *'unsafe-inline'* ]]; then
  pass "CSP header present and strict (no unsafe-inline/unsafe-eval)"
else
  fail "CSP header missing or unsafe: $csp"
fi
if grep -qi '^x-frame-options: DENY' "$WORKDIR/index_headers" \
   && grep -qi '^x-content-type-options: nosniff' "$WORKDIR/index_headers" \
   && grep -qi '^referrer-policy: no-referrer' "$WORKDIR/index_headers"; then
  pass "X-Frame-Options / X-Content-Type-Options / Referrer-Policy present"
else
  fail "security headers missing on /"
fi
curl -s -D "$WORKDIR/js_headers" -o "$WORKDIR/config.js" "$BASE_URL/assets/js/config.js"
js_type=$(grep -i '^content-type:' "$WORKDIR/js_headers" | tr -d '\r' | awk '{ print $2 }')
if [[ "$js_type" == text/javascript* || "$js_type" == application/javascript* ]] \
   && grep -q "DEFAULT_API_BASE_URL = '/api/v1'" "$WORKDIR/config.js"; then
  pass "served config.js uses the same-origin default '/api/v1' (no frontend edits)"
else
  fail "config.js not served correctly ($js_type)"
fi

# --- 3. login (cookie + CSRF) with the seeded demo doctor -------------------
echo "-- step 3: login (doctor)"
if [ ! -f "$ENV_FILE" ]; then
  fail "env file $ENV_FILE not found (needed for DEMO_* credentials)"; exit 1
fi
DOCTOR_EMAIL=$(env_value DEMO_DOCTOR_EMAIL)
DOCTOR_PASSWORD=$(env_value DEMO_DOCTOR_PASSWORD)
ADMIN_EMAIL=$(env_value DEMO_ADMIN_EMAIL)
ADMIN_PASSWORD=$(env_value DEMO_ADMIN_PASSWORD)
TECH_EMAIL=$(env_value DEMO_TECH_ADMIN_EMAIL)
TECH_PASSWORD=$(env_value DEMO_TECH_ADMIN_PASSWORD)
DOCTOR_PASSWORD_WRONG="definitely-not-the-password"

JAR_D="$WORKDIR/doctor.jar"
JAR_A="$WORKDIR/admin.jar"
JAR_T="$WORKDIR/tech.jar"

api_status "$JAR_D" GET /api/v1/auth/me "$WORKDIR/me_anon.json" >/dev/null
if [ "$(csrf_token "$JAR_D")" != "" ]; then
  pass "CSRF cookie bootstrapped via GET /api/v1/auth/me"
else
  fail "CSRF cookie not issued"
fi

bad_login=$(login "$JAR_D" "$DOCTOR_EMAIL" DOCTOR_PASSWORD_WRONG)
if [ "$bad_login" = "401" ]; then
  pass "wrong password -> 401 (uniform failure)"
else
  fail "wrong password -> $bad_login (expected 401)"
fi

status=$(login "$JAR_D" "$DOCTOR_EMAIL" DOCTOR_PASSWORD)
if [ "$status" = "200" ] && grep -q '"doctor"' "$WORKDIR/last_login"; then
  pass "doctor login -> 200 with role doctor (session cookie + X-CSRF-Token)"
else
  fail "doctor login -> $status"
fi

# --- 4. patient list -> detail -> labs -> prescriptions ---------------------
echo "-- step 4: clinical reads"
api_status "$JAR_D" GET "/api/v1/patients?limit=25" "$WORKDIR/patients.json" >/dev/null
patients_total=$(json_eval 'd.get("total")' < "$WORKDIR/patients.json" 2>/dev/null || echo "")
patient_id=$(json_eval 'd["items"][0]["id"]' < "$WORKDIR/patients.json" 2>/dev/null || echo "")
patient_name=$(json_eval 'd["items"][0]["display_name"]' < "$WORKDIR/patients.json" 2>/dev/null || echo "")
if [ -n "$patient_id" ] && [ -n "$patients_total" ]; then
  pass "patient list -> 200 (total=$patients_total, ids present)"
else
  fail "patient list shape unexpected"
fi

for endpoint in "" "/summary" "/labs" "/prescriptions"; do
  status=$(api_status "$JAR_D" GET "/api/v1/patients/$patient_id$endpoint" "$WORKDIR/read.json")
  shape=$(json_eval 'sorted(d)[:4]' < "$WORKDIR/read.json" 2>/dev/null || echo "")
  if [ "$status" = "200" ]; then
    pass "patient$endpoint -> 200 (keys: $shape)"
  else
    fail "patient$endpoint -> $status"
  fi
done

# --- 5. AI endpoint returns the documented 503 (LLM disabled) ---------------
echo "-- step 5: AI unavailable path (LLM_ENABLED=false)"
python3 -c 'import json, sys; print(json.dumps({"patient_id": sys.argv[1], "question": "Summarize this synthetic patient."}))' \
  "$patient_id" > "$WORKDIR/ask.json"
status=$(curl -s -o "$WORKDIR/ask_out.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/ai/ask" \
  -b "$JAR_D" -c "$JAR_D" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
  --data-binary @"$WORKDIR/ask.json")
code=$(json_eval 'd["error"]["code"]' < "$WORKDIR/ask_out.json" 2>/dev/null || echo "")
if [ "$status" = "503" ] && [ "$code" = "dependency_unavailable" ]; then
  pass "/api/v1/ai/ask -> 503 dependency_unavailable (documented behavior)"
else
  fail "/api/v1/ai/ask -> $status $code (expected 503 dependency_unavailable)"
fi
if [ "$(api_status "$JAR_D" GET "/api/v1/patients/$patient_id" /dev/null)" = "200" ] \
   && curl -fsS "$BASE_URL/health/ready" >/dev/null; then
  pass "app fully usable after the AI 503 (patient read + readiness still 200)"
else
  fail "app not usable after the AI 503"
fi

# --- 6. admin checks and role boundaries ------------------------------------
echo "-- step 6: admin checks"
if [ "$(api_status "$JAR_D" GET /api/v1/staff /dev/null)" = "403" ]; then
  pass "doctor on /api/v1/staff -> 403"
else
  fail "doctor on /api/v1/staff not denied"
fi
if [ "$(api_status "$JAR_D" GET /api/v1/audit /dev/null)" = "403" ]; then
  pass "doctor on /api/v1/audit -> 403"
else
  fail "doctor on /api/v1/audit not denied"
fi

api_status "$JAR_A" GET /api/v1/auth/me >/dev/null
if [ "$(login "$JAR_A" "$ADMIN_EMAIL" ADMIN_PASSWORD)" = "200" ] \
   && [ "$(api_status "$JAR_A" GET '/api/v1/staff?limit=25' "$WORKDIR/staff.json")" = "200" ]; then
  pass "clinical_admin lists staff -> 200"
else
  fail "clinical_admin staff list failed"
fi

staff_status=$(curl -s -o "$WORKDIR/staff_new.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/staff" \
  -b "$JAR_A" -c "$JAR_A" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_A")" \
  --data "{\"email\": \"e2e-smoke-$(date +%s)@example.com\", \"display_name\": \"E2E Smoke\"}")
new_user_id=$(json_eval 'd["user"]["id"]' < "$WORKDIR/staff_new.json" 2>/dev/null || echo "")
if [ "$staff_status" = "201" ] && [ -n "$new_user_id" ]; then
  pass "clinical_admin creates staff -> 201 (one-time password not printed)"
else
  fail "staff create -> $staff_status"
fi

if [ -n "$new_user_id" ]; then
  grant_status=$(curl -s -o "$WORKDIR/grant.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/staff/$new_user_id/roles" \
    -b "$JAR_A" -c "$JAR_A" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_token "$JAR_A")" \
    --data '{"role": "nurse"}')
  if [ "$grant_status" = "200" ] && grep -q '"nurse"' "$WORKDIR/grant.json"; then
    pass "clinical_admin grants role nurse -> 200"
  else
    fail "role grant -> $grant_status"
  fi
fi

if [ "$(login "$JAR_T" "$TECH_EMAIL" TECH_PASSWORD)" = "200" ] \
   && [ "$(api_status "$JAR_T" GET /api/v1/patients /dev/null)" = "403" ]; then
  pass "tech_admin on /api/v1/patients -> 403 (no clinical access)"
else
  fail "tech_admin clinical-access boundary broken"
fi

# --- 7. logout -> protected endpoints 401 -----------------------------------
echo "-- step 7: logout"
if [ "$(api_status "$JAR_D" POST /api/v1/auth/logout /dev/null)" = "204" ] \
   && [ "$(api_status "$JAR_D" GET /api/v1/patients /dev/null)" = "401" ] \
   && [ "$(api_status "$JAR_D" GET /api/v1/auth/me /dev/null)" = "401" ]; then
  pass "logout -> 204, then protected endpoints -> 401"
else
  fail "logout flow broken"
fi

# --- 8. audit trail ---------------------------------------------------------
echo "-- step 8: audit"
api_status "$JAR_A" GET '/api/v1/audit?limit=100' "$WORKDIR/audit.json" >/dev/null
missing_actions=$(python3 - "$WORKDIR/audit.json" <<'PY'
import json, sys
required = {
    "auth.login.success", "auth.login.failure", "auth.logout", "authz.denied",
    "patient.read", "patient.summary.read", "labs.read", "prescriptions.read",
    "ai.ask", "staff.create", "role.grant",
}
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
emitted = {item["action"] for item in data.get("items", [])}
print(",".join(sorted(required - emitted)))
PY
)
if [ -z "$missing_actions" ]; then
  pass "audit rows cover the workflow (login, denials, reads, ai.ask, staff/role)"
else
  fail "audit rows missing actions: $missing_actions"
fi

# --- 9. container logs contain no secrets -----------------------------------
if [ "$DOCKER_STEPS" = "1" ]; then
  echo "-- step 9: container log secret scan"
  $COMPOSE_CMD logs --no-color app > "$WORKDIR/app.log" 2>&1
  scan_result=$(ENV_FILE="$ENV_FILE" WORKDIR_LOG="$WORKDIR/app.log" PATIENT_NAME="$patient_name" python3 - <<'PY'
import os

env_path = os.environ["ENV_FILE"]
patient_name = os.environ.get("PATIENT_NAME", "")
wanted = [
    "APP_SECRET_KEY", "LLM_API_KEY",
    "DEMO_ADMIN_PASSWORD", "DEMO_DOCTOR_PASSWORD",
    "DEMO_NURSE_PASSWORD", "DEMO_TECH_ADMIN_PASSWORD",
]
values = {}
with open(env_path, encoding="utf-8") as handle:
    for line in handle:
        line = line.rstrip("\n")
        for key in wanted:
            if line.startswith(key + "="):
                value = line[len(key) + 1:].strip().strip("'\"")
                if value:
                    values[key] = value
with open(os.environ["WORKDIR_LOG"], encoding="utf-8", errors="replace") as handle:
    logs = handle.read()
leaks = [key for key, value in values.items() if value in logs]
if patient_name and patient_name in logs:
    leaks.append("CLINICAL_PAYLOAD(patient display name)")
print(",".join(leaks))
PY
)
  if [ -z "$scan_result" ]; then
    pass "container logs contain no secrets and no clinical payloads"
  else
    fail "container logs leak: $scan_result"
  fi
else
  echo "-- step 9: skipped (--no-docker)"
fi

# --- 10. restart -> data persists -------------------------------------------
if [ "$DOCKER_STEPS" = "1" ]; then
  echo "-- step 10: restart and persistence"
  $COMPOSE_CMD restart app >/dev/null 2>&1
  ready=0
  for _ in $(seq 1 60); do
    if curl -fsS "$BASE_URL/health/ready" >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
  done
  if [ "$ready" = "1" ]; then
    pass "app healthy again after docker compose restart app"
  else
    fail "app not healthy after restart"
  fi
  JAR_D2="$WORKDIR/doctor2.jar"
  api_status "$JAR_D2" GET /api/v1/auth/me >/dev/null
  if [ "$(login "$JAR_D2" "$DOCTOR_EMAIL" DOCTOR_PASSWORD)" = "200" ]; then
    pass "login works after restart (sessions persist in the database)"
  else
    fail "login broken after restart"
  fi
  api_status "$JAR_D2" GET "/api/v1/patients?limit=1" "$WORKDIR/patients_after.json" >/dev/null
  total_after=$(json_eval 'd.get("total")' < "$WORKDIR/patients_after.json" 2>/dev/null || echo "")
  if [ -n "$patients_total" ] && [ "$total_after" = "$patients_total" ]; then
    pass "data persisted across restart (patient count $total_after unchanged)"
  else
    fail "patient count changed across restart ($patients_total -> $total_after)"
  fi
else
  echo "-- step 10: skipped (--no-docker)"
fi

echo "== summary =="
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL STEPS PASSED"
  exit 0
fi
echo "$FAILURES STEP(S) FAILED"
exit 1
