#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.runtime/gunicorn.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "gunicorn status: not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
  echo "gunicorn status: running pid=$PID"
  ss -ltnp '( sport = :8008 )' 2>/dev/null || true
  exit 0
fi

echo "gunicorn status: stale pid=$PID"
exit 1
