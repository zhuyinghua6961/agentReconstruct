#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"

required_files=(
  "$COMPOSE_FILE"
  "$DEPLOY_DIR/mysql-init/001_schema.sql"
  "$DEPLOY_DIR/minio-init/init.sh"
)

required_vars=(
  COMPOSE_PROJECT_NAME
  GATEWAY_IMAGE
  PUBLIC_SERVICE_IMAGE
  FASTQA_IMAGE
  HIGHTHINKINGQA_IMAGE
  PATENT_IMAGE
  FRONTEND_IMAGE
  FRONTEND_PUBLISH_PORT
  NGINX_IMAGE_TAG
  MYSQL_ROOT_PASSWORD
  MYSQL_DATABASE
  MYSQL_APP_USER
  MYSQL_APP_PASSWORD
  REDIS_PASSWORD
  JWT_SECRET
  PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN
  MINIO_ROOT_USER
  MINIO_ROOT_PASSWORD
  MINIO_BUCKET
  LLM_BASE_URL
  LLM_MODEL
  QA_EMBEDDING_MODEL_TYPE
  QA_EMBEDDING_BASE_URL
  QA_EMBEDDING_MODEL
  HIGHTHINKINGQA_EMBEDDING_BASE_URL
  HIGHTHINKINGQA_EMBEDDING_MODEL
  RERANK_PROVIDER
  RERANK_MODEL
  PATENT_NEO4J_USERNAME
  PATENT_NEO4J_DATABASE
)

placeholder_patterns=(
  'change_me_'
  'ghcr.io/example/'
  'replace_with_real_'
)

check_seed_dir() {
  local dir="$1"
  if find "$dir" -mindepth 1 ! -name '.gitkeep' -print -quit | grep -q .; then
    echo "ok: seed-data present in $dir"
  else
    echo "warn: seed-data directory is empty: $dir"
  fi
}

check_optional_seed_dir() {
  local dir="$1"
  if find "$dir" -mindepth 1 ! -name '.gitkeep' -print -quit | grep -q .; then
    echo "ok: minio seed present in $dir"
  else
    echo "warn: minio seed directory is empty: $dir"
  fi
}

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "missing required file: $file" >&2
    exit 1
  fi
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  echo "hint: cp deploy/.env.production.example deploy/.env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "missing required variable in $ENV_FILE: $var_name" >&2
    exit 1
  fi
done

if [[ "${QA_EMBEDDING_MODEL_TYPE}" != "remote" ]]; then
  echo "invalid QA_EMBEDDING_MODEL_TYPE: expected remote, got ${QA_EMBEDDING_MODEL_TYPE}" >&2
  exit 1
fi

case "${RERANK_PROVIDER}" in
  local|dashscope|none|off|disabled) ;;
  *)
    echo "invalid RERANK_PROVIDER: expected local, dashscope, none, off, or disabled; got ${RERANK_PROVIDER}" >&2
    exit 1
    ;;
esac

if [[ "${RERANK_PROVIDER}" != "none" && "${RERANK_PROVIDER}" != "off" && "${RERANK_PROVIDER}" != "disabled" && -z "${RERANK_BASE_URL:-}" ]]; then
  echo "missing required variable in $ENV_FILE: RERANK_BASE_URL is required when RERANK_PROVIDER=${RERANK_PROVIDER}" >&2
  exit 1
fi

for pattern in "${placeholder_patterns[@]}"; do
  if grep -q "$pattern" "$ENV_FILE"; then
    echo "warn: placeholder values matching '$pattern' still exist in $ENV_FILE"
  fi
done

check_seed_dir "$DEPLOY_DIR/seed-data/public-service"
check_seed_dir "$DEPLOY_DIR/seed-data/fastQA"
check_seed_dir "$DEPLOY_DIR/seed-data/highThinkingQA"
check_seed_dir "$DEPLOY_DIR/seed-data/patentQA"
check_optional_seed_dir "$DEPLOY_DIR/minio-seed/$MINIO_BUCKET"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null

echo "preflight check passed"
