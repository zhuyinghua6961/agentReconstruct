#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"

required_files=(
  "$COMPOSE_FILE"
  "$DEPLOY_DIR/mysql-init/001_schema.sql"
  "$DEPLOY_DIR/mysql-init/002_seed_departments.sql"
  "$DEPLOY_DIR/mysql-init/003_seed_admin.sql"
  "$DEPLOY_DIR/minio-init/init.sh"
  "$DEPLOY_DIR/nginx/edge-https.conf.template"
  "$DEPLOY_DIR/certs/fullchain.pem"
  "$DEPLOY_DIR/certs/privkey.pem"
)

required_vars=(
  HTTP_PUBLISH_PORT
  HTTPS_PUBLISH_PORT
  HTTPS_SERVER_NAME
  HTTPS_REDIRECT_HOST
  FRONTEND_PUBLISH_PORT
  MYSQL_ROOT_PASSWORD
  MYSQL_APP_PASSWORD
  REDIS_PASSWORD
  JWT_SECRET
  PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN
  MINIO_ROOT_USER
  MINIO_ROOT_PASSWORD
  MINIO_BUCKET
  LLM_BASE_URL
  LLM_MODEL
  INTENT_MODEL_ENABLED
  INTENT_MODEL_BASE_URL
  INTENT_MODEL
  INTENT_MODEL_TIMEOUT_SECONDS
  QA_EMBEDDING_BASE_URL
  QA_EMBEDDING_MODEL
  HIGHTHINKINGQA_EMBEDDING_BASE_URL
  HIGHTHINKINGQA_EMBEDDING_MODEL
  RERANK_PROVIDER
  RERANK_MODEL
  DATA_PACKAGE_VERSION
)

required_packages=(
  minio-originals.tar.zst
  fastqa-ref.tar.zst
  highthinking-ref.tar.zst
  patentqa-ref.tar.zst
  public-service-ref.tar.zst
  neo4j-literature.dump.zst
  neo4j-patent.dump.zst
)

placeholder_patterns=(
  'change_me_'
  'replace_with_real_'
)

tls_cert_pubkey_hash() {
  openssl x509 -in "$1" -pubkey -noout \
    | openssl pkey -pubin -outform DER \
    | openssl sha256 \
    | awk '{print $2}'
}

tls_key_pubkey_hash() {
  openssl pkey -in "$1" -pubout -outform DER \
    | openssl sha256 \
    | awk '{print $2}'
}

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "missing required file: $file" >&2
    exit 1
  fi
done

if command -v openssl >/dev/null 2>&1; then
  if ! tls_cert_hash="$(tls_cert_pubkey_hash "$DEPLOY_DIR/certs/fullchain.pem")"; then
    echo "invalid TLS certificate file: $DEPLOY_DIR/certs/fullchain.pem" >&2
    exit 1
  fi
  if ! tls_key_hash="$(tls_key_pubkey_hash "$DEPLOY_DIR/certs/privkey.pem")"; then
    echo "invalid TLS private key file: $DEPLOY_DIR/certs/privkey.pem" >&2
    exit 1
  fi
  if [[ "$tls_cert_hash" != "$tls_key_hash" ]]; then
    echo "TLS certificate and private key do not match: certs/fullchain.pem certs/privkey.pem" >&2
    echo "regenerate both files together, or replace them with a matched certificate/key pair" >&2
    exit 1
  fi
else
  echo "warn: openssl unavailable; skipping TLS certificate/private-key match check"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  echo "hint: cp deploy/.env.production.example deploy/.env" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
load_env_files_preserving_process_env "$ENV_FILE"

