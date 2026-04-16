#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${TUNNEL_RUNTIME_DIR:-$ROOT_DIR/resource/runtime/dev/tunnel}"
LOG_DIR="${TUNNEL_LOG_DIR:-$ROOT_DIR/resource/logs/dev/tunnel}"

AUTOSSH_BIN="${AUTOSSH_BIN:-/home/cqy/local/bin/autossh}"
SSH_KEY_PATH="${SSH_KEY_PATH:-/home/cqy/.ssh/aliyun_ecs}"
REMOTE_USER_HOST="${REMOTE_USER_HOST:-zyh@182.92.69.36}"
REMOTE_BIND_HOST="${REMOTE_BIND_HOST:-127.0.0.1}"
REMOTE_BIND_PORT="${REMOTE_BIND_PORT:-18101}"
LOCAL_GATEWAY_HOST="${LOCAL_GATEWAY_HOST:-127.0.0.1}"
LOCAL_GATEWAY_PORT="${LOCAL_GATEWAY_PORT:-8101}"
LOCAL_HEALTH_URL="${LOCAL_HEALTH_URL:-http://${LOCAL_GATEWAY_HOST}:${LOCAL_GATEWAY_PORT}/healthz}"
REMOTE_HEALTH_URL="${REMOTE_HEALTH_URL:-http://${REMOTE_BIND_HOST}:${REMOTE_BIND_PORT}/healthz}"

LOCK_FILE="$RUNTIME_DIR/aliyun-gateway-autossh-check.lock"
AUTOSSH_PID_FILE="$RUNTIME_DIR/aliyun-gateway-autossh.pid"
AUTOSSH_LOGFILE="${AUTOSSH_LOGFILE:-$LOG_DIR/aliyun-gateway-autossh.log}"

SSH_CONNECT_TIMEOUT_SECONDS="${SSH_CONNECT_TIMEOUT_SECONDS:-10}"
HTTP_CONNECT_TIMEOUT_SECONDS="${HTTP_CONNECT_TIMEOUT_SECONDS:-3}"
HTTP_MAX_TIME_SECONDS="${HTTP_MAX_TIME_SECONDS:-5}"
PROBE_RETRIES="${PROBE_RETRIES:-3}"
PROBE_SLEEP_SECONDS="${PROBE_SLEEP_SECONDS:-2}"
MAX_AUTOSSH_LOG_BYTES="${MAX_AUTOSSH_LOG_BYTES:-1048576}"

# Default tunnel spec for the current deployment target:
# -R 127.0.0.1:18101:127.0.0.1:8101
AUTOSSH_TUNNEL_SPEC="-R ${REMOTE_BIND_HOST}:${REMOTE_BIND_PORT}:${LOCAL_GATEWAY_HOST}:${LOCAL_GATEWAY_PORT}"
AUTOSSH_PGREP_PATTERN="autossh .*${REMOTE_BIND_HOST}:${REMOTE_BIND_PORT}:${LOCAL_GATEWAY_HOST}:${LOCAL_GATEWAY_PORT} .*${REMOTE_USER_HOST}"
SSH_PGREP_PATTERN="ssh .*${REMOTE_BIND_HOST}:${REMOTE_BIND_PORT}:${LOCAL_GATEWAY_HOST}:${LOCAL_GATEWAY_PORT} .*${REMOTE_USER_HOST}"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "aliyun gateway tunnel check already running"
  exit 0
fi

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

require_bin() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "missing required command: $1"
    exit 1
  fi
}

trim_log_if_needed() {
  local path="$1"
  local max_bytes="$2"
  [[ -f "$path" ]] || return 0
  local size
  size="$(wc -c <"$path")"
  if [[ "${size:-0}" -le "$max_bytes" ]]; then
    return 0
  fi
  tail -c "$max_bytes" "$path" >"${path}.tmp"
  mv "${path}.tmp" "$path"
}

