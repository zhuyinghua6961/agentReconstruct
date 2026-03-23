#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.runtime/gunicorn.pid"
LOG_DIR="$ROOT_DIR/.runtime/logs"

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "gunicorn already running: pid=$PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$ROOT_DIR"
conda run --no-capture-output -n agent \
  gunicorn server_fastapi.asgi:app \
  -c server_fastapi/gunicorn.conf.py \
  --daemon \
  --pid "$PID_FILE" \
  --access-logfile "$LOG_DIR/gunicorn-access.log" \
  --error-logfile "$LOG_DIR/gunicorn-error.log"

sleep 2
echo "gunicorn started: pid=$(cat "$PID_FILE")"
