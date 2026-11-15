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
curl -fsS "$BASE_URL/appointments.html" -o "$WORKDIR/appointments.html"
curl -fsS "$BASE_URL/assets/js/appointments.js" -o "$WORKDIR/appointments.js"
if grep -q 'voice-example-use' "$WORKDIR/appointments.html" \
   && grep -q 'voice-listener' "$WORKDIR/appointments.html" \
   && grep -q 'voice-waveform' "$WORKDIR/appointments.html" \
   && grep -q 'voice-suggestions' "$WORKDIR/appointments.html" \
   && grep -q 'VOICE_EXAMPLES' "$WORKDIR/appointments.js" \
   && grep -q 'startVoiceMeter' "$WORKDIR/appointments.js" \
   && grep -q 'patient-suggestions' "$WORKDIR/appointments.js"; then
  pass "served voice UI includes listening orb, live meter, example and patient suggestions"
else
  fail "served voice UI is missing the listening orb/meter/example/suggestions"
fi
if curl -fsS "$BASE_URL/assets/js/print-record.js" | grep -q 'prepareAndPrint' \
   && curl -fsS "$BASE_URL/assets/css/print.css" | grep -q 'size: A4 portrait'; then
  pass "print record module and A4 stylesheet are served"
else
  fail "print record module or A4 stylesheet is missing"
fi
missing_status=$(curl -s -o "$WORKDIR/404.html" -w '%{http_code}' \
  -H 'Accept: text/html' "$BASE_URL/definitely-missing-page")
if [ "$missing_status" = "404" ] \
   && grep -q 'Page not found' "$WORKDIR/404.html" \
   && grep -q 'error-404.svg' "$WORKDIR/404.html"; then
  pass "unknown browser navigation -> custom 404 page (status 404, illustration present)"
else
  fail "custom 404 page -> $missing_status"
fi
if curl -fsS "$BASE_URL/assets/images/error-404.svg" | grep -q '<svg' \
   && curl -fsS "$BASE_URL/assets/images/error-connection.svg" | grep -q '<svg'; then
  pass "custom error illustrations are served locally"
else
  fail "custom error illustrations missing"
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
NURSE_EMAIL=$(env_value DEMO_NURSE_EMAIL)
NURSE_PASSWORD=$(env_value DEMO_NURSE_PASSWORD)
DOCTOR_PASSWORD_WRONG="definitely-not-the-password"

JAR_D="$WORKDIR/doctor.jar"
JAR_A="$WORKDIR/admin.jar"
JAR_N="$WORKDIR/nurse.jar"

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

# --- 4. patient list -> detail -> labs -> trends -> prescriptions ------------
echo "-- step 4: clinical reads"
api_status "$JAR_D" GET "/api/v1/patients?limit=25" "$WORKDIR/patients.json" >/dev/null
patients_total=$(json_eval 'd.get("total")' < "$WORKDIR/patients.json" 2>/dev/null || echo "")
patient_id=$(json_eval 'd["items"][0]["id"]' < "$WORKDIR/patients.json" 2>/dev/null || echo "")
patient_name=$(json_eval 'd["items"][0]["display_name"]' < "$WORKDIR/patients.json" 2>/dev/null || echo "")
# The synthetic seed loads a 45-patient demo cohort; require the floor so a
# thin dataset fails loudly.
PATIENTS_FLOOR=40
if [ -n "$patient_id" ] && [ -n "$patients_total" ] && [ "$patients_total" -ge "$PATIENTS_FLOOR" ] 2>/dev/null; then
  pass "patient list -> 200 (total=$patients_total >= $PATIENTS_FLOOR, ids present)"
else
  fail "patient list shape unexpected (total='$patients_total', expected >= $PATIENTS_FLOOR seeded patients)"
fi

if [ "$(api_status "$JAR_D" GET "/api/v1/patients/$patient_id/summary" "$WORKDIR/patient_summary.json")" = "200" ] \
   && grep -q '"last_visit"' "$WORKDIR/patient_summary.json" \
   && grep -q '"medication_responses"' "$WORKDIR/patient_summary.json" \
   && grep -q '"systolic_bp"' "$WORKDIR/patient_summary.json"; then
  pass "patient summary carries visit/vitals/treatment-response data"
else
  fail "patient summary missing visit/vitals/treatment-response data"
fi

for endpoint in "" "/summary" "/encounters" "/labs" "/labs/trends" "/prescriptions"; do
  status=$(api_status "$JAR_D" GET "/api/v1/patients/$patient_id$endpoint" "$WORKDIR/read.json")
  shape=$(json_eval 'sorted(d)[:4]' < "$WORKDIR/read.json" 2>/dev/null || echo "")
  if [ "$status" = "200" ]; then
    pass "patient$endpoint -> 200 (keys: $shape)"
  else
    fail "patient$endpoint -> $status"
  fi
done

# --- 4b. appointment calendar: availability, slots, create, cancel ----------
echo "-- step 4b: appointment calendar"
if [ "$(api_status "$JAR_D" GET /api/v1/appointments/config /dev/null)" = "200" ] \
   && [ "$(api_status "$JAR_D" GET /api/v1/appointments/availability /dev/null)" = "200" ]; then
  pass "appointment config + own availability -> 200"
else
  fail "appointment config/availability not readable"
