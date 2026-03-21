#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
RUNTIME_DIR_DEFAULT="$PROJECT_ROOT/.runtime"
CONFIG_DIR_DEFAULT="$PROJECT_ROOT"
STATE_DIR_DEFAULT="$PROJECT_ROOT"
ASSET_DIR_DEFAULT="$PROJECT_ROOT"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/fastQA"
  STATE_DIR_DEFAULT="$RESOURCE_DIR/state/dev/fastQA"
  RUNTIME_DIR_DEFAULT="$RESOURCE_DIR/runtime/dev/fastQA"
  ASSET_DIR_DEFAULT="$RESOURCE_DIR/assets"
fi

export FASTQA_SERVICE_CONFIG_ROOT="${FASTQA_SERVICE_CONFIG_ROOT:-$CONFIG_DIR_DEFAULT}"
export FASTQA_SERVICE_STATE_ROOT="${FASTQA_SERVICE_STATE_ROOT:-$STATE_DIR_DEFAULT}"
export FASTQA_SERVICE_RUNTIME_ROOT="${FASTQA_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"
export FASTQA_SERVICE_ASSET_ROOT="${FASTQA_SERVICE_ASSET_ROOT:-$ASSET_DIR_DEFAULT}"
export APP_PORT="${APP_PORT:-8008}"
export FASTAPI_PORT="${FASTAPI_PORT:-$APP_PORT}"
export BACKEND_PORT="${BACKEND_PORT:-$FASTAPI_PORT}"
export FASTQA_GUNICORN_WORKERS="${FASTQA_GUNICORN_WORKERS:-8}"

PID_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-gunicorn.pid"
ACCESS_LOG_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-access.log"
ERROR_LOG_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-error.log"
APP_LOG_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-app.log"
mkdir -p "$FASTQA_SERVICE_RUNTIME_ROOT"

export FASTQA_APP_LOG_FILE="${FASTQA_APP_LOG_FILE:-$APP_LOG_FILE}"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "fastQA gunicorn already running: pid=$(cat "$PID_FILE")"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$FASTQA_APP_LOG_FILE"
  exit 0
fi

: > "$ACCESS_LOG_FILE"
: > "$ERROR_LOG_FILE"
: > "$FASTQA_APP_LOG_FILE"

conda run --no-capture-output -n agent gunicorn \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --chdir "$PROJECT_ROOT" \
  --bind "0.0.0.0:${FASTAPI_PORT}" \
  --workers "${FASTQA_GUNICORN_WORKERS}" \
  --timeout 600 \
  --daemon \
  --pid "$PID_FILE" \
  --capture-output \
  --access-logfile "$ACCESS_LOG_FILE" \
  --error-logfile "$ERROR_LOG_FILE"

sleep 2
if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "fastQA gunicorn failed to start; inspect $ERROR_LOG_FILE and $FASTQA_APP_LOG_FILE"
  exit 1
fi

echo "fastQA gunicorn started: pid=$(cat "$PID_FILE") port=${FASTAPI_PORT}"
echo "access_log=$ACCESS_LOG_FILE"
echo "error_log=$ERROR_LOG_FILE"
echo "app_log=$FASTQA_APP_LOG_FILE"
