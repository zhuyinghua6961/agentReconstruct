#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
RUNTIME_DIR_DEFAULT="$PROJECT_ROOT/.runtime"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  RUNTIME_DIR_DEFAULT="$RESOURCE_DIR/runtime/dev/patent"
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/patent"
fi

export PATENT_SERVICE_RUNTIME_ROOT="${PATENT_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"
export PATENT_SERVICE_LOG_ROOT="${PATENT_SERVICE_LOG_ROOT:-$LOG_DIR_DEFAULT}"
export PATENT_PORT="${PATENT_PORT:-8010}"

PID_FILE="$PATENT_SERVICE_RUNTIME_ROOT/patent-gunicorn.pid"
STARTUP_LOG_FILE="$PATENT_SERVICE_LOG_ROOT/patent-startup.log"
ACCESS_LOG_FILE="$PATENT_SERVICE_LOG_ROOT/patent-access.log"
ERROR_LOG_FILE="$PATENT_SERVICE_LOG_ROOT/patent-error.log"
APP_LOG_FILE="${PATENT_APP_LOG_FILE:-$PATENT_SERVICE_LOG_ROOT/patent-app.log}"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$APP_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "patent gunicorn running: pid=$PID port=$PATENT_PORT"
    ss -ltnp "( sport = :$PATENT_PORT )" 2>/dev/null || true
    print_logs
    exit 0
  fi
  echo "patent gunicorn stale pid: ${PID:-unknown}"
  print_logs
  exit 1
fi

echo "patent gunicorn not running"
ss -ltnp "( sport = :$PATENT_PORT )" 2>/dev/null || true
print_logs
