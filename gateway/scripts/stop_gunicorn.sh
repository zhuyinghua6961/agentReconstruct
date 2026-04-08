#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PID_FILE="$PROJECT_ROOT/.runtime/gateway-gunicorn.pid"
PORT="${GATEWAY_PORT:-8101}"

terminate_pid() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 60); do
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
  fuser -k "${PORT}/tcp" 2>/dev/null || true
fi

rm -f "$PID_FILE"
if ss -ltn "( sport = :$PORT )" 2>/dev/null | rg -q ":${PORT}\\b"; then
  echo "gateway gunicorn stop incomplete: port ${PORT} still in use"
  exit 1
fi

echo "gateway gunicorn stopped"
