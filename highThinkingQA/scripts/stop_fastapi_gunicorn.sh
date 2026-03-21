#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/../resource" 2>/dev/null && pwd || true)"
SERVICE_RUNTIME_ROOT_DEFAULT="$ROOT_DIR/.runtime"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  SERVICE_RUNTIME_ROOT_DEFAULT="$RESOURCE_DIR/runtime/dev/highThinkingQA"
fi

export HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="${HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT:-$SERVICE_RUNTIME_ROOT_DEFAULT}"
PID_FILE="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/gunicorn.pid"
PORT="${APP_PORT:-8009}"

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
  fuser -k "${PORT}/tcp" 2>/dev/null || true
fi

rm -f "$PID_FILE"

if ss -ltn "( sport = :$PORT )" 2>/dev/null | rg -q ":${PORT}\\b"; then
  echo "gunicorn stop incomplete: port ${PORT} still in use"
  exit 1
fi

echo "gunicorn stopped"
