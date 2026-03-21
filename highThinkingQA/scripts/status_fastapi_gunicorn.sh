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

if [[ ! -f "$PID_FILE" ]]; then
  echo "gunicorn status: not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
  APP_PORT="$(conda run --no-capture-output -n agent python - <<PY
import sys
sys.path.insert(0, r"$ROOT_DIR")
import config
print(config.APP_PORT)
PY
)"
  echo "gunicorn status: running pid=$PID"
  ss -ltnp "( sport = :$APP_PORT )" 2>/dev/null || true
  exit 0
fi

echo "gunicorn status: stale pid=$PID"
exit 1
