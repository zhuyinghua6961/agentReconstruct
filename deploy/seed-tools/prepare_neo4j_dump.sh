#!/bin/sh
set -eu

: "${PACKAGE_NAME:?PACKAGE_NAME is required}"
: "${PACKAGE_FILE:?PACKAGE_FILE is required}"

DATA_DIR="${DATA_DIR:-/data}"
WORK_DIR="${WORK_DIR:-/work/neo4j-dumps}"
PACKAGE_VERSION="${PACKAGE_VERSION:-${DATA_PACKAGE_VERSION:-latest}}"
DATA_SEED_FORCE="${DATA_SEED_FORCE:-0}"
PACKAGE_PATH="$DATA_DIR/$PACKAGE_FILE"
DUMP_NAME="${DUMP_NAME:-neo4j.dump}"
READY_FILE="$WORK_DIR/$PACKAGE_NAME.$PACKAGE_VERSION.ready"
TARGET_DUMP="$WORK_DIR/$DUMP_NAME"

case "$DATA_SEED_FORCE" in
  1|true|yes|on) DATA_SEED_FORCE=1 ;;
  *) DATA_SEED_FORCE=0 ;;
esac

if [ ! -f "$PACKAGE_PATH" ]; then
  echo "neo4j dump prepare failed: package not found: $PACKAGE_PATH" >&2
  exit 1
fi

mkdir -p "$WORK_DIR"
if [ "$DATA_SEED_FORCE" != "1" ] && [ -f "$READY_FILE" ] && [ -f "$TARGET_DUMP" ]; then
  echo "neo4j dump prepare skipped: package=$PACKAGE_NAME version=$PACKAGE_VERSION"
  exit 0
fi

rm -f "$TARGET_DUMP" "$READY_FILE"
zstd -dc "$PACKAGE_PATH" > "$TARGET_DUMP"
printf "package=%s\nversion=%s\n" "$PACKAGE_NAME" "$PACKAGE_VERSION" > "$READY_FILE"
echo "neo4j dump prepared: package=$PACKAGE_NAME dump=$TARGET_DUMP"
