#!/bin/sh
set -eu

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
: "${MINIO_BUCKET:?MINIO_BUCKET is required}"

DATA_DIR="${DATA_DIR:-/data}"
WORK_DIR="${WORK_DIR:-/work/minio-originals}"
PACKAGE_FILE="${MINIO_ORIGINALS_PACKAGE_FILE:-minio-originals.tar.zst}"
PACKAGE_VERSION="${DATA_PACKAGE_VERSION:-${MINIO_ORIGINALS_DATA_VERSION:-latest}}"
DATA_SEED_FORCE="${DATA_SEED_FORCE:-0}"
MINIO_SEED_ENDPOINT="${MINIO_SEED_ENDPOINT:-http://minio:9000}"
PACKAGE_PATH="$DATA_DIR/$PACKAGE_FILE"
MARKER="_deploy/data-seed/minio-originals/$PACKAGE_VERSION.done"

case "$DATA_SEED_FORCE" in
  1|true|yes|on) DATA_SEED_FORCE=1 ;;
  *) DATA_SEED_FORCE=0 ;;
esac

if [ ! -f "$PACKAGE_PATH" ]; then
  echo "minio originals seed failed: package not found: $PACKAGE_PATH" >&2
  exit 1
fi

until mc alias set local "$MINIO_SEED_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  sleep 2
done

mc mb --ignore-existing "local/$MINIO_BUCKET"

if [ "$DATA_SEED_FORCE" != "1" ] && mc stat "local/$MINIO_BUCKET/$MARKER" >/dev/null 2>&1; then
  echo "minio originals seed skipped: version already imported: $PACKAGE_VERSION"
  exit 0
fi

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

export MINIO_BUCKET WORK_DIR
tar --zstd -xf "$PACKAGE_PATH" --to-command='
  set -eu
  object="${TAR_FILENAME#./}"
  object="${object#/}"

  case "$object" in
    papers/*)
      touch "$WORK_DIR/.has_papers"
      ;;
    patent/originals/*)
      touch "$WORK_DIR/.has_patent_originals"
      ;;
    *)
      exit 0
      ;;
  esac

  case "$object" in
    */)
      exit 0
      ;;
  esac

  case "${TAR_FILETYPE:-f}" in
    f|file|regular|"regular file")
      mc pipe --quiet "local/$MINIO_BUCKET/$object"
      ;;
    *)
      exit 0
      ;;
  esac
'

if [ ! -f "$WORK_DIR/.has_papers" ] || [ ! -f "$WORK_DIR/.has_patent_originals" ]; then
  echo "minio originals seed failed: package must contain papers/ and patent/originals/" >&2
  exit 1
fi

printf "package=minio-originals\nversion=%s\n" "$PACKAGE_VERSION" > /tmp/minio-originals.done
mc cp /tmp/minio-originals.done "local/$MINIO_BUCKET/$MARKER"
rm -rf "$WORK_DIR"
echo "minio originals seed import complete: bucket=$MINIO_BUCKET version=$PACKAGE_VERSION"
