#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
source "$PROJECT_ROOT/../scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
RUNTIME_DIR_DEFAULT="$PROJECT_ROOT/.runtime"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
CONFIG_DIR_DEFAULT="$PROJECT_ROOT"
STATE_DIR_DEFAULT="$PROJECT_ROOT"
ASSET_DIR_DEFAULT="$PROJECT_ROOT"
SHARED_CONFIG_DIR_DEFAULT=""
FASTQA_SHARED_ENV_FILES_DEFAULT=""

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/fastQA"
  SHARED_CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/shared"
  FASTQA_SHARED_ENV_FILES_DEFAULT="$SHARED_CONFIG_DIR_DEFAULT/infrastructure.shared.env:$SHARED_CONFIG_DIR_DEFAULT/model-endpoints.shared.env:$SHARED_CONFIG_DIR_DEFAULT/infrastructure.secret.env"
  STATE_DIR_DEFAULT="$RESOURCE_DIR/state/dev/fastQA"
  RUNTIME_DIR_DEFAULT="$RESOURCE_DIR/runtime/dev/fastQA"
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/fastQA"
  ASSET_DIR_DEFAULT="$RESOURCE_DIR/assets"
fi

export FASTQA_SERVICE_CONFIG_ROOT="${FASTQA_SERVICE_CONFIG_ROOT:-$CONFIG_DIR_DEFAULT}"
export FASTQA_SERVICE_STATE_ROOT="${FASTQA_SERVICE_STATE_ROOT:-$STATE_DIR_DEFAULT}"
export FASTQA_SERVICE_RUNTIME_ROOT="${FASTQA_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"
export FASTQA_SERVICE_ASSET_ROOT="${FASTQA_SERVICE_ASSET_ROOT:-$ASSET_DIR_DEFAULT}"
export FASTQA_SERVICE_LOG_ROOT="${FASTQA_SERVICE_LOG_ROOT:-$LOG_DIR_DEFAULT}"
export FASTQA_SHARED_ENV_FILES="${FASTQA_SHARED_ENV_FILES:-$FASTQA_SHARED_ENV_FILES_DEFAULT}"
export FASTQA_ENV_FILES="${FASTQA_ENV_FILES:-$FASTQA_SHARED_ENV_FILES:$FASTQA_SERVICE_CONFIG_ROOT/config.env:$FASTQA_SERVICE_CONFIG_ROOT/config.shared.env:$FASTQA_SERVICE_CONFIG_ROOT/config.secret.env:$PROJECT_ROOT/.env}"
export APP_PORT="${APP_PORT:-8008}"
export FASTAPI_PORT="${FASTAPI_PORT:-$APP_PORT}"
export BACKEND_PORT="${BACKEND_PORT:-$FASTAPI_PORT}"
export FASTQA_GUNICORN_WORKERS="${FASTQA_GUNICORN_WORKERS:-4}"

load_env_files_preserving_process_env "$FASTQA_ENV_FILES"

PID_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-gunicorn.pid"
STARTUP_LOG_FILE="$FASTQA_SERVICE_LOG_ROOT/fastqa-startup.log"
ACCESS_LOG_FILE="$FASTQA_SERVICE_LOG_ROOT/fastqa-access.log"
ERROR_LOG_FILE="$FASTQA_SERVICE_LOG_ROOT/fastqa-error.log"
APP_LOG_FILE="$FASTQA_SERVICE_LOG_ROOT/fastqa-app.log"
mkdir -p "$FASTQA_SERVICE_RUNTIME_ROOT" "$FASTQA_SERVICE_LOG_ROOT"

export FASTQA_APP_LOG_FILE="${FASTQA_APP_LOG_FILE:-$APP_LOG_FILE}"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$FASTQA_APP_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "fastQA gunicorn already running: pid=$EXISTING_PID"
    print_logs
    exit 0
  fi
  rm -f "$PID_FILE"
fi

: > "$STARTUP_LOG_FILE"
: > "$ACCESS_LOG_FILE"
: > "$ERROR_LOG_FILE"
: > "$FASTQA_APP_LOG_FILE"

nohup env PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  conda run --no-capture-output -n agent gunicorn \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --config "$PROJECT_ROOT/gunicorn.conf.py" \
  --chdir "$PROJECT_ROOT" \
  --bind "0.0.0.0:${FASTAPI_PORT}" \
  --workers "${FASTQA_GUNICORN_WORKERS}" \
  --timeout 600 \
  --pid "$PID_FILE" \
  --capture-output \
  --access-logfile "$ACCESS_LOG_FILE" \
  --error-logfile "$ERROR_LOG_FILE" \
  >"$STARTUP_LOG_FILE" 2>&1 &

LAUNCHER_PID=$!
for _ in $(seq 1 60); do
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "fastQA gunicorn started: pid=$PID port=${FASTAPI_PORT}"
      print_logs
      exit 0
    fi
  fi
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "fastQA gunicorn failed to start; inspect $STARTUP_LOG_FILE, $ERROR_LOG_FILE and $FASTQA_APP_LOG_FILE"
print_logs
exit 1
