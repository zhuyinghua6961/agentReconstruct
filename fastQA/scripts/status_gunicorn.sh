#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
RUNTIME_DIR_DEFAULT="$PROJECT_ROOT/.runtime"
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  RUNTIME_DIR_DEFAULT="$RESOURCE_DIR/runtime/dev/fastQA"
fi
export FASTQA_SERVICE_RUNTIME_ROOT="${FASTQA_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"
export APP_PORT="${APP_PORT:-8008}"
export FASTAPI_PORT="${FASTAPI_PORT:-$APP_PORT}"
PID_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-gunicorn.pid"
ACCESS_LOG_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-access.log"
ERROR_LOG_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-error.log"
APP_LOG_FILE="${FASTQA_APP_LOG_FILE:-$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-app.log}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "fastQA gunicorn not running"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$APP_LOG_FILE"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
  echo "fastQA gunicorn running: pid=$PID"
  ss -ltnp "( sport = :$FASTAPI_PORT )" 2>/dev/null || true
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$APP_LOG_FILE"
  exit 0
fi

echo "fastQA gunicorn stale pid: $PID"
echo "access_log=$ACCESS_LOG_FILE"
echo "error_log=$ERROR_LOG_FILE"
echo "app_log=$APP_LOG_FILE"
exit 1
