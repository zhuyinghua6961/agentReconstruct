#!/bin/sh
set -eu

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
: "${MINIO_BUCKET:?MINIO_BUCKET is required}"

DATA_DIR="${DATA_DIR:-/data}"
WORK_DIR="${WORK_DIR:-/work/minio-originals}"
PACKAGE_FILE="${MINIO_ORIGINALS_PACKAGE_FILE:-minio-originals.tar.zst}"
PATCH_PACKAGE_FILES="${MINIO_PATCH_PACKAGE_FILES:-}"
PACKAGE_VERSION="${DATA_PACKAGE_VERSION:-${MINIO_ORIGINALS_DATA_VERSION:-latest}}"
PATCH_PACKAGE_VERSION="${MINIO_PATCH_DATA_VERSION:-$PACKAGE_VERSION}"
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

import_minio_package() {
  package_file="$1"
  marker="$2"
  label="$3"
  require_mode="$4"
  marker_version="${5:-$PACKAGE_VERSION}"
  package_path="$DATA_DIR/$package_file"

  if [ ! -f "$package_path" ]; then
    echo "minio originals seed failed: package not found: $package_path" >&2
    exit 1
  fi

  if [ "$DATA_SEED_FORCE" != "1" ] && mc stat "local/$MINIO_BUCKET/$marker" >/dev/null 2>&1; then
    echo "minio originals seed skipped: package=$label version=$marker_version file=$package_file"
    return 0
  fi

  rm -rf "$WORK_DIR"
  mkdir -p "$WORK_DIR"

  export MINIO_BUCKET WORK_DIR
  tar --zstd -xf "$package_path" --to-command='
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

  if [ "$require_mode" = "base" ]; then
    if [ ! -f "$WORK_DIR/.has_papers" ] || [ ! -f "$WORK_DIR/.has_patent_originals" ]; then
      echo "minio originals seed failed: package must contain papers/ and patent/originals/: $package_file" >&2
      exit 1
    fi
  elif [ ! -f "$WORK_DIR/.has_papers" ] && [ ! -f "$WORK_DIR/.has_patent_originals" ]; then
    echo "minio originals seed failed: patch package contains no supported objects: $package_file" >&2
    exit 1
  fi

  printf "package=%s\nversion=%s\nfile=%s\n" "$label" "$marker_version" "$package_file" > "$WORK_DIR/.done"
  mc cp "$WORK_DIR/.done" "local/$MINIO_BUCKET/$marker"
  rm -rf "$WORK_DIR"
  echo "minio originals seed import complete: package=$label bucket=$MINIO_BUCKET version=$marker_version file=$package_file"
}

import_minio_package "$PACKAGE_FILE" "$MARKER" "minio-originals" "base" "$PACKAGE_VERSION"

if [ -n "$PATCH_PACKAGE_FILES" ]; then
  patch_list="$(printf "%s" "$PATCH_PACKAGE_FILES" | tr "," " ")"
  for patch_file in $patch_list; do
    safe_file="$(printf "%s" "$patch_file" | tr "/: " "___")"
    patch_marker="_deploy/data-seed/minio-originals-patches/$PATCH_PACKAGE_VERSION/$safe_file.done"
    import_minio_package "$patch_file" "$patch_marker" "minio-originals-patch" "patch" "$PATCH_PACKAGE_VERSION"
  done
fi
