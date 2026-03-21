#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PID_FILE="$PROJECT_ROOT/.runtime/public-service-gunicorn.pid"
PORT="${PUBLIC_SERVICE_PORT:-8102}"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "public-service gunicorn running: pid=$(cat "$PID_FILE") port=$PORT"
else
  echo "public-service gunicorn not running"
fi

ss -ltnp 2>/dev/null | rg ":${PORT}\\b" || true
