#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
PID_FILE="$RUNTIME_DIR/gateway-gunicorn.pid"
LOG_FILE="$RUNTIME_DIR/gateway-gunicorn.log"
mkdir -p "$RUNTIME_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "gateway gunicorn already running: pid=$(cat "$PID_FILE")"
  exit 0
fi

export GATEWAY_PORT="${GATEWAY_PORT:-8101}"
export GATEWAY_GUNICORN_WORKERS="${GATEWAY_GUNICORN_WORKERS:-8}"
export PUBLIC_BACKEND_BASE_URL="${PUBLIC_BACKEND_BASE_URL:-http://127.0.0.1:8102}"
export FAST_BACKEND_BASE_URL="${FAST_BACKEND_BASE_URL:-http://127.0.0.1:8008}"
export THINKING_BACKEND_BASE_URL="${THINKING_BACKEND_BASE_URL:-http://127.0.0.1:8009}"
export PATENT_BACKEND_BASE_URL="${PATENT_BACKEND_BASE_URL:-http://127.0.0.1:8010}"
export GATEWAY_CONVERSATION_FILE_PROVIDER="${GATEWAY_CONVERSATION_FILE_PROVIDER:-public_http}"

conda run --no-capture-output -n agent gunicorn \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --chdir "$PROJECT_ROOT" \
  --bind "0.0.0.0:${GATEWAY_PORT}" \
  --workers "${GATEWAY_GUNICORN_WORKERS}" \
  --timeout 600 \
  --daemon \
  --pid "$PID_FILE" \
  --access-logfile "$LOG_FILE" \
  --error-logfile "$LOG_FILE"

sleep 2
if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "gateway gunicorn failed to start; inspect $LOG_FILE"
  exit 1
fi

echo "gateway gunicorn started: pid=$(cat "$PID_FILE") port=${GATEWAY_PORT} log=$LOG_FILE"
