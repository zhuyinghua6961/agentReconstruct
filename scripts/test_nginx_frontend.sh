#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FRONTEND_NGINX_PORT="${FRONTEND_NGINX_PORT:-9093}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${FRONTEND_NGINX_PORT}}"
AUTH_BEARER_TOKEN="${AUTH_BEARER_TOKEN:-}"
ASK_STREAM_JSON_FILE="${ASK_STREAM_JSON_FILE:-}"
TASK_REQUEST_PAYLOAD_FILE="${TASK_REQUEST_PAYLOAD_FILE:-}"
TMP_DIR="$(mktemp -d)"
HEADERS_FILE="$TMP_DIR/headers.txt"
STREAM_FILE="$TMP_DIR/stream.txt"
RECOVERY_FIRST_FILE="$TMP_DIR/recovery-first.txt"
RECOVERY_SECOND_FILE="$TMP_DIR/recovery-second.txt"
HEALTH_FILE="$TMP_DIR/health.json"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "required tool missing: $1" >&2
    exit 1
  fi
}

require_tool curl
require_tool jq
require_tool rg

echo "checking static homepage via $BASE_URL/"
curl -fsS "$BASE_URL/" | rg -qi '<!doctype html|<html'

echo "checking SPA fallback via $BASE_URL/nonexistent-route"
curl -fsS "$BASE_URL/nonexistent-route" | rg -qi '<!doctype html|<html'

echo "checking proxied health via $BASE_URL/health"
curl -fsS "$BASE_URL/health" >"$HEALTH_FILE"
jq -e '.success == true' "$HEALTH_FILE" >/dev/null

REDIS_ENABLED="$(jq -r '.components.redis.enabled // false' "$HEALTH_FILE")"
REDIS_LIVE_AVAILABLE="$(jq -r '.components.redis.live_available // false' "$HEALTH_FILE")"

echo "redis_enabled=$REDIS_ENABLED"
echo "redis_live_available=$REDIS_LIVE_AVAILABLE"

if [[ -z "$AUTH_BEARER_TOKEN" || -z "$ASK_STREAM_JSON_FILE" || -z "$TASK_REQUEST_PAYLOAD_FILE" ]]; then
  echo "skipping streaming and task recovery checks; set AUTH_BEARER_TOKEN, ASK_STREAM_JSON_FILE, TASK_REQUEST_PAYLOAD_FILE"
  exit 0
fi

if [[ "$REDIS_ENABLED" != "true" || "$REDIS_LIVE_AVAILABLE" != "true" ]]; then
  echo "skipping task recovery checks because REDIS is not live through gateway health"
  exit 0
fi

echo "checking SSE response headers and streaming body"
curl -sS -N \
  -D "$HEADERS_FILE" \
  -H "Authorization: Bearer $AUTH_BEARER_TOKEN" \
  -H "Accept: text/event-stream" \
  -H "Content-Type: application/json" \
  --data "@$ASK_STREAM_JSON_FILE" \
  --max-time "${SSE_MAX_SECONDS:-20}" \
  "$BASE_URL/api/thinking/ask_stream" >"$STREAM_FILE" || true

rg -qi 'text/event-stream' "$HEADERS_FILE"
rg -q '^data:' "$STREAM_FILE"

echo "creating refresh-survivable task"
TASK_ID="$(
  curl -fsS \
    -H "Authorization: Bearer $AUTH_BEARER_TOKEN" \
    -H "Content-Type: application/json" \
    --data "@$TASK_REQUEST_PAYLOAD_FILE" \
    "$BASE_URL/api/v1/tasks" \
  | jq -r '.task_id // empty'
)"

if [[ -z "$TASK_ID" ]]; then
  echo "task creation did not return task_id" >&2
  exit 1
fi

echo "reading first task event window"
curl -sS -N \
  -H "Authorization: Bearer $AUTH_BEARER_TOKEN" \
  -H "Accept: text/event-stream" \
  --max-time "${TASK_EVENT_FIRST_WINDOW_SECONDS:-5}" \
  "$BASE_URL/api/v1/tasks/$TASK_ID/events?after_seq=0" >"$RECOVERY_FIRST_FILE" || true

LAST_SEQ="$(
  sed -n 's/^data:[[:space:]]*//p' "$RECOVERY_FIRST_FILE" \
  | jq -r '.seq // empty' \
  | tail -n 1
)"

if [[ -z "$LAST_SEQ" ]]; then
  echo "did not capture a replay cursor from first stream" >&2
  exit 1
fi

echo "reconnecting with after_seq=$LAST_SEQ"
curl -sS -N \
  -H "Authorization: Bearer $AUTH_BEARER_TOKEN" \
  -H "Accept: text/event-stream" \
  --max-time "${TASK_EVENT_SECOND_WINDOW_SECONDS:-10}" \
  "$BASE_URL/api/v1/tasks/$TASK_ID/events?after_seq=$LAST_SEQ" >"$RECOVERY_SECOND_FILE" || true

sed -n 's/^data:[[:space:]]*//p' "$RECOVERY_SECOND_FILE" | jq -e '.seq > 0' >/dev/null
echo "frontend nginx verification completed"
