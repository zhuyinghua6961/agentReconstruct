#!/bin/sh
set -eu

DATA_DIR="${1:-/data}"
MANIFEST="$DATA_DIR/manifest.json"
REQUIRED_PACKAGES="minio-originals fastqa-ref highthinking-ref patentqa-ref public-service-ref neo4j-literature neo4j-patent"
EXPECTED_DATA_VERSION="${EXPECTED_DATA_VERSION:-}"

if [ ! -f "$MANIFEST" ]; then
  echo "manifest not found: $MANIFEST" >&2
  exit 1
fi

jq -e '.packages | type == "object"' "$MANIFEST" >/dev/null

if [ -n "$EXPECTED_DATA_VERSION" ]; then
  actual_version="$(jq -r '.data_version // ""' "$MANIFEST")"
  if [ "$actual_version" != "$EXPECTED_DATA_VERSION" ]; then
    echo "manifest data_version mismatch: expected=$EXPECTED_DATA_VERSION actual=$actual_version" >&2
    exit 1
  fi
fi

for package_name in $REQUIRED_PACKAGES; do
  if ! jq -e --arg name "$package_name" '.packages[$name] | type == "object"' "$MANIFEST" >/dev/null; then
    echo "manifest missing required package: $package_name" >&2
    exit 1
  fi

  package_file="$(jq -r --arg name "$package_name" '.packages[$name].file // ""' "$MANIFEST")"
  expected_sha="$(jq -r --arg name "$package_name" '.packages[$name].sha256 // ""' "$MANIFEST")"
  if [ -z "$package_file" ] || [ -z "$expected_sha" ]; then
    echo "manifest package missing file or sha256: $package_name" >&2
    exit 1
  fi
  if [ -n "$EXPECTED_DATA_VERSION" ]; then
    package_version="$(jq -r --arg name "$package_name" '.packages[$name].version // ""' "$MANIFEST")"
    if [ "$package_version" != "$EXPECTED_DATA_VERSION" ]; then
      echo "manifest package version mismatch for $package_name: expected=$EXPECTED_DATA_VERSION actual=$package_version" >&2
      exit 1
    fi
  fi

  package_path="$DATA_DIR/$package_file"
  if [ ! -f "$package_path" ]; then
    echo "package file not found: $package_name: $package_path" >&2
    exit 1
  fi

  actual_sha="$(sha256sum "$package_path" | awk '{print $1}')"
  if [ "$actual_sha" != "$expected_sha" ]; then
    echo "sha256 mismatch for $package_name: expected=$expected_sha actual=$actual_sha" >&2
    exit 1
  fi
done

echo "data package manifest validated: $DATA_DIR"
