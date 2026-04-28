#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
source "$PROJECT_ROOT/../scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
CONFIG_DIR_DEFAULT="$PROJECT_ROOT"
SHARED_CONFIG_DIR_DEFAULT=""
GATEWAY_SHARED_ENV_FILES_DEFAULT=""
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/gateway"
  SHARED_CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/shared"
  GATEWAY_SHARED_ENV_FILES_DEFAULT="$SHARED_CONFIG_DIR_DEFAULT/infrastructure.shared.env:$SHARED_CONFIG_DIR_DEFAULT/model-endpoints.shared.env:$SHARED_CONFIG_DIR_DEFAULT/infrastructure.secret.env"
fi

export GATEWAY_PORT="${GATEWAY_PORT:-8101}"
export GATEWAY_GUNICORN_WORKERS="${GATEWAY_GUNICORN_WORKERS:-4}"
export GATEWAY_RUNTIME_ROLE="${GATEWAY_RUNTIME_ROLE:-web}"
export GATEWAY_SHARED_ENV_FILES="${GATEWAY_SHARED_ENV_FILES:-$GATEWAY_SHARED_ENV_FILES_DEFAULT}"
export GATEWAY_ENV_FILES="${GATEWAY_ENV_FILES:-$GATEWAY_SHARED_ENV_FILES:$CONFIG_DIR_DEFAULT/config.env:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$PROJECT_ROOT/.env}"
export PUBLIC_BACKEND_BASE_URL="${PUBLIC_BACKEND_BASE_URL:-http://127.0.0.1:8102}"
export FAST_BACKEND_BASE_URL="${FAST_BACKEND_BASE_URL:-http://127.0.0.1:8008}"
export THINKING_BACKEND_BASE_URL="${THINKING_BACKEND_BASE_URL:-http://127.0.0.1:8009}"
export PATENT_BACKEND_BASE_URL="${PATENT_BACKEND_BASE_URL:-http://127.0.0.1:8010}"
export GATEWAY_CONVERSATION_FILE_PROVIDER="${GATEWAY_CONVERSATION_FILE_PROVIDER:-public_http}"

load_env_files_preserving_process_env "$GATEWAY_ENV_FILES"

cd "$PROJECT_ROOT"
exec conda run --no-capture-output -n agent gunicorn \
  -k uvicorn.workers.UvicornWorker \
  app.main:app \
  --chdir "$PROJECT_ROOT" \
  --bind "0.0.0.0:${GATEWAY_PORT}" \
  --workers "${GATEWAY_GUNICORN_WORKERS}" \
  --timeout 600
