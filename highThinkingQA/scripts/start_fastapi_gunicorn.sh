#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/../resource" 2>/dev/null && pwd || true)"
SERVICE_CONFIG_ROOT_DEFAULT="$ROOT_DIR"
SERVICE_STATE_ROOT_DEFAULT="$ROOT_DIR"
SERVICE_RUNTIME_ROOT_DEFAULT="$ROOT_DIR/.runtime"
SERVICE_LOG_ROOT_DEFAULT="$ROOT_DIR/.runtime/logs"
SERVICE_ASSET_ROOT_DEFAULT="$ROOT_DIR"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  SERVICE_CONFIG_ROOT_DEFAULT="$RESOURCE_DIR/config/services/highThinkingQA"
  SERVICE_STATE_ROOT_DEFAULT="$RESOURCE_DIR/state/dev/highThinkingQA"
  SERVICE_RUNTIME_ROOT_DEFAULT="$RESOURCE_DIR/runtime/dev/highThinkingQA"
  SERVICE_LOG_ROOT_DEFAULT="$RESOURCE_DIR/logs/dev/highThinkingQA"
  if [[ -d "$RESOURCE_DIR/assets/prompts" ]]; then
    SERVICE_ASSET_ROOT_DEFAULT="$RESOURCE_DIR/assets"
  fi
fi

export HIGHTHINKINGQA_SERVICE_CONFIG_ROOT="${HIGHTHINKINGQA_SERVICE_CONFIG_ROOT:-$SERVICE_CONFIG_ROOT_DEFAULT}"
export HIGHTHINKINGQA_SERVICE_STATE_ROOT="${HIGHTHINKINGQA_SERVICE_STATE_ROOT:-$SERVICE_STATE_ROOT_DEFAULT}"
export HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="${HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT:-$SERVICE_RUNTIME_ROOT_DEFAULT}"
export HIGHTHINKINGQA_SERVICE_ASSET_ROOT="${HIGHTHINKINGQA_SERVICE_ASSET_ROOT:-$SERVICE_ASSET_ROOT_DEFAULT}"
export APP_RUNTIME_LOGS_DIR="${APP_RUNTIME_LOGS_DIR:-$SERVICE_LOG_ROOT_DEFAULT}"
export APP_PORT="${APP_PORT:-8009}"

PID_FILE="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/gunicorn.pid"
LOG_DIR="$APP_RUNTIME_LOGS_DIR"
STARTUP_LOG_FILE="$LOG_DIR/gunicorn-startup.log"
ACCESS_LOG_FILE="$LOG_DIR/gunicorn-access.log"
ERROR_LOG_FILE="$LOG_DIR/gunicorn-error.log"
APP_LOG_FILE="$LOG_DIR/highThinkingQA-app.log"

mkdir -p "$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT" "$LOG_DIR"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$APP_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "gunicorn already running: pid=$EXISTING_PID"
    print_logs
    exit 0
  fi
  rm -f "$PID_FILE"
fi

: > "$STARTUP_LOG_FILE"
: > "$ACCESS_LOG_FILE"
: > "$ERROR_LOG_FILE"
: > "$APP_LOG_FILE"

cd "$ROOT_DIR"
nohup conda run --no-capture-output -n agent   gunicorn server_fastapi.asgi:app   -c server_fastapi/gunicorn.conf.py   --pid "$PID_FILE"   --capture-output   --access-logfile "$ACCESS_LOG_FILE"   --error-logfile "$ERROR_LOG_FILE"   >"$STARTUP_LOG_FILE" 2>&1 &

LAUNCHER_PID=$!
for _ in $(seq 1 60); do
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "gunicorn started: pid=$PID port=${APP_PORT}"
      print_logs
      exit 0
    fi
  fi
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "gunicorn failed to start; inspect $STARTUP_LOG_FILE and $ERROR_LOG_FILE"
print_logs
exit 1
