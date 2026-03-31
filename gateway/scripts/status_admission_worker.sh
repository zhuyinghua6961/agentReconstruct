#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
PID_FILE="$PROJECT_ROOT/.runtime/gateway-admission-worker.pid"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/gateway"
fi
STARTUP_LOG_FILE="$LOG_DIR_DEFAULT/gateway-admission-worker-startup.log"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "gateway admission worker running: pid=$PID"
    echo "startup_log=$STARTUP_LOG_FILE"
    exit 0
  fi
  echo "gateway admission worker stale pid: ${PID:-unknown}"
  echo "startup_log=$STARTUP_LOG_FILE"
  exit 1
fi

echo "gateway admission worker not running"
echo "startup_log=$STARTUP_LOG_FILE"
