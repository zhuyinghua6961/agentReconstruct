#!/bin/sh
set -eu

: "${PACKAGE_NAME:?PACKAGE_NAME is required}"

NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"
PACKAGE_VERSION="${PACKAGE_VERSION:-${DATA_PACKAGE_VERSION:-latest}}"
DATA_SEED_FORCE="${DATA_SEED_FORCE:-0}"
WORK_DIR="${WORK_DIR:-/work/neo4j-dumps}"
DUMP_NAME="${DUMP_NAME:-neo4j.dump}"
DUMP_PATH="$WORK_DIR/$DUMP_NAME"
MARKER_DIR="/data/.deploy/data-seed/$PACKAGE_NAME"
MARKER_FILE="$MARKER_DIR/$PACKAGE_VERSION.done"

case "$DATA_SEED_FORCE" in
  1|true|yes|on) DATA_SEED_FORCE=1 ;;
  *) DATA_SEED_FORCE=0 ;;
esac

if [ "$DATA_SEED_FORCE" != "1" ] && [ -f "$MARKER_FILE" ]; then
  echo "neo4j seed skipped: package=$PACKAGE_NAME version=$PACKAGE_VERSION"
  exit 0
fi

if [ ! -f "$DUMP_PATH" ]; then
  echo "neo4j seed failed: dump not found: $DUMP_PATH" >&2
  exit 1
fi

neo4j-admin database load "$NEO4J_DATABASE" --from-path="$WORK_DIR" --overwrite-destination=true
mkdir -p "$MARKER_DIR"
printf "package=%s\nversion=%s\ndatabase=%s\n" "$PACKAGE_NAME" "$PACKAGE_VERSION" "$NEO4J_DATABASE" > "$MARKER_FILE"
echo "neo4j seed complete: package=$PACKAGE_NAME database=$NEO4J_DATABASE version=$PACKAGE_VERSION"