fi

suggestion_status=$(curl -s -o "$WORKDIR/patient_suggestion.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/v1/appointments/patient-suggestions" \
  -b "$JAR_D" -c "$JAR_D" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
  --data '{"text":"Book 30 minute follow up for Elena Georgievah. M-R-N-0-0-0-1."}')
if [ "$suggestion_status" = "200" ] \
   && grep -q 'Елена Георгиева' "$WORKDIR/patient_suggestion.json" \
   && grep -q 'MRN-0001' "$WORKDIR/patient_suggestion.json"; then
  pass "transcript patient suggestion resolves Elena Georgieva / MRN-0001"
else
  fail "transcript patient suggestion failed ($suggestion_status)"
fi

future_date=$(python3 -c "from datetime import datetime, timedelta; from zoneinfo import ZoneInfo
d = datetime.now(ZoneInfo('Europe/Sofia')).date() + timedelta(days=2)
d += timedelta(days=(7 - d.weekday()) % 7)
print(d.isoformat())")
api_status "$JAR_D" GET "/api/v1/appointments/slots?date=$future_date" "$WORKDIR/slots.json" >/dev/null
slot_start=$(json_eval 'next((s["start_at"] for s in d.get("slots", []) if s.get("available")), "")' < "$WORKDIR/slots.json" 2>/dev/null || echo "")
if [ -n "$slot_start" ]; then
  python3 -c 'import json, sys; print(json.dumps({"patient_id": sys.argv[1], "start_at": sys.argv[2], "duration_minutes": 30, "appointment_type": "Consultation"}))' \
    "$patient_id" "$slot_start" > "$WORKDIR/appt.json"
  appt_status=$(curl -s -o "$WORKDIR/appt_out.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/appointments" \
    -b "$JAR_D" -c "$JAR_D" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
    --data-binary @"$WORKDIR/appt.json")
  appt_id=$(json_eval 'd.get("id", "")' < "$WORKDIR/appt_out.json" 2>/dev/null || echo "")
  if [ "$appt_status" = "201" ] && [ -n "$appt_id" ]; then
    pass "appointment create -> 201 (id present)"
    patch_status=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$BASE_URL/api/v1/appointments/$appt_id" \
      -b "$JAR_D" -c "$JAR_D" \
      -H "Content-Type: application/json" \
      -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
      --data-binary '{"notes":"E2E reschedule probe"}')
    if [ "$patch_status" = "200" ]; then
      pass "appointment update -> 200"
    else
      fail "appointment update -> $patch_status"
    fi
    cancel_status=$(curl -s -o "$WORKDIR/appt_cancel.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/appointments/$appt_id/cancel" \
      -b "$JAR_D" -c "$JAR_D" -H "X-CSRF-Token: $(csrf_token "$JAR_D")")
    cancelled=$(json_eval 'd.get("status")' < "$WORKDIR/appt_cancel.json" 2>/dev/null || echo "")
    if [ "$cancel_status" = "200" ] && [ "$cancelled" = "cancelled" ]; then
      pass "appointment cancel -> 200 cancelled"
    else
      fail "appointment cancel -> $cancel_status ($cancelled)"
    fi
    remove_doctor_status=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      "$BASE_URL/api/v1/appointments/$appt_id/remove-from-calendar" \
      -b "$JAR_D" -c "$JAR_D" -H "X-CSRF-Token: $(csrf_token "$JAR_D")")
    if [ "$remove_doctor_status" = "403" ]; then
      pass "doctor cannot remove a cancelled calendar entry -> 403 (Admin-only)"
    else
      fail "doctor remove-from-calendar -> $remove_doctor_status (expected 403)"
    fi
  else
    fail "appointment create -> $appt_status"
  fi
else
  fail "no available appointment slot for $future_date (is availability seeded?)"
fi

