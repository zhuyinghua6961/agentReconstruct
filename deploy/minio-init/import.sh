#!/bin/sh
set -eu

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
: "${MINIO_BUCKET:?MINIO_BUCKET is required}"

SEED_BUCKET_DIR="/seed/${MINIO_BUCKET}"

until mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  sleep 2
done

mc mb --ignore-existing "local/$MINIO_BUCKET"

if [ ! -d "$SEED_BUCKET_DIR" ]; then
  echo "minio seed skipped: bucket seed dir not found: $SEED_BUCKET_DIR"
  exit 0
fi

if ! find "$SEED_BUCKET_DIR" -mindepth 1 -print -quit | grep -q .; then
  echo "minio seed skipped: bucket seed dir is empty: $SEED_BUCKET_DIR"
  exit 0
fi

mc mirror --overwrite "$SEED_BUCKET_DIR" "local/$MINIO_BUCKET"
echo "minio seed import complete for bucket: $MINIO_BUCKET"