pid_is_running() {
  local pid="$1"
  [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null
}

local_gateway_healthy() {
  curl -fsS \
    --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" \
    --max-time "$HTTP_MAX_TIME_SECONDS" \
    "$LOCAL_HEALTH_URL" >/dev/null
}

remote_tunnel_healthy() {
  ssh \
    -i "$SSH_KEY_PATH" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=yes \
    -o ConnectTimeout="$SSH_CONNECT_TIMEOUT_SECONDS" \
    "$REMOTE_USER_HOST" \
    "curl -fsS --connect-timeout $HTTP_CONNECT_TIMEOUT_SECONDS --max-time $HTTP_MAX_TIME_SECONDS '$REMOTE_HEALTH_URL' >/dev/null"
}

find_running_autossh_pid() {
  pgrep -af "$AUTOSSH_PGREP_PATTERN" | awk '$0 !~ /check_aliyun_gateway_tunnel/ {print $1; exit}'
}

kill_stale_tunnel_processes() {
  local pid=""
  while read -r pid; do
    [[ -n "${pid:-}" ]] || continue
    kill "$pid" 2>/dev/null || true
  done < <(pgrep -af "$AUTOSSH_PGREP_PATTERN" | awk '$0 !~ /check_aliyun_gateway_tunnel/ {print $1}')

  while read -r pid; do
    [[ -n "${pid:-}" ]] || continue
    kill "$pid" 2>/dev/null || true
  done < <(pgrep -af "$SSH_PGREP_PATTERN" | awk '$0 !~ /check_aliyun_gateway_tunnel/ {print $1}')

  sleep 1
}

start_tunnel() {
  trim_log_if_needed "$AUTOSSH_LOGFILE" "$MAX_AUTOSSH_LOG_BYTES"

  AUTOSSH_GATETIME=0 AUTOSSH_LOGFILE="$AUTOSSH_LOGFILE" \
    nohup "$AUTOSSH_BIN" \
      -M 0 \
      -N \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o StrictHostKeyChecking=yes \
      -o ConnectTimeout="$SSH_CONNECT_TIMEOUT_SECONDS" \
      -i "$SSH_KEY_PATH" \
      $AUTOSSH_TUNNEL_SPEC \
      "$REMOTE_USER_HOST" >/dev/null 2>&1 &

  local autossh_pid="$!"
  echo "$autossh_pid" >"$AUTOSSH_PID_FILE"
  log "started autossh pid=$autossh_pid"
}

require_bin "$AUTOSSH_BIN"
require_bin ssh
require_bin curl
require_bin pgrep
require_bin flock

if ! local_gateway_healthy; then
  log "local gateway health check failed: $LOCAL_HEALTH_URL"
  exit 1
fi

CURRENT_PID="$(cat "$AUTOSSH_PID_FILE" 2>/dev/null || true)"
if ! pid_is_running "$CURRENT_PID"; then
  CURRENT_PID="$(find_running_autossh_pid || true)"
  if pid_is_running "$CURRENT_PID"; then
    echo "$CURRENT_PID" >"$AUTOSSH_PID_FILE"
  else
    rm -f "$AUTOSSH_PID_FILE"
  fi
fi

if pid_is_running "$CURRENT_PID" && remote_tunnel_healthy; then
  log "aliyun gateway tunnel healthy: pid=$CURRENT_PID remote=$REMOTE_HEALTH_URL"
  exit 0
fi

log "aliyun gateway tunnel unhealthy or absent; restarting"
kill_stale_tunnel_processes
rm -f "$AUTOSSH_PID_FILE"
start_tunnel

for attempt in $(seq 1 "$PROBE_RETRIES"); do
  CURRENT_PID="$(cat "$AUTOSSH_PID_FILE" 2>/dev/null || true)"
  if pid_is_running "$CURRENT_PID" && remote_tunnel_healthy; then
    log "aliyun gateway tunnel recovered: pid=$CURRENT_PID remote=$REMOTE_HEALTH_URL"
    exit 0
  fi
  sleep "$PROBE_SLEEP_SECONDS"
done

CURRENT_PID="$(cat "$AUTOSSH_PID_FILE" 2>/dev/null || true)"
log "aliyun gateway tunnel recovery failed: pid=${CURRENT_PID:-missing} remote=$REMOTE_HEALTH_URL"
exit 1
