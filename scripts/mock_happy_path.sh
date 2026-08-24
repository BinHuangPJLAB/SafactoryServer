#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:-safactory-local-development-key}"
AUTH_HEADER=( -H "Authorization: Bearer $API_KEY" )

curl -sS "${AUTH_HEADER[@]}" "$BASE_URL/v1/models"

JOB_JSON="$(curl -sS -X POST "$BASE_URL/v1/jobs" \
  "${AUTH_HEADER[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"model_id":"model_glm_001","range_id":"range_web_001"}')"
echo "$JOB_JSON"
JOB_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$JOB_JSON")"

sleep 3
SESSIONS_JSON="$(curl -sS "${AUTH_HEADER[@]}" "$BASE_URL/v1/jobs/sessions?job_id=$JOB_ID")"
echo "$SESSIONS_JSON"
SESSION_IDS="$(python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)["session_ids"]))' <<<"$SESSIONS_JSON")"

for SESSION_ID in $SESSION_IDS; do
  curl -sS "${AUTH_HEADER[@]}" "$BASE_URL/v1/sessions/result?job_id=$JOB_ID&session_id=$SESSION_ID"
  STEPS_JSON="$(curl -sS "${AUTH_HEADER[@]}" "$BASE_URL/v1/sessions/steps?job_id=$JOB_ID&session_id=$SESSION_ID")"
  echo "$STEPS_JSON"
  STEP_IDS="$(python3 -c 'import json,sys; print(" ".join(step["step_id"] for step in json.load(sys.stdin)["steps"]))' <<<"$STEPS_JSON")"
  for STEP_ID in $STEP_IDS; do
    curl -sS "${AUTH_HEADER[@]}" "$BASE_URL/v1/sessions/steps/trajectory?job_id=$JOB_ID&session_id=$SESSION_ID&step_id=$STEP_ID"
  done
done
