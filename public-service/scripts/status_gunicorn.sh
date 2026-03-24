#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
PID_FILE="$PROJECT_ROOT/.runtime/public-service-gunicorn.pid"
PORT="${PUBLIC_SERVICE_PORT:-8102}"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/public-service"
fi
STARTUP_LOG_FILE="$LOG_DIR_DEFAULT/public-service-startup.log"
ACCESS_LOG_FILE="$LOG_DIR_DEFAULT/public-service-access.log"
ERROR_LOG_FILE="$LOG_DIR_DEFAULT/public-service-error.log"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "public-service gunicorn running: pid=$PID port=$PORT"
    ss -ltnp "( sport = :$PORT )" 2>/dev/null || true
    print_logs
    exit 0
  fi
  echo "public-service gunicorn stale pid: ${PID:-unknown}"
  print_logs
  exit 1
fi

echo "public-service gunicorn not running"
ss -ltnp "( sport = :$PORT )" 2>/dev/null || true
print_logs
