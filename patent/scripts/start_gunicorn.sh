#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
PUBLIC_SERVICE_ROOT="$(cd "$PROJECT_ROOT/../public-service" 2>/dev/null && pwd || true)"
source "$PROJECT_ROOT/../scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
RUNTIME_DIR_DEFAULT="$PROJECT_ROOT/.runtime"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
CONFIG_DIR_DEFAULT="$PROJECT_ROOT"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  RUNTIME_DIR_DEFAULT="$RESOURCE_DIR/runtime/dev/patent"
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/patent"
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/patent"
fi

export PATENT_SERVICE_RUNTIME_ROOT="${PATENT_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"
export PATENT_SERVICE_LOG_ROOT="${PATENT_SERVICE_LOG_ROOT:-$LOG_DIR_DEFAULT}"
PATENT_SHARED_ENV_FILES=""
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  PATENT_SHARED_ENV_FILES="$RESOURCE_DIR/config/shared/infrastructure.shared.env:$RESOURCE_DIR/config/shared/model-endpoints.shared.env:$RESOURCE_DIR/config/shared/infrastructure.secret.env:$RESOURCE_DIR/config/shared/model-endpoints.secret.env:$RESOURCE_DIR/config/shared/graph.shared.env:$RESOURCE_DIR/config/shared/graph.secret.env"
fi
export PATENT_ENV_FILES="${PATENT_ENV_FILES:-$PROJECT_ROOT/config.shared.env:$PROJECT_ROOT/config.secret.env:$PROJECT_ROOT/.env:$PUBLIC_SERVICE_ROOT/config.shared.env:$PUBLIC_SERVICE_ROOT/config.secret.env:$PATENT_SHARED_ENV_FILES:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$CONFIG_DIR_DEFAULT/.env:$CONFIG_DIR_DEFAULT/config.env}"

load_env_files_preserving_process_env "$PATENT_ENV_FILES"

export PATENT_PORT="${PATENT_PORT:-8010}"
export PATENT_DURABLE_MODE_ENABLED="${PATENT_DURABLE_MODE_ENABLED:-true}"
export PATENT_DURABLE_AUTHORITY_ENABLED="${PATENT_DURABLE_AUTHORITY_ENABLED:-true}"
export PATENT_AUTHORITY_BASE_URL="${PATENT_AUTHORITY_BASE_URL:-http://127.0.0.1:8102}"
export PATENT_AUTHORITY_INTERNAL_TOKEN="${PATENT_AUTHORITY_INTERNAL_TOKEN:-${PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN:-}}"
export PATENT_REDIS_ENABLED="${PATENT_REDIS_ENABLED:-true}"

if [[ -z "${PATENT_REDIS_URL:-}" ]]; then
  REDIS_SCHEME="${REDIS_SCHEME:-redis}"
  REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
  REDIS_PORT="${REDIS_PORT:-6379}"
  REDIS_DB="${REDIS_DB:-0}"
  REDIS_USERNAME="${REDIS_USERNAME:-}"
  REDIS_PASSWORD="${REDIS_PASSWORD:-123456}"
  REDIS_AUTH=""
  if [[ -n "${REDIS_USERNAME:-}" || -n "${REDIS_PASSWORD:-}" ]]; then
    REDIS_AUTH="${REDIS_USERNAME:-}:${REDIS_PASSWORD:-}@"
  fi
  export PATENT_REDIS_URL="${REDIS_SCHEME}://${REDIS_AUTH}${REDIS_HOST}:${REDIS_PORT}/${REDIS_DB}"
fi

PID_FILE="$PATENT_SERVICE_RUNTIME_ROOT/patent-gunicorn.pid"
STARTUP_LOG_FILE="$PATENT_SERVICE_LOG_ROOT/patent-startup.log"
ACCESS_LOG_FILE="$PATENT_SERVICE_LOG_ROOT/patent-access.log"
ERROR_LOG_FILE="$PATENT_SERVICE_LOG_ROOT/patent-error.log"
APP_LOG_FILE="${PATENT_APP_LOG_FILE:-$PATENT_SERVICE_LOG_ROOT/patent-app.log}"
mkdir -p "$PATENT_SERVICE_RUNTIME_ROOT" "$PATENT_SERVICE_LOG_ROOT"
export PATENT_APP_LOG_FILE="$APP_LOG_FILE"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
  echo "app_log=$APP_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "patent gunicorn already running: pid=$EXISTING_PID"
    print_logs
    exit 0
  fi
  rm -f "$PID_FILE"
fi

: > "$STARTUP_LOG_FILE"
: > "$ACCESS_LOG_FILE"
: > "$ERROR_LOG_FILE"
: > "$APP_LOG_FILE"

nohup env PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  conda run --no-capture-output -n agent \
  gunicorn server_fastapi.asgi:app \
  --config "$PROJECT_ROOT/server_fastapi/gunicorn.conf.py" \
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
      echo "patent gunicorn started: pid=$PID port=${PATENT_PORT}"
      print_logs
      exit 0
    fi
  fi
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "patent gunicorn failed to start; inspect $STARTUP_LOG_FILE and $ERROR_LOG_FILE"
print_logs
exit 1
