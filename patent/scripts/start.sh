#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/../resource" 2>/dev/null && pwd || true)"
PUBLIC_SERVICE_ROOT="$(cd "$ROOT_DIR/../public-service" 2>/dev/null && pwd || true)"
source "$ROOT_DIR/../scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
CONFIG_DIR_DEFAULT="$ROOT_DIR"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/patent"
fi

cd "$ROOT_DIR"
PATENT_SHARED_ENV_FILES=""
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  PATENT_SHARED_ENV_FILES="$RESOURCE_DIR/config/shared/infrastructure.shared.env:$RESOURCE_DIR/config/shared/model-endpoints.shared.env:$RESOURCE_DIR/config/shared/infrastructure.secret.env"
fi
export PATENT_ENV_FILES="${PATENT_ENV_FILES:-$PATENT_SHARED_ENV_FILES:$CONFIG_DIR_DEFAULT/config.env:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$PUBLIC_SERVICE_ROOT/config.shared.env:$PUBLIC_SERVICE_ROOT/config.secret.env:$ROOT_DIR/.env}"

load_env_files_preserving_process_env "$PATENT_ENV_FILES"

export PATENT_PORT="${PATENT_PORT:-8010}"
export PATENT_DURABLE_MODE_ENABLED="${PATENT_DURABLE_MODE_ENABLED:-true}"
export PATENT_DURABLE_AUTHORITY_ENABLED="${PATENT_DURABLE_AUTHORITY_ENABLED:-true}"
export PATENT_AUTHORITY_BASE_URL="${PATENT_AUTHORITY_BASE_URL:-http://127.0.0.1:8102}"
export PATENT_AUTHORITY_INTERNAL_TOKEN="${PATENT_AUTHORITY_INTERNAL_TOKEN:-${PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN:-}}"

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

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec conda run -n agent gunicorn server_fastapi.asgi:app --config "$ROOT_DIR/server_fastapi/gunicorn.conf.py"
