#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PID_FILE="$PROJECT_ROOT/.runtime/gateway-gunicorn.pid"
PORT="${GATEWAY_PORT:-8101}"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "gateway gunicorn running: pid=$(cat "$PID_FILE") port=$PORT"
else
  echo "gateway gunicorn not running"
fi

ss -ltnp 2>/dev/null | rg ":${PORT}\\b" || true
