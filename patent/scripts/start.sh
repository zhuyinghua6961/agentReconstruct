#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/../resource" 2>/dev/null && pwd || true)"
PUBLIC_SERVICE_ROOT="$(cd "$ROOT_DIR/../public-service" 2>/dev/null && pwd || true)"
CONFIG_DIR_DEFAULT="$ROOT_DIR"

if [[ -n "${RESOURCE_DIR:-}" ]]; then
  CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/services/patent"
fi

cd "$ROOT_DIR"
export PATENT_ENV_FILES="${PATENT_ENV_FILES:-$CONFIG_DIR_DEFAULT/config.env:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$ROOT_DIR/config.shared.env:$ROOT_DIR/config.secret.env:$PUBLIC_SERVICE_ROOT/config.shared.env:$PUBLIC_SERVICE_ROOT/config.secret.env:$ROOT_DIR/.env}"

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

load_env_files "$PATENT_ENV_FILES"

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

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec conda run -n agent gunicorn server_fastapi.asgi:app --config "$ROOT_DIR/server_fastapi/gunicorn.conf.py"
