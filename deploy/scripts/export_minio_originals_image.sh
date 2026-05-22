#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"
OUTPUT_TAR="${2:-$DEPLOY_DIR/lifeo4agent-minio-originals.tar}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  echo "hint: cp deploy/.env.production.example deploy/.env" >&2
  exit 1
fi

echo "warning: export_minio_originals_image.sh is a legacy/debug path; recommended releases use deploy/data/minio-originals.tar.zst" >&2

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

MINIO_ORIGINALS_IMAGE="${MINIO_ORIGINALS_IMAGE:-lifeo4agent/minio-originals:$(date +%F)}"

if ! docker image inspect "$MINIO_ORIGINALS_IMAGE" >/dev/null 2>&1; then
  echo "docker image not found locally: $MINIO_ORIGINALS_IMAGE" >&2
  echo "hint: bash deploy/scripts/build_minio_originals_image.sh $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_TAR")"
docker save -o "$OUTPUT_TAR" "$MINIO_ORIGINALS_IMAGE"
echo "exported MinIO originals image to $OUTPUT_TAR"
