#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
RUNTIME_DIR_DEFAULT="$PROJECT_ROOT/.runtime"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  RUNTIME_DIR_DEFAULT="$RESOURCE_DIR/runtime/dev/patent"
fi

export PATENT_SERVICE_RUNTIME_ROOT="${PATENT_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"
export PATENT_PORT="${PATENT_PORT:-8010}"

PID_FILE="$PATENT_SERVICE_RUNTIME_ROOT/patent-gunicorn.pid"

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
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  terminate_pid "$PID"
fi

if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PATENT_PORT}/tcp" 2>/dev/null || true
fi

rm -f "$PID_FILE"
if ss -ltn "( sport = :$PATENT_PORT )" 2>/dev/null | rg -q ":${PATENT_PORT}\\b"; then
  echo "patent gunicorn stop incomplete: port ${PATENT_PORT} still in use"
  exit 1
fi

echo "patent gunicorn stopped"
