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
PORT="${HIGHTHINKINGQA_PORT:-${APP_PORT:-8009}}"
PID_FILE="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/gunicorn.pid"
STARTUP_LOG_FILE="$APP_RUNTIME_LOGS_DIR/gunicorn-startup.log"
ACCESS_LOG_FILE="$APP_RUNTIME_LOGS_DIR/gunicorn-access.log"
ERROR_LOG_FILE="$APP_RUNTIME_LOGS_DIR/gunicorn-error.log"
APP_LOG_FILE="$APP_RUNTIME_LOGS_DIR/highThinkingQA-app.log"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$APP_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "gunicorn status: running pid=$PID port=$PORT"
    ss -ltnp "( sport = :$PORT )" 2>/dev/null || true
    print_logs
    exit 0
  fi
  echo "gunicorn status: stale pid=${PID:-unknown}"
  print_logs
  exit 1
fi

echo "gunicorn status: not running"
ss -ltnp "( sport = :$PORT )" 2>/dev/null || true
print_logs
