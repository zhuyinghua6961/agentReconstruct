#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
source "$PROJECT_ROOT/../scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
LOG_DIR_DEFAULT="$PROJECT_ROOT/.runtime/logs"
CONFIG_DIR_DEFAULT="$PROJECT_ROOT"
SHARED_CONFIG_DIR_DEFAULT=""
GATEWAY_SHARED_ENV_FILES_DEFAULT=""
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  LOG_DIR_DEFAULT="$RESOURCE_DIR/logs/dev/gateway"
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/gateway"
  SHARED_CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/shared"
  GATEWAY_SHARED_ENV_FILES_DEFAULT="$SHARED_CONFIG_DIR_DEFAULT/infrastructure.shared.env:$SHARED_CONFIG_DIR_DEFAULT/model-endpoints.shared.env:$SHARED_CONFIG_DIR_DEFAULT/infrastructure.secret.env:$SHARED_CONFIG_DIR_DEFAULT/model-endpoints.secret.env:$SHARED_CONFIG_DIR_DEFAULT/graph.shared.env:$SHARED_CONFIG_DIR_DEFAULT/graph.secret.env"
fi
PID_FILE="$RUNTIME_DIR/gateway-admission-worker.pid"
STARTUP_LOG_FILE="$LOG_DIR_DEFAULT/gateway-admission-worker-startup.log"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR_DEFAULT"

export GATEWAY_RUNTIME_ROLE="${GATEWAY_RUNTIME_ROLE:-admission_worker}"
export GATEWAY_SHARED_ENV_FILES="${GATEWAY_SHARED_ENV_FILES:-$GATEWAY_SHARED_ENV_FILES_DEFAULT}"
export GATEWAY_ENV_FILES="${GATEWAY_ENV_FILES:-$PROJECT_ROOT/config.env:$PROJECT_ROOT/config.shared.env:$PROJECT_ROOT/config.secret.env:$PROJECT_ROOT/.env:$GATEWAY_SHARED_ENV_FILES:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$CONFIG_DIR_DEFAULT/.env:$CONFIG_DIR_DEFAULT/config.env}"
export GATEWAY_ADMISSION_STARTUP_STABLE_CHECKS="${GATEWAY_ADMISSION_STARTUP_STABLE_CHECKS:-3}"

load_env_files_preserving_process_env "$GATEWAY_ENV_FILES"

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "gateway admission worker already running: pid=$EXISTING_PID"
    echo "startup_log=$STARTUP_LOG_FILE"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

: > "$STARTUP_LOG_FILE"

cd "$PROJECT_ROOT"
nohup conda run --no-capture-output -n agent python -m app.services.execution_admission >"$STARTUP_LOG_FILE" 2>&1 &

LAUNCHER_PID=$!
STABLE_COUNT=0
for _ in $(seq 1 30); do
  if kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    STABLE_COUNT=$((STABLE_COUNT + 1))
    if (( STABLE_COUNT >= GATEWAY_ADMISSION_STARTUP_STABLE_CHECKS )); then
      echo "$LAUNCHER_PID" > "$PID_FILE"
      echo "gateway admission worker started: pid=$LAUNCHER_PID"
      echo "startup_log=$STARTUP_LOG_FILE"
      exit 0
    fi
  else
    break
  fi
  sleep 1
done

echo "gateway admission worker failed to start; inspect $STARTUP_LOG_FILE"
exit 1
