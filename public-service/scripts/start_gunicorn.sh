#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
APP_DIR="$PROJECT_ROOT/backend"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/public-service"
fi
PID_FILE="$RUNTIME_DIR/public-service-gunicorn.pid"
STARTUP_LOG_FILE="$LOG_DIR_DEFAULT/public-service-startup.log"
ACCESS_LOG_FILE="$LOG_DIR_DEFAULT/public-service-access.log"
ERROR_LOG_FILE="$LOG_DIR_DEFAULT/public-service-error.log"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR_DEFAULT"

export PUBLIC_SERVICE_PORT="${PUBLIC_SERVICE_PORT:-8102}"
export PUBLIC_SERVICE_ENV_FILES="${PUBLIC_SERVICE_ENV_FILES:-$PROJECT_ROOT/config.shared.env:$PROJECT_ROOT/config.secret.env}"
export PUBLIC_SERVICE_GUNICORN_WORKERS="${PUBLIC_SERVICE_GUNICORN_WORKERS:-16}"

load_env_files() {
  local env_files="$1"
  IFS=':' read -r -a files <<< "$env_files"
  for file in "${files[@]}"; do
    [[ -n "${file:-}" ]] || continue
    [[ -f "$file" ]] || continue
    while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
      line="${raw_line%$'\r'}"
      [[ "$line" =~ ^[[:space:]]*$ ]] && continue
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      if [[ "$line" =~ ^[[:space:]]*export[[:space:]]+ ]]; then
        line="${line#export }"
      fi
      [[ "$line" == *=* ]] || continue
      name="${line%%=*}"
      value="${line#*=}"
      name="${name#"${name%%[![:space:]]*}"}"
      name="${name%"${name##*[![:space:]]}"}"
      [[ -n "${name:-}" ]] || continue
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
      export "${name}=${value}"
    done < "$file"
  done
}

load_env_files "$PUBLIC_SERVICE_ENV_FILES"

print_logs() {
  echo "startup_log=$STARTUP_LOG_FILE"
  echo "access_log=$ACCESS_LOG_FILE"
  echo "error_log=$ERROR_LOG_FILE"
}

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "public-service gunicorn already running: pid=$EXISTING_PID"
    print_logs
    exit 0
  fi
  rm -f "$PID_FILE"
fi

: > "$STARTUP_LOG_FILE"
: > "$ACCESS_LOG_FILE"
: > "$ERROR_LOG_FILE"

nohup conda run --no-capture-output -n agent gunicorn   -k uvicorn.workers.UvicornWorker   app.main:app   --chdir "$APP_DIR"   --bind "0.0.0.0:${PUBLIC_SERVICE_PORT}"   --workers "${PUBLIC_SERVICE_GUNICORN_WORKERS}"   --timeout 600   --pid "$PID_FILE"   --capture-output   --access-logfile "$ACCESS_LOG_FILE"   --error-logfile "$ERROR_LOG_FILE"   >"$STARTUP_LOG_FILE" 2>&1 &

LAUNCHER_PID=$!
for _ in $(seq 1 60); do
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "public-service gunicorn started: pid=$PID port=${PUBLIC_SERVICE_PORT}"
      print_logs
      exit 0
    fi
  fi
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "public-service gunicorn failed to start; inspect $STARTUP_LOG_FILE and $ERROR_LOG_FILE"
print_logs
exit 1