# --- 4c. appointment history, status action and export ----------------------
if [ -n "$slot_start" ]; then
  python3 -c 'import json, sys; print(json.dumps({"patient_id": sys.argv[1], "start_at": sys.argv[2], "duration_minutes": 30, "appointment_type": "Consultation"}))' \
    "$patient_id" "$slot_start" > "$WORKDIR/completion_appt.json"
  completion_create=$(curl -s -o "$WORKDIR/completion_appt_out.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/appointments" \
    -b "$JAR_D" -c "$JAR_D" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
    --data-binary @"$WORKDIR/completion_appt.json")
  completion_id=$(json_eval 'd.get("id", "")' < "$WORKDIR/completion_appt_out.json" 2>/dev/null || echo "")
  if [ "$completion_create" = "201" ] && [ -n "$completion_id" ]; then
    completion_patch=$(curl -s -o "$WORKDIR/completion_patch.json" -w '%{http_code}' -X PATCH "$BASE_URL/api/v1/appointments/$completion_id" \
      -b "$JAR_D" -c "$JAR_D" \
      -H "Content-Type: application/json" \
      -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
      --data '{"status":"completed"}')
    completion_status=$(json_eval 'd.get("status")' < "$WORKDIR/completion_patch.json" 2>/dev/null || echo "")
    if [ "$completion_patch" = "200" ] && [ "$completion_status" = "completed" ]; then
      pass "doctor marks an appointment completed -> 200 (status recorded)"
    else
      fail "mark completed -> $completion_patch ($completion_status)"
    fi
  else
    fail "history appointment create -> $completion_create"
  fi

  if [ -n "$completion_id" ]; then
    # Admin/Nurse sessions are exercised here; step 6 re-logs them in for the
    # remaining role-boundary checks (login rotates the session cookie).
    login "$JAR_A" "$ADMIN_EMAIL" ADMIN_PASSWORD >/dev/null
    login "$JAR_N" "$NURSE_EMAIL" NURSE_PASSWORD >/dev/null
    # Scope history/export to the appointment's own date so repeated E2E runs
    # cannot push the row outside a fixed 50-row page.
    history_range=$(python3 -c "from datetime import datetime, timedelta
from urllib.parse import urlencode
import sys
s = datetime.fromisoformat(sys.argv[1].replace('Z', '+00:00'))
print(urlencode({'start': (s - timedelta(days=1)).isoformat(), 'end': (s + timedelta(days=1)).isoformat(), 'limit': 50}))" "$slot_start")
    export_range=$(python3 -c "from datetime import datetime, timedelta
from urllib.parse import urlencode
import sys
s = datetime.fromisoformat(sys.argv[1].replace('Z', '+00:00'))
print(urlencode({'start': (s - timedelta(days=1)).isoformat(), 'end': (s + timedelta(days=1)).isoformat()}))" "$slot_start")
    history_ok=0
    for jar in "$JAR_D" "$JAR_A" "$JAR_N"; do
      code=$(api_status "$jar" GET "/api/v1/appointments/history?$history_range" "$WORKDIR/appt_history.json")
      if [ "$code" = "200" ] && grep -q "$completion_id" "$WORKDIR/appt_history.json"; then
        history_ok=$((history_ok + 1))
      fi
    done
    if [ "$history_ok" = "3" ]; then
      pass "Doctor/Admin/Nurse read the same retained appointment history -> 200"
    else
      fail "appointment history role read failed"
    fi

    doctor_export=$(api_status "$JAR_D" GET "/api/v1/appointments/history/export.csv?$export_range" "$WORKDIR/appt_export.csv")
    admin_export=$(api_status "$JAR_A" GET "/api/v1/appointments/history/export.csv?$export_range" "$WORKDIR/appt_export_admin.csv")
    nurse_export=$(api_status "$JAR_N" GET "/api/v1/appointments/history/export.csv?$export_range" /dev/null)
    if [ "$doctor_export" = "200" ] && [ "$admin_export" = "200" ] && [ "$nurse_export" = "403" ] \
       && head -n 1 "$WORKDIR/appt_export.csv" | grep -q "appointment_id" \
       && grep -q "$completion_id" "$WORKDIR/appt_export.csv" \
       && grep -q "$completion_id" "$WORKDIR/appt_export_admin.csv"; then
      pass "appointment history CSV: Doctor/Admin 200 with retained row, Nurse 403"
    else
      fail "appointment history export permissions/content failed"
    fi
  fi
else
  fail "appointment history checks could not resolve a slot"
fi

# --- 5. AI endpoint: real answer (LLM enabled) or documented 503 ------------
echo "-- step 5: AI path (answers 200 when LLM configured, 503 when not)"
python3 -c 'import json, sys; print(json.dumps({"patient_id": sys.argv[1], "question": "Summarize this synthetic patient."}))' \
  "$patient_id" > "$WORKDIR/ask.json"
status=$(curl -s -o "$WORKDIR/ask_out.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/ai/ask" \
  -b "$JAR_D" -c "$JAR_D" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
  --data-binary @"$WORKDIR/ask.json")
code=$(json_eval 'd["error"]["code"]' < "$WORKDIR/ask_out.json" 2>/dev/null || echo "")
ai_generated=$(json_eval 'd.get("ai_generated") is True and "answer" in d' < "$WORKDIR/ask_out.json" 2>/dev/null || echo "")
if [ "$status" = "200" ] && [ "$ai_generated" = "True" ]; then
  pass "/api/v1/ai/ask -> 200 with answer (LLM configured: $(json_eval 'd.get("model", "?")' < "$WORKDIR/ask_out.json"))"
elif [ "$status" = "503" ] && [ "$code" = "dependency_unavailable" ]; then
  pass "/api/v1/ai/ask -> 503 dependency_unavailable (LLM disabled/unavailable - documented behavior)"
else
  fail "/api/v1/ai/ask -> $status $code (expected 200 with answer, or 503 dependency_unavailable)"
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
if [ "$(api_status "$JAR_D" GET /api/v1/analytics/summary /dev/null)" = "403" ]; then
  pass "doctor on /api/v1/analytics/summary -> 403"
else
  fail "doctor on /api/v1/analytics/summary not denied"
fi

api_status "$JAR_A" GET /api/v1/auth/me >/dev/null
if [ "$(login "$JAR_A" "$ADMIN_EMAIL" ADMIN_PASSWORD)" = "200" ] \
   && [ "$(api_status "$JAR_A" GET '/api/v1/staff?limit=25' "$WORKDIR/staff.json")" = "200" ]; then
  pass "clinical_admin lists staff -> 200"
else
  fail "clinical_admin staff list failed"
fi

E2E_STAFF_EMAIL="e2e-smoke-$(date +%s)@example.com"
staff_status=$(curl -s -o "$WORKDIR/staff_new.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/staff" \
  -b "$JAR_A" -c "$JAR_A" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_A")" \
  --data "{\"email\": \"$E2E_STAFF_EMAIL\", \"display_name\": \"E2E Smoke\"}")
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

# Log in as the temporary Nurse account so E2E can exercise Nurse messaging
# and prove that an unrelated Nurse cannot read another Nurse's conversation.
E2E_STAFF_PASSWORD=$(json_eval 'd.get("generated_password") or ""' < "$WORKDIR/staff_new.json" 2>/dev/null || echo "")
JAR_E2E="$WORKDIR/e2e.jar"
if [ -n "$new_user_id" ] && [ -n "$E2E_STAFF_PASSWORD" ] \
   && [ "$(login "$JAR_E2E" "$E2E_STAFF_EMAIL" E2E_STAFF_PASSWORD)" = "200" ]; then
  pass "temporary E2E Nurse can sign in -> 200 (password never printed)"
else
  fail "temporary E2E Nurse login failed"
fi

nurse_range=$(python3 -c "from datetime import datetime, timedelta, timezone
n = datetime.now(timezone.utc)
print(n.isoformat().replace('+00:00', 'Z'), (n + timedelta(days=7)).isoformat().replace('+00:00', 'Z'))")
nurse_start=${nurse_range% *}
nurse_end=${nurse_range#* }
if [ "$(login "$JAR_N" "$NURSE_EMAIL" NURSE_PASSWORD)" = "200" ] \
   && [ "$(api_status "$JAR_N" GET /api/v1/staff /dev/null)" = "403" ] \
   && [ "$(api_status "$JAR_N" GET /api/v1/audit /dev/null)" = "403" ] \
   && [ "$(api_status "$JAR_N" GET /api/v1/analytics/summary /dev/null)" = "403" ] \
   && [ "$(api_status "$JAR_N" GET "/api/v1/appointments?start=$nurse_start&end=$nurse_end" /dev/null)" = "200" ] \
   && [ "$(api_status "$JAR_N" POST /api/v1/appointments /dev/null)" = "403" ] \
   && [ "$(api_status "$JAR_N" GET /api/v1/patients /dev/null)" = "200" ]; then
  pass "nurse least privilege: /staff 403, /audit 403, analytics 403, appointments read-only (GET 200, POST 403), /patients 200"
else
  fail "nurse role boundary broken (expected /staff 403, /audit 403, analytics 403, appointments GET 200/POST 403, /patients 200)"
fi

# The nurse's effective permissions must include appointment:read but not the
# write or availability permissions (proof in the /auth/me payload).
api_status "$JAR_N" GET /api/v1/auth/me "$WORKDIR/me_nurse.json" >/dev/null
if grep -q '"patient:read"' "$WORKDIR/me_nurse.json" \
   && grep -q '"appointment:read"' "$WORKDIR/me_nurse.json" \
   && grep -q '"message:read"' "$WORKDIR/me_nurse.json" \
   && grep -q '"message:write"' "$WORKDIR/me_nurse.json" \
   && ! grep -q '"appointment:write"' "$WORKDIR/me_nurse.json" \
   && ! grep -q '"appointment:manage_availability"' "$WORKDIR/me_nurse.json" \
   && ! grep -q '"staff:read"' "$WORKDIR/me_nurse.json" \
   && ! grep -q '"audit:read"' "$WORKDIR/me_nurse.json" \
   && ! grep -q '"logs:read"' "$WORKDIR/me_nurse.json"; then
  pass "nurse /auth/me permissions: clinical reads + appointments + messaging, no admin/write access"
else
  fail "nurse /auth/me permissions wrong"
fi

# Admin holds every permission now (clinical reads included).
if [ "$(api_status "$JAR_A" GET /api/v1/patients /dev/null)" = "200" ] \
   && [ "$(api_status "$JAR_A" GET "/api/v1/patients/$patient_id/labs" /dev/null)" = "200" ] \
   && [ "$(api_status "$JAR_A" GET /api/v1/audit /dev/null)" = "200" ]; then
  pass "admin full access: /patients -> 200, labs -> 200, /audit -> 200"
else
  fail "admin clinical/audit access broken"
fi

# System Logs: read-only, Admin-only; history and SSE reject clinical roles.
if [ "$(api_status "$JAR_D" GET /api/v1/admin/logs /dev/null)" = "403" ] \
   && [ "$(api_status "$JAR_D" GET /api/v1/admin/logs/stream /dev/null)" = "403" ] \
   && [ "$(api_status "$JAR_N" GET /api/v1/admin/logs /dev/null)" = "403" ] \
   && [ "$(api_status "$JAR_N" GET /api/v1/admin/logs/stream /dev/null)" = "403" ]; then
  pass "System Logs deny Doctor/Nurse on history and stream -> 403"
else
  fail "System Logs role boundary broken"
fi
if [ "$(api_status "$JAR_A" GET '/api/v1/admin/logs?limit=5' "$WORKDIR/system_logs.json")" = "200" ] \
   && grep -q '"timezone"' "$WORKDIR/system_logs.json" \
   && grep -q '"counts"' "$WORKDIR/system_logs.json"; then
  pass "Admin reads bounded system-log history -> 200 (counts + timezone)"
else
  fail "Admin system-log history failed"
fi
# Replay a persisted patient read over SSE (curl exits 28 at --max-time).
api_status "$JAR_A" GET "/api/v1/patients/$patient_id" /dev/null >/dev/null
curl -sN --max-time 2 -b "$JAR_A" \
  "$BASE_URL/api/v1/admin/logs/stream?category=patients" > "$WORKDIR/system_logs_stream.txt" 2>/dev/null || true
if grep -q 'event: system' "$WORKDIR/system_logs_stream.txt" \
   && grep -q '"event_type":"patient.read"' "$WORKDIR/system_logs_stream.txt"; then
  pass "Admin SSE replay delivers persisted patient.read events"
else
  fail "Admin SSE replay delivered no patient.read event"
fi

# Patient lifecycle: Admin creates a synthetic record, Doctor/Nurse cannot
# delete it, Admin soft-deletes it, and it immediately disappears from reads.
patient_suffix=$(date +%s)
patient_create_status=$(curl -s -o "$WORKDIR/patient_created.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/patients" \
  -b "$JAR_A" -c "$JAR_A" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_A")" \
  --data "{\"given_name\": \"E2E $patient_suffix\", \"family_name\": \"Lifecycle\", \"sex\": \"unknown\"}")
e2e_patient_id=$(json_eval 'd.get("id", "")' < "$WORKDIR/patient_created.json" 2>/dev/null || echo "")
if [ "$patient_create_status" = "201" ] && [ -n "$e2e_patient_id" ]; then
  pass "admin creates a patient -> 201 (server MRN)"
  delete_denied=0
  for jar in "$JAR_D" "$JAR_N"; do
    code=$(api_status "$jar" DELETE "/api/v1/patients/$e2e_patient_id" /dev/null)
    [ "$code" = "403" ] || delete_denied=1
  done
  if [ "$delete_denied" = "0" ]; then
    pass "doctor/nurse cannot delete a patient -> 403"
  else
    fail "doctor/nurse patient deletion was not 403"
  fi
  delete_status=$(api_status "$JAR_A" DELETE "/api/v1/patients/$e2e_patient_id" /dev/null)
  if [ "$delete_status" = "204" ] \
     && [ "$(api_status "$JAR_A" GET "/api/v1/patients/$e2e_patient_id" /dev/null)" = "404" ]; then
    pass "admin soft-deletes the patient -> 204, then detail -> 404"
  else
    fail "admin patient soft-delete -> $delete_status"
  fi
else
  fail "admin patient create -> $patient_create_status"
fi

if [ -n "$appt_id" ]; then
  remove_admin_status=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "$BASE_URL/api/v1/appointments/$appt_id/remove-from-calendar" \
    -b "$JAR_A" -c "$JAR_A" -H "X-CSRF-Token: $(csrf_token "$JAR_A")")
  if [ "$remove_admin_status" = "204" ]; then
    pass "admin removes a cancelled calendar entry -> 204 (record retained)"
  else
    fail "admin remove-from-calendar -> $remove_admin_status (expected 204)"
  fi
fi

analytics_status=$(api_status "$JAR_A" GET '/api/v1/analytics/summary?start=2026-10-01&end=2026-10-14' "$WORKDIR/analytics.json")
if [ "$analytics_status" = "200" ] \
   && python3 -c 'import json,sys
d = json.load(open(sys.argv[1]))
required = {"range","counts","activity","previous_activity","comparisons","patients","appointments","labs","prescriptions","security","ai_usage","data_quality","system_health","staff"}
assert required <= set(d), sorted(required - set(d))
assert d["range"]["days"] == 14
assert len(d["appointments"]["by_weekday"]) == 7
assert len(d["appointments"]["by_hour"]) == 24
assert d["ai_usage"]["latency"]["available"] is False
assert d["system_health"]["database"] == "connected"
assert "MRN-" not in json.dumps(d)
' "$WORKDIR/analytics.json"; then
  pass "admin analytics summary -> 200 (typed ranges, appointment/lab/security/AI blocks)"
else
  fail "admin analytics summary -> $analytics_status"
fi
if [ "$(api_status "$JAR_A" GET '/api/v1/analytics/summary?start=2026-10-14&end=2026-10-01' /dev/null)" = "400" ] \
   && [ "$(api_status "$JAR_A" GET '/api/v1/analytics/summary?start=2026-10-01' /dev/null)" = "400" ] \
   && [ "$(api_status "$JAR_A" GET '/api/v1/analytics/summary?days=7' /dev/null)" = "400" ]; then
  pass "analytics rejects reversed/partial/unknown ranges -> 400"
else
  fail "analytics range validation broken"
fi

# Resolve the seeded staff ids from the Admin directory for the messaging
# lifecycle. The temporary E2E Nurse logs in with its one-time password above.
read -r DOCTOR_ID NURSE_ID ADMIN_ID <<< "$(python3 - "$WORKDIR/staff.json" "$DOCTOR_EMAIL" "$NURSE_EMAIL" "$ADMIN_EMAIL" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
by_email = {
    str(item.get("email", "")).lower(): str(item.get("id", ""))
    for item in data.get("items", [])
}
print(" ".join(by_email.get(email.lower(), "") for email in sys.argv[2:]))
PY
)"

send_message() {
  local jar="$1" recipient="$2" subject="$3" body="$4" out="$5"
  python3 - "$recipient" "$subject" "$body" <<'PY' > "$WORKDIR/message_send_body.json"
import json
import sys

print(json.dumps({
    "recipient_id": sys.argv[1],
    "subject": sys.argv[2],
    "body": sys.argv[3],
}))
PY
  curl -s -o "$out" -w '%{http_code}' -X POST "$BASE_URL/api/v1/messages" \
    -b "$jar" -c "$jar" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_token "$jar")" \
    --data-binary @"$WORKDIR/message_send_body.json"
}

message_in_box() {
  local jar="$1" box="$2" subject="$3" message_id="$4" out="$5" status
  status=$(api_status "$jar" GET "/api/v1/messages?box=$box&q=$subject&limit=50" "$out")
  [ "$status" = "200" ] || return 1
  python3 - "$out" "$message_id" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
ids = {str(item.get("id", "")) for item in data.get("items", [])}
raise SystemExit(0 if sys.argv[2] in ids else 1)
PY
}

if [ -n "$DOCTOR_ID" ] && [ -n "$NURSE_ID" ] && [ -n "$ADMIN_ID" ] && [ -f "$JAR_E2E" ]; then
  MSG_TOKEN=$(python3 -c 'import uuid; print(uuid.uuid4().hex[:10])')

  nurse_subject="e2e-nurse-doctor-$MSG_TOKEN"
  nurse_send_status=$(send_message "$JAR_N" "$DOCTOR_ID" "$nurse_subject" \
    "Private nurse-to-doctor message." "$WORKDIR/msg_nurse_doctor.json")
  nurse_message_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_nurse_doctor.json" 2>/dev/null || echo "")
  if [ "$nurse_send_status" = "201" ] && [ -n "$nurse_message_id" ]; then
    pass "Nurse -> Doctor message -> 201"
  else
    fail "Nurse -> Doctor message -> $nurse_send_status (expected 201)"
  fi

  if message_in_box "$JAR_D" inbox "$nurse_subject" "$nurse_message_id" "$WORKDIR/msg_doctor_inbox.json"; then
    pass "Doctor sees the Nurse's incoming message"
  else
    fail "Doctor inbox missing the Nurse's message"
  fi
  if [ "$(api_status "$JAR_D" POST "/api/v1/messages/$nurse_message_id/read" /dev/null)" = "204" ]; then
    pass "Doctor marks the Nurse's message read -> 204"
  else
    fail "Doctor mark-read failed"
  fi

  # Admin and an unrelated Nurse must not see or mutate the private exchange.
  if message_in_box "$JAR_A" inbox "$nurse_subject" "$nurse_message_id" "$WORKDIR/msg_admin_private.json"; then
    fail "Admin must not see a private Nurse -> Doctor message"
  else
    pass "Admin cannot see the private Nurse -> Doctor message"
  fi
  if [ "$(api_status "$JAR_A" POST "/api/v1/messages/$nurse_message_id/read" /dev/null)" = "404" ]; then
    pass "Admin cannot mark another user's message read -> 404"
  else
    fail "Admin mark-read privacy boundary broken"
  fi
  if message_in_box "$JAR_E2E" inbox "$nurse_subject" "$nurse_message_id" "$WORKDIR/msg_outsider_private.json"; then
    fail "Unrelated Nurse must not see the private Nurse -> Doctor message"
  else
    pass "Unrelated Nurse cannot see the private Nurse -> Doctor message"
  fi
  if [ "$(api_status "$JAR_E2E" POST "/api/v1/messages/$nurse_message_id/read" /dev/null)" = "404" ]; then
    pass "Unrelated Nurse cannot mark the message read -> 404"
  else
    fail "Unrelated Nurse mark-read privacy boundary broken"
  fi

  doctor_subject="e2e-doctor-nurse-$MSG_TOKEN"
  doctor_send_status=$(send_message "$JAR_D" "$NURSE_ID" "$doctor_subject" \
    "Doctor reply to the Nurse." "$WORKDIR/msg_doctor_nurse.json")
  doctor_message_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_doctor_nurse.json" 2>/dev/null || echo "")
  if [ "$doctor_send_status" = "201" ] \
     && message_in_box "$JAR_N" inbox "$doctor_subject" "$doctor_message_id" "$WORKDIR/msg_nurse_reply.json"; then
    pass "Doctor -> Nurse reply -> 201 and appears in the Nurse inbox"
  else
    fail "Doctor -> Nurse reply lifecycle failed"
  fi

  admin_subject="e2e-admin-nurse-$MSG_TOKEN"
  admin_send_status=$(send_message "$JAR_A" "$NURSE_ID" "$admin_subject" \
    "Admin message to the Nurse." "$WORKDIR/msg_admin_nurse.json")
  admin_message_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_admin_nurse.json" 2>/dev/null || echo "")
  if [ "$admin_send_status" = "201" ] \
     && message_in_box "$JAR_N" inbox "$admin_subject" "$admin_message_id" "$WORKDIR/msg_nurse_admin_inbox.json"; then
    pass "Admin -> Nurse message -> 201 and appears in the Nurse inbox"
  else
    fail "Admin -> Nurse lifecycle failed"
  fi

  nurse_admin_subject="e2e-nurse-admin-$MSG_TOKEN"
  nurse_admin_status=$(send_message "$JAR_N" "$ADMIN_ID" "$nurse_admin_subject" \
    "Nurse message to the Admin." "$WORKDIR/msg_nurse_admin.json")
  nurse_admin_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_nurse_admin.json" 2>/dev/null || echo "")
  if [ "$nurse_admin_status" = "201" ] \
     && message_in_box "$JAR_A" inbox "$nurse_admin_subject" "$nurse_admin_id" "$WORKDIR/msg_admin_inbox.json"; then
    pass "Nurse -> Admin message -> 201 and appears in the Admin inbox"
  else
    fail "Nurse -> Admin lifecycle failed"
  fi

  doctor_admin_subject="e2e-doctor-admin-$MSG_TOKEN"
  doctor_admin_status=$(send_message "$JAR_D" "$ADMIN_ID" "$doctor_admin_subject" \
    "Doctor message to the Admin." "$WORKDIR/msg_doctor_admin.json")
  doctor_admin_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_doctor_admin.json" 2>/dev/null || echo "")
  if [ "$doctor_admin_status" = "201" ] \
     && message_in_box "$JAR_A" inbox "$doctor_admin_subject" "$doctor_admin_id" "$WORKDIR/msg_admin_from_doctor.json"; then
    pass "Doctor -> Admin message -> 201 and appears in the Admin inbox"
  else
    fail "Doctor -> Admin lifecycle failed"
  fi

  admin_doctor_subject="e2e-admin-doctor-$MSG_TOKEN"
  admin_doctor_status=$(send_message "$JAR_A" "$DOCTOR_ID" "$admin_doctor_subject" \
    "Admin message to the Doctor." "$WORKDIR/msg_admin_doctor.json")
  admin_doctor_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_admin_doctor.json" 2>/dev/null || echo "")
  if [ "$admin_doctor_status" = "201" ] \
     && message_in_box "$JAR_D" inbox "$admin_doctor_subject" "$admin_doctor_id" "$WORKDIR/msg_doctor_admin_inbox.json"; then
    pass "Admin -> Doctor message -> 201 and appears in the Doctor inbox"
  else
    fail "Admin -> Doctor lifecycle failed"
  fi

  nurse_nurse_subject="e2e-nurse-nurse-$MSG_TOKEN"
  nurse_nurse_status=$(send_message "$JAR_N" "$new_user_id" "$nurse_nurse_subject" \
    "Nurse message to another Nurse." "$WORKDIR/msg_nurse_nurse.json")
  nurse_nurse_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_nurse_nurse.json" 2>/dev/null || echo "")
  if [ "$nurse_nurse_status" = "201" ] \
     && message_in_box "$JAR_E2E" inbox "$nurse_nurse_subject" "$nurse_nurse_id" "$WORKDIR/msg_e2e_nurse_inbox.json"; then
    pass "Nurse -> Nurse message -> 201 and appears in the other Nurse inbox"
  else
    fail "Nurse -> Nurse lifecycle failed"
  fi

  if [ "$(send_message "$JAR_N" "$NURSE_ID" "e2e-self-$MSG_TOKEN" "self" "$WORKDIR/msg_self.json")" = "400" ]; then
    pass "Nurse self-message -> 400"
  else
    fail "Nurse self-message was not rejected"
  fi
  unknown_message_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
  if [ "$(send_message "$JAR_N" "$unknown_message_id" "e2e-unknown-$MSG_TOKEN" "unknown" "$WORKDIR/msg_unknown.json")" = "400" ]; then
    pass "Nurse -> unknown recipient -> 400"
  else
    fail "Nurse -> unknown recipient was not rejected"
  fi

  if [ "$(api_status "$JAR_D" GET /api/v1/messages/counts "$WORKDIR/message_counts.json")" = "200" ] \
     && grep -q '"unread_total"' "$WORKDIR/message_counts.json" \
     && [ "$(api_status "$JAR_N" GET /api/v1/messages/recipients /dev/null)" = "200" ] \
     && [ "$(api_status "$JAR_A" GET /api/v1/messages /dev/null)" = "200" ]; then
    pass "messages: search/counts/recipients available to Doctor, Nurse and Admin"
  else
    fail "messages summary endpoints are not available to all roles"
  fi

  # Bounded Allowlisted attachment upload + sender/recipient-only download.
  attachment_subject="e2e-attachment-$MSG_TOKEN"
  printf 'E2E synthetic message attachment.\n' > "$WORKDIR/e2e-note.txt"
  attachment_status=$(curl -s -o "$WORKDIR/msg_attachment.json" -w '%{http_code}' \
    -X POST "$BASE_URL/api/v1/messages/attachments" \
    -b "$JAR_N" -c "$JAR_N" \
    -H "X-CSRF-Token: $(csrf_token "$JAR_N")" \
    -F "recipient_id=$DOCTOR_ID" \
    -F "subject=$attachment_subject" \
    -F "body=Please download the attachment." \
    -F "file=@$WORKDIR/e2e-note.txt;type=text/plain;filename=e2e-note.txt")
  attachment_id=$(json_eval 'd.get("id") or ""' < "$WORKDIR/msg_attachment.json" 2>/dev/null || echo "")
  attachment_name=$(json_eval 'd.get("attachment", {}).get("filename") or ""' < "$WORKDIR/msg_attachment.json" 2>/dev/null || echo "")
  if [ "$attachment_status" = "201" ] && [ -n "$attachment_id" ] \
     && [ "$attachment_name" = "e2e-note.txt" ]; then
    pass "Nurse sends an allowlisted file attachment -> 201"
  else
    fail "message attachment upload -> $attachment_status ($attachment_name)"
  fi
  attachment_download=$(curl -s -o "$WORKDIR/msg_attachment_download.txt" -w '%{http_code}' \
    -b "$JAR_D" "$BASE_URL/api/v1/messages/$attachment_id/attachment")
  attachment_unrelated=$(api_status "$JAR_A" GET "/api/v1/messages/$attachment_id/attachment" /dev/null)
  if [ "$attachment_download" = "200" ] \
     && grep -q "E2E synthetic message attachment" "$WORKDIR/msg_attachment_download.txt" \
     && [ "$attachment_unrelated" = "404" ]; then
    pass "Recipient downloads the attachment; unrelated Admin gets 404"
  else
    fail "message attachment download/privacy failed ($attachment_download/$attachment_unrelated)"
  fi
else
  fail "messaging E2E could not resolve seeded staff ids or the temporary Nurse login"
fi

# Patient creation is Doctor/Admin only; the invalid-body doctor probe proves
# the authorized route is reachable without creating test data.
patient_denied=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE_URL/api/v1/patients" \
  -b "$JAR_N" -c "$JAR_N" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_N")" \
  --data '{"given_name":"E2E","family_name":"NurseDenied"}')
