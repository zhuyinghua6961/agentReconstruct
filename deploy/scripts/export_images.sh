#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"
OUTPUT_TAR="${2:-$DEPLOY_DIR/highthinking-images.tar}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

required_vars=(
  GATEWAY_IMAGE
  PUBLIC_SERVICE_IMAGE
  FASTQA_IMAGE
  HIGHTHINKINGQA_IMAGE
  PATENT_IMAGE
  FRONTEND_IMAGE
  MYSQL_IMAGE_TAG
  REDIS_IMAGE_TAG
  MINIO_IMAGE_TAG
  MINIO_MC_IMAGE_TAG
  ALPINE_IMAGE_TAG
  NGINX_IMAGE_TAG
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "missing required variable in $ENV_FILE: $var_name" >&2
    exit 1
  fi
done

images=(
  "$GATEWAY_IMAGE"
  "$PUBLIC_SERVICE_IMAGE"
  "$FASTQA_IMAGE"
  "$HIGHTHINKINGQA_IMAGE"
  "$PATENT_IMAGE"
  "$FRONTEND_IMAGE"
  "mysql:${MYSQL_IMAGE_TAG}"
  "redis:${REDIS_IMAGE_TAG}"
  "minio/minio:${MINIO_IMAGE_TAG}"
  "minio/mc:${MINIO_MC_IMAGE_TAG}"
  "alpine:${ALPINE_IMAGE_TAG}"
  "nginx:${NGINX_IMAGE_TAG}"
)

for image in "${images[@]}"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "docker image not found locally: $image" >&2
    exit 1
  fi
done

mkdir -p "$(dirname "$OUTPUT_TAR")"
docker save -o "$OUTPUT_TAR" "${images[@]}"
echo "exported images to $OUTPUT_TAR"
