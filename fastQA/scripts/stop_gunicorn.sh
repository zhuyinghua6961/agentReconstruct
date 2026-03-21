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

terminate_pid() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  terminate_pid "$PID"
fi

if command -v fuser >/dev/null 2>&1; then
  fuser -k "${FASTAPI_PORT}/tcp" 2>/dev/null || true
fi

rm -f "$PID_FILE"

if ss -ltn "( sport = :$FASTAPI_PORT )" 2>/dev/null | rg -q ":${FASTAPI_PORT}\\b"; then
  echo "fastQA gunicorn stop incomplete: port ${FASTAPI_PORT} still in use"
  exit 1
fi

echo "fastQA gunicorn stopped"