if [ "$patient_denied" = "403" ]; then
  pass "nurse cannot create patients -> 403"
else
  fail "nurse patient creation -> $patient_denied (expected 403)"
fi
patient_doctor=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE_URL/api/v1/patients" \
  -b "$JAR_D" -c "$JAR_D" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $(csrf_token "$JAR_D")" \
  --data '{}')
if [ "$patient_doctor" = "400" ]; then
  pass "doctor reaches patient-create validation -> 400 (patient:write)"
else
  fail "doctor patient creation -> $patient_doctor (expected 400)"
fi

# tech_admin no longer exists: granting it is a validation error (400).
if [ -n "$new_user_id" ]; then
  invalid_role_status=$(curl -s -o "$WORKDIR/grant_invalid.json" -w '%{http_code}' -X POST "$BASE_URL/api/v1/staff/$new_user_id/roles" \
    -b "$JAR_A" -c "$JAR_A" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_token "$JAR_A")" \
    --data '{"role": "tech_admin"}')
  if [ "$invalid_role_status" = "400" ]; then
    pass "granting role tech_admin -> 400 (removed role key is rejected)"
  else
    fail "granting role tech_admin -> $invalid_role_status (expected 400)"
  fi
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
    "message.attachment.download",
    "appointment.create", "appointment.update", "appointment.cancel",
    "appointment.complete", "appointment.history.export",
}
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
emitted = {item["action"] for item in data.get("items", [])}
print(",".join(sorted(required - emitted)))
PY
)
if [ -z "$missing_actions" ]; then
  pass "audit rows cover the workflow (login, denials, reads, ai.ask, staff/role, appointments)"
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
    "DEMO_NURSE_PASSWORD",
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
  if [ -n "${completion_id:-}" ] && [ -n "${history_range:-}" ]; then
    api_status "$JAR_D2" GET "/api/v1/appointments/history?$history_range" "$WORKDIR/history_after.json" >/dev/null
    if grep -q "$completion_id" "$WORKDIR/history_after.json"; then
      pass "appointment history persisted across restart"
    else
      fail "appointment history missing after restart"
    fi
  fi
