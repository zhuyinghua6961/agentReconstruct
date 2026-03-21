#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/../resource" 2>/dev/null && pwd || true)"
SERVICE_CONFIG_ROOT_DEFAULT="$ROOT_DIR"
SERVICE_STATE_ROOT_DEFAULT="$ROOT_DIR"
SERVICE_RUNTIME_ROOT_DEFAULT="$ROOT_DIR/.runtime"
SERVICE_ASSET_ROOT_DEFAULT="$ROOT_DIR"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  SERVICE_CONFIG_ROOT_DEFAULT="$RESOURCE_DIR/config/services/highThinkingQA"
  SERVICE_STATE_ROOT_DEFAULT="$RESOURCE_DIR/state/dev/highThinkingQA"
  SERVICE_RUNTIME_ROOT_DEFAULT="$RESOURCE_DIR/runtime/dev/highThinkingQA"
  if [[ -d "$RESOURCE_DIR/assets/prompts" ]]; then
    SERVICE_ASSET_ROOT_DEFAULT="$RESOURCE_DIR/assets"
  fi
fi

export HIGHTHINKINGQA_SERVICE_CONFIG_ROOT="${HIGHTHINKINGQA_SERVICE_CONFIG_ROOT:-$SERVICE_CONFIG_ROOT_DEFAULT}"
export HIGHTHINKINGQA_SERVICE_STATE_ROOT="${HIGHTHINKINGQA_SERVICE_STATE_ROOT:-$SERVICE_STATE_ROOT_DEFAULT}"
export HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="${HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT:-$SERVICE_RUNTIME_ROOT_DEFAULT}"
export HIGHTHINKINGQA_SERVICE_ASSET_ROOT="${HIGHTHINKINGQA_SERVICE_ASSET_ROOT:-$SERVICE_ASSET_ROOT_DEFAULT}"

PID_FILE="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/gunicorn.pid"
LOG_DIR="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/logs"

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
