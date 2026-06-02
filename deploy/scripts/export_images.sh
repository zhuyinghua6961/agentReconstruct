#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"
OUTPUT_TAR="${2:-$DEPLOY_DIR/lifeo4agent-images.tar}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
load_env_files_preserving_process_env "$ENV_FILE"

GATEWAY_IMAGE="${GATEWAY_IMAGE:-lifeo4agent/gateway:latest}"
PUBLIC_SERVICE_IMAGE="${PUBLIC_SERVICE_IMAGE:-lifeo4agent/public-service:latest}"
FASTQA_IMAGE="${FASTQA_IMAGE:-lifeo4agent/fastqa:latest}"
HIGHTHINKINGQA_IMAGE="${HIGHTHINKINGQA_IMAGE:-lifeo4agent/highthinkingqa:latest}"
PATENT_IMAGE="${PATENT_IMAGE:-lifeo4agent/patent:latest}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-lifeo4agent/frontend:latest}"
MYSQL_IMAGE_TAG="${MYSQL_IMAGE_TAG:-8.0}"
REDIS_IMAGE_TAG="${REDIS_IMAGE_TAG:-7}"
MINIO_IMAGE_TAG="${MINIO_IMAGE_TAG:-latest}"
MINIO_MC_IMAGE_TAG="${MINIO_MC_IMAGE_TAG:-latest}"
NEO4J_IMAGE_TAG="${NEO4J_IMAGE_TAG:-5.26.12}"
SEED_TOOLS_IMAGE="${SEED_TOOLS_IMAGE:-lifeo4agent/seed-tools:latest}"
NGINX_IMAGE_TAG="${NGINX_IMAGE_TAG:-1.27-alpine}"

images=(
  "$GATEWAY_IMAGE"
  "$PUBLIC_SERVICE_IMAGE"
  "$FASTQA_IMAGE"
  "$HIGHTHINKINGQA_IMAGE"
  "$PATENT_IMAGE"
  "$FRONTEND_IMAGE"
  "$SEED_TOOLS_IMAGE"
  "mysql:${MYSQL_IMAGE_TAG}"
  "redis:${REDIS_IMAGE_TAG}"
  "minio/minio:${MINIO_IMAGE_TAG}"
  "minio/mc:${MINIO_MC_IMAGE_TAG}"
  "neo4j:${NEO4J_IMAGE_TAG}"
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
