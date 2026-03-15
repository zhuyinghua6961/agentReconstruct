#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.runtime/gunicorn.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "gunicorn not running: pid file missing"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  sleep 2
  echo "gunicorn stopped: pid=$PID"
else
  echo "stale pid file removed: pid=$PID"
fi

rm -f "$PID_FILE"
