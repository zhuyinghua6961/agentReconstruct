#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
RUNTIME_DIR_DEFAULT="$PROJECT_ROOT/.runtime"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  RUNTIME_DIR_DEFAULT="$RESOURCE_DIR/runtime/dev/fastQA"
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/fastQA"
fi
export FASTQA_SERVICE_RUNTIME_ROOT="${FASTQA_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"
export FASTQA_SERVICE_LOG_ROOT="${FASTQA_SERVICE_LOG_ROOT:-$LOG_DIR_DEFAULT}"
export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"
export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"
PID_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-gunicorn.pid"
STARTUP_LOG_FILE="$FASTQA_SERVICE_LOG_ROOT/fastqa-startup.log"
ACCESS_LOG_FILE="$FASTQA_SERVICE_LOG_ROOT/fastqa-access.log"
ERROR_LOG_FILE="$FASTQA_SERVICE_LOG_ROOT/fastqa-error.log"
APP_LOG_FILE="${FASTQA_APP_LOG_FILE:-$FASTQA_SERVICE_LOG_ROOT/fastqa-app.log}"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$APP_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "fastQA gunicorn running: pid=$PID port=$FASTAPI_PORT"
    ss -ltnp "( sport = :$FASTAPI_PORT )" 2>/dev/null || true
    print_logs
    exit 0
  fi
  echo "fastQA gunicorn stale pid: ${PID:-unknown}"
  print_logs
  exit 1
fi

echo "fastQA gunicorn not running"
ss -ltnp "( sport = :$FASTAPI_PORT )" 2>/dev/null || true
print_logs