DATA_DIR="${DEPLOY_DATA_DIR:-$DEPLOY_DIR/data}"
if [[ "$DATA_DIR" != /* ]]; then
  DATA_DIR="$ROOT_DIR/$DATA_DIR"
fi
DATA_SEED_FORCE="${DATA_SEED_FORCE:-0}"
SEED_TOOLS_IMAGE="${SEED_TOOLS_IMAGE:-lifeo4agent/seed-tools:latest}"
NEO4J_IMAGE_TAG="${NEO4J_IMAGE_TAG:-5.26.12}"
NGINX_IMAGE_TAG="${NGINX_IMAGE_TAG:-1.27-alpine}"
PYTHON_BIN="${PYTHON_BIN:-python}"

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "missing required variable in $ENV_FILE: $var_name" >&2
    exit 1
  fi
done

case "${RERANK_PROVIDER}" in
  local|dashscope|none|off|disabled) ;;
  *)
    echo "invalid RERANK_PROVIDER: expected local, dashscope, none, off, or disabled; got ${RERANK_PROVIDER}" >&2
    exit 1
    ;;
esac

case "${INTENT_MODEL_ENABLED}" in
  0|1|true|false|yes|no|on|off) ;;
  *)
    echo "invalid INTENT_MODEL_ENABLED: expected 0, 1, true, false, yes, no, on, or off; got ${INTENT_MODEL_ENABLED}" >&2
    exit 1
    ;;
esac

if [[ "${RERANK_PROVIDER}" != "none" && "${RERANK_PROVIDER}" != "off" && "${RERANK_PROVIDER}" != "disabled" && -z "${RERANK_BASE_URL:-}" ]]; then
  echo "missing required variable in $ENV_FILE: RERANK_BASE_URL is required when RERANK_PROVIDER=${RERANK_PROVIDER}" >&2
  exit 1
fi

case "${DATA_SEED_FORCE}" in
  0|1|true|false|yes|no|on|off) ;;
  *)
    echo "invalid DATA_SEED_FORCE: expected 0, 1, true, false, yes, no, on, or off; got ${DATA_SEED_FORCE}" >&2
    exit 1
    ;;
esac

for pattern in "${placeholder_patterns[@]}"; do
  if grep -q "$pattern" "$ENV_FILE"; then
    echo "warn: placeholder values matching '$pattern' still exist in $ENV_FILE"
  fi
done

if [[ ! -f "$DATA_DIR/manifest.json" ]]; then
  echo "missing data package manifest: $DATA_DIR/manifest.json" >&2
  echo "hint: put the release data packages in deploy/data/ or set DEPLOY_DATA_DIR" >&2
  exit 1
fi

for package in "${required_packages[@]}"; do
  if [[ ! -f "$DATA_DIR/$package" ]]; then
    echo "missing required data package: $DATA_DIR/$package" >&2
    exit 1
  fi
done

if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  "$PYTHON_BIN" "$DEPLOY_DIR/scripts/validate_data_packages.py" \
    --data-dir "$DATA_DIR" \
    --require-all \
    --expected-version "$DATA_PACKAGE_VERSION" >/dev/null
  echo "ok: data package manifest and sha256 validated with python"
elif docker info >/dev/null 2>&1 && docker image inspect "$SEED_TOOLS_IMAGE" >/dev/null 2>&1; then
  docker run --rm \
    -e "EXPECTED_DATA_VERSION=$DATA_PACKAGE_VERSION" \
    -v "$DATA_DIR:/data:ro" \
    "$SEED_TOOLS_IMAGE" \
    /seed-tools/validate_manifest.sh /data >/dev/null
  echo "ok: data package manifest and sha256 validated with seed-tools"
else
  echo "missing python, or Docker/seed-tools image unavailable; cannot validate data package sha256" >&2
  exit 1
fi

if docker info >/dev/null 2>&1; then
  images=(
    "${GATEWAY_IMAGE:-lifeo4agent/gateway:latest}"
    "${PUBLIC_SERVICE_IMAGE:-lifeo4agent/public-service:latest}"
    "${FASTQA_IMAGE:-lifeo4agent/fastqa:latest}"
    "${HIGHTHINKINGQA_IMAGE:-lifeo4agent/highthinkingqa:latest}"
    "${PATENT_IMAGE:-lifeo4agent/patent:latest}"
    "${FRONTEND_IMAGE:-lifeo4agent/frontend:latest}"
    "$SEED_TOOLS_IMAGE"
    "mysql:${MYSQL_IMAGE_TAG:-8.0}"
    "redis:${REDIS_IMAGE_TAG:-7}"
    "minio/minio:${MINIO_IMAGE_TAG:-latest}"
    "minio/mc:${MINIO_MC_IMAGE_TAG:-latest}"
    "neo4j:$NEO4J_IMAGE_TAG"
    "nginx:$NGINX_IMAGE_TAG"
  )
  for image in "${images[@]}"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
      echo "ok: docker image present: $image"
    else
      echo "warn: docker image not found locally: $image"
    fi
  done
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null

echo "preflight check passed"