else
  echo "-- step 10: skipped (--no-docker)"
fi

# --- 11. cleanup temporary E2E staff account -------------------------------
if [ -n "${E2E_STAFF_EMAIL:-}" ] && [ -n "${new_user_id:-}" ]; then
  echo "-- step 11: cleanup temporary E2E staff account"
  if [ "$DOCKER_STEPS" = "1" ]; then
    if $COMPOSE_CMD exec -T app python /app/scripts/cleanup_e2e_staff.py --email "$E2E_STAFF_EMAIL" >/dev/null 2>&1; then
      pass "soft-deleted the temporary E2E staff account"
    else
      fail "temporary E2E staff account cleanup failed"
    fi
  else
    deactivate_status=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$BASE_URL/api/v1/staff/$new_user_id" \
      -b "$JAR_A" -c "$JAR_A" \
      -H "Content-Type: application/json" \
      -H "X-CSRF-Token: $(csrf_token "$JAR_A")" \
      --data '{"is_active": false}')
    if [ "$deactivate_status" = "200" ]; then
      pass "deactivated the temporary E2E staff account (no Docker soft-delete)"
    else
      fail "temporary E2E staff account deactivation failed"
    fi
  fi
else
  echo "-- step 11: skipped (no temporary staff account)"
fi

echo "== summary =="
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL STEPS PASSED"
  exit 0
fi
echo "$FAILURES STEP(S) FAILED"
exit 1
