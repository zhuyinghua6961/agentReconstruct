#!/bin/sh
set -eu

: "${PACKAGE_NAME:?PACKAGE_NAME is required}"
: "${PACKAGE_FILE:?PACKAGE_FILE is required}"

DATA_DIR="${DATA_DIR:-/data}"
TARGET_DIR="${TARGET_DIR:-/target}"
PACKAGE_VERSION="${PACKAGE_VERSION:-${DATA_PACKAGE_VERSION:-latest}}"
DATA_SEED_FORCE="${DATA_SEED_FORCE:-0}"
MARKER_DIR="$TARGET_DIR/.deploy/data-seed/$PACKAGE_NAME"
MARKER_FILE="$MARKER_DIR/$PACKAGE_VERSION.done"
PACKAGE_PATH="$DATA_DIR/$PACKAGE_FILE"

case "$DATA_SEED_FORCE" in
  1|true|yes|on) DATA_SEED_FORCE=1 ;;
  *) DATA_SEED_FORCE=0 ;;
esac

if [ ! -f "$PACKAGE_PATH" ]; then
  echo "data seed failed: package not found: $PACKAGE_PATH" >&2
  exit 1
fi

if [ "$DATA_SEED_FORCE" != "1" ] && [ -f "$MARKER_FILE" ]; then
  echo "data seed skipped: package=$PACKAGE_NAME version=$PACKAGE_VERSION"
  exit 0
fi

mkdir -p "$TARGET_DIR"
find "$TARGET_DIR" -mindepth 1 -maxdepth 1 ! -name ".deploy" -exec rm -rf {} +
tar --zstd -xf "$PACKAGE_PATH" -C "$TARGET_DIR"
mkdir -p "$MARKER_DIR"
printf "package=%s\nversion=%s\n" "$PACKAGE_NAME" "$PACKAGE_VERSION" > "$MARKER_FILE"
echo "data seed complete: package=$PACKAGE_NAME version=$PACKAGE_VERSION target=$TARGET_DIR"
