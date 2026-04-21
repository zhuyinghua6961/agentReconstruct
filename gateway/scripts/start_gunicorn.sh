#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
CONFIG_DIR_DEFAULT="$PROJECT_ROOT"
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/gateway"
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/gateway"
fi
PID_FILE="$RUNTIME_DIR/gateway-gunicorn.pid"
STARTUP_LOG_FILE="$LOG_DIR_DEFAULT/gateway-startup.log"
ACCESS_LOG_FILE="$LOG_DIR_DEFAULT/gateway-access.log"
ERROR_LOG_FILE="$LOG_DIR_DEFAULT/gateway-error.log"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR_DEFAULT"

export GATEWAY_PORT="${GATEWAY_PORT:-8101}"
export GATEWAY_GUNICORN_WORKERS="${GATEWAY_GUNICORN_WORKERS:-4}"
export GATEWAY_RUNTIME_ROLE="${GATEWAY_RUNTIME_ROLE:-web}"
export GATEWAY_ENV_FILES="${GATEWAY_ENV_FILES:-$CONFIG_DIR_DEFAULT/config.env:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$PROJECT_ROOT/.env}"
export PUBLIC_BACKEND_BASE_URL="${PUBLIC_BACKEND_BASE_URL:-http://127.0.0.1:8102}"
export FAST_BACKEND_BASE_URL="${FAST_BACKEND_BASE_URL:-http://127.0.0.1:8008}"
export THINKING_BACKEND_BASE_URL="${THINKING_BACKEND_BASE_URL:-http://127.0.0.1:8009}"
export PATENT_BACKEND_BASE_URL="${PATENT_BACKEND_BASE_URL:-http://127.0.0.1:8010}"
export GATEWAY_CONVERSATION_FILE_PROVIDER="${GATEWAY_CONVERSATION_FILE_PROVIDER:-public_http}"

load_env_files() {
  local env_files="$1"
  local old_allexport
  old_allexport="$(set +o | rg '^set \\+o allexport$' || true)"
  set -a
  IFS=':' read -r -a files <<< "$env_files"
  for file in "${files[@]}"; do
    [[ -n "${file:-}" ]] || continue
    [[ -f "$file" ]] || continue
    # shellcheck disable=SC1090
    source "$file"
  done
  set +a
  if [[ -n "$old_allexport" ]]; then
    eval "$old_allexport"
  fi
}

load_env_files "$GATEWAY_ENV_FILES"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "gateway gunicorn already running: pid=$EXISTING_PID"
    print_logs
    exit 0
  fi
  rm -f "$PID_FILE"
fi

: > "$STARTUP_LOG_FILE"
: > "$ACCESS_LOG_FILE"
: > "$ERROR_LOG_FILE"

nohup env PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  conda run --no-capture-output -n agent gunicorn \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --config "$PROJECT_ROOT/gunicorn.conf.py" \
  --chdir "$PROJECT_ROOT" \
  --bind "0.0.0.0:${GATEWAY_PORT}" \
  --workers "${GATEWAY_GUNICORN_WORKERS}" \
  --timeout 600 \
  --pid "$PID_FILE" \
  --capture-output \
  --access-logfile "$ACCESS_LOG_FILE" \
  --error-logfile "$ERROR_LOG_FILE" \
  >"$STARTUP_LOG_FILE" 2>&1 &

LAUNCHER_PID=$!
for _ in $(seq 1 60); do
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "gateway gunicorn started: pid=$PID port=${GATEWAY_PORT}"
      print_logs
      exit 0
    fi
  fi
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "gateway gunicorn failed to start; inspect $STARTUP_LOG_FILE and $ERROR_LOG_FILE"
print_logs
exit 1
