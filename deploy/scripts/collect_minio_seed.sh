#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
DEFAULT_BUCKET="${MINIO_BUCKET:-agentcode}"
TARGET_BUCKET="${1:-$DEFAULT_BUCKET}"
TARGET_DIR="$DEPLOY_DIR/minio-seed/$TARGET_BUCKET"

prefer_existing_path() {
  local candidate
  for candidate in "$@"; do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  printf '%s\n' "$1"
}

PUBLIC_SERVICE_PAPERS_DEFAULT="$ROOT_DIR/public-service/data/runtime/papers"
FASTQA_PAPERS_DEFAULT="$(prefer_existing_path \
  "$ROOT_DIR/resource/fastqa/papers" \
  "$ROOT_DIR/resource/state/dev/fastQA/papers")"
FASTQA_LOCAL_PAPERS_DEFAULT="$ROOT_DIR/resource/state/dev/fastQA/papers_local"
HIGHTHINKINGQA_PAPERS_DEFAULT="$(prefer_existing_path \
  "$ROOT_DIR/resource/highThinkingQA/papers" \
  "$ROOT_DIR/resource/state/dev/highThinkingQA/papers")"

PUBLIC_SERVICE_PAPERS_SRC="${PUBLIC_SERVICE_PAPERS_SRC:-$PUBLIC_SERVICE_PAPERS_DEFAULT}"
FASTQA_PAPERS_SRC="${FASTQA_PAPERS_SRC:-$FASTQA_PAPERS_DEFAULT}"
FASTQA_LOCAL_PAPERS_SRC="${FASTQA_LOCAL_PAPERS_SRC:-$FASTQA_LOCAL_PAPERS_DEFAULT}"
HIGHTHINKINGQA_PAPERS_SRC="${HIGHTHINKINGQA_PAPERS_SRC:-$HIGHTHINKINGQA_PAPERS_DEFAULT}"

CLEAN=0
BUCKET_SET=0

usage() {
  cat <<USAGE
Usage: bash deploy/scripts/collect_minio_seed.sh [bucket] [--clean]

Collects local papers into deploy/minio-seed/<bucket>/papers so deployment can
auto-import them into MinIO.

Environment overrides:
  PUBLIC_SERVICE_PAPERS_SRC
  FASTQA_PAPERS_SRC
  FASTQA_LOCAL_PAPERS_SRC
  HIGHTHINKINGQA_PAPERS_SRC
USAGE
}

copy_papers_from_dir() {
  local src="$1"
  local dest="$2"

  if [[ ! -d "$src" ]]; then
    echo "skip: papers source not found: $src"
    return 0
  fi

  mkdir -p "$dest"
  find "$src" -maxdepth 1 -type f ! -name '.*' | while read -r file_path; do
    local file_name
    file_name="$(basename "$file_path")"
    if [[ -f "$dest/$file_name" ]]; then
      echo "skip: minio seed already has $file_name"
      continue
    fi
    cp -a "$file_path" "$dest/$file_name"
    echo "copied: $file_path -> $dest/$file_name"
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ "$BUCKET_SET" == "1" ]]; then
        echo "unknown argument: $1" >&2
        usage >&2
        exit 1
      fi
      TARGET_BUCKET="$1"
      TARGET_DIR="$DEPLOY_DIR/minio-seed/$TARGET_BUCKET"
      BUCKET_SET=1
      shift
      ;;
  esac
done

mkdir -p "$TARGET_DIR/papers"

if [[ "$CLEAN" == "1" ]]; then
  find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  mkdir -p "$TARGET_DIR/papers"
fi

copy_papers_from_dir "$PUBLIC_SERVICE_PAPERS_SRC" "$TARGET_DIR/papers"
copy_papers_from_dir "$HIGHTHINKINGQA_PAPERS_SRC" "$TARGET_DIR/papers"
copy_papers_from_dir "$FASTQA_PAPERS_SRC" "$TARGET_DIR/papers"
copy_papers_from_dir "$FASTQA_LOCAL_PAPERS_SRC" "$TARGET_DIR/papers"

echo "minio seed collection complete: $TARGET_DIR"
