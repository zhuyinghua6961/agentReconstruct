#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
APP_DIR="$PROJECT_ROOT/backend"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
PID_FILE="$RUNTIME_DIR/public-service-gunicorn.pid"
LOG_FILE="$RUNTIME_DIR/public-service-gunicorn.log"
mkdir -p "$RUNTIME_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "public-service gunicorn already running: pid=$(cat "$PID_FILE")"
  exit 0
fi

export PUBLIC_SERVICE_PORT="${PUBLIC_SERVICE_PORT:-8102}"
export PUBLIC_SERVICE_ENV_FILES="${PUBLIC_SERVICE_ENV_FILES:-$PROJECT_ROOT/config.shared.env:$PROJECT_ROOT/config.secret.env}"
export PUBLIC_SERVICE_GUNICORN_WORKERS="${PUBLIC_SERVICE_GUNICORN_WORKERS:-8}"

conda run --no-capture-output -n agent gunicorn \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --chdir "$APP_DIR" \
  --bind "0.0.0.0:${PUBLIC_SERVICE_PORT}" \
  --workers "${PUBLIC_SERVICE_GUNICORN_WORKERS}" \
  --timeout 600 \
  --daemon \
  --pid "$PID_FILE" \
  --access-logfile "$LOG_FILE" \
  --error-logfile "$LOG_FILE"

sleep 2
if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "public-service gunicorn failed to start; inspect $LOG_FILE"
  exit 1
fi

echo "public-service gunicorn started: pid=$(cat "$PID_FILE") port=${PUBLIC_SERVICE_PORT} log=$LOG_FILE"
