#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
SEED_DIR="$DEPLOY_DIR/seed-data"

prefer_existing_path() {
  local candidate
  for candidate in "$@"; do
    if [[ -e "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  printf '%s\n' "$1"
}

PUBLIC_SERVICE_SRC_DEFAULT="$ROOT_DIR/public-service/data/runtime"
FASTQA_SRC_DEFAULT="$(prefer_existing_path \
  "$ROOT_DIR/resource/fastqa" \
  "$ROOT_DIR/resource/state/dev/fastQA")"
HIGHTHINKINGQA_SRC_DEFAULT="$(prefer_existing_path \
  "$ROOT_DIR/resource/highThinkingQA" \
  "$ROOT_DIR/resource/state/dev/highThinkingQA")"
PATENTQA_SRC_DEFAULT="$ROOT_DIR/resource/patentQA"

PUBLIC_SERVICE_SRC="${PUBLIC_SERVICE_SRC:-$PUBLIC_SERVICE_SRC_DEFAULT}"
FASTQA_SRC="${FASTQA_SRC:-$FASTQA_SRC_DEFAULT}"
HIGHTHINKINGQA_SRC="${HIGHTHINKINGQA_SRC:-$HIGHTHINKINGQA_SRC_DEFAULT}"
PATENTQA_SRC="${PATENTQA_SRC:-$PATENTQA_SRC_DEFAULT}"

CLEAN=0

usage() {
  cat <<USAGE
Usage: bash deploy/scripts/collect_seed_data.sh [--clean]

Copies retrieval-related runtime data from the current worktree into deploy/seed-data.

Environment overrides:
  PUBLIC_SERVICE_SRC
  FASTQA_SRC
  HIGHTHINKINGQA_SRC
  PATENTQA_SRC
USAGE
}

copy_dir() {
  local src="$1"
  local dest="$2"

  if [[ ! -d "$src" ]]; then
    echo "skip: source not found: $src"
    return 0
  fi

  mkdir -p "$dest"
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  cp -a "$src" "$dest"
  echo "copied: $src -> $dest"
}

copy_file() {
  local src="$1"
  local dest="$2"

  if [[ ! -f "$src" ]]; then
    echo "skip: file source not found: $src"
    return 0
  fi

  mkdir -p "$(dirname "$dest")"
  cp -a "$src" "$dest"
  echo "copied: $src -> $dest"
}

clean_target_dir() {
  local dir="$1"
  mkdir -p "$dir"
  find "$dir" -mindepth 1 -maxdepth 1 ! -name '.gitkeep' -exec rm -rf {} +
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
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

mkdir -p "$SEED_DIR/public-service" "$SEED_DIR/fastQA" "$SEED_DIR/highThinkingQA" "$SEED_DIR/patentQA"

if [[ "$CLEAN" == "1" ]]; then
  clean_target_dir "$SEED_DIR/public-service"
  clean_target_dir "$SEED_DIR/fastQA"
  clean_target_dir "$SEED_DIR/highThinkingQA"
  clean_target_dir "$SEED_DIR/patentQA"
fi

copy_dir "$PUBLIC_SERVICE_SRC/vector_database" "$SEED_DIR/public-service/vector_database"
copy_dir "$PUBLIC_SERVICE_SRC/papers" "$SEED_DIR/public-service/papers"
copy_dir "$PUBLIC_SERVICE_SRC/storage" "$SEED_DIR/public-service/storage"
copy_dir "$PUBLIC_SERVICE_SRC/translation_cache" "$SEED_DIR/public-service/translation_cache"

copy_dir "$FASTQA_SRC/vector_database" "$SEED_DIR/fastQA/vector_database"
copy_dir "$FASTQA_SRC/vector_database_local" "$SEED_DIR/fastQA/vector_database_local"
copy_dir "$FASTQA_SRC/vector_database_md" "$SEED_DIR/fastQA/vector_database_md"
copy_dir "$FASTQA_SRC/community_vector_database" "$SEED_DIR/fastQA/community_vector_database"
copy_file "$FASTQA_SRC/vector_db_topic_index.json" "$SEED_DIR/fastQA/vector_db_topic_index.json"

copy_dir "$HIGHTHINKINGQA_SRC/vectordb" "$SEED_DIR/highThinkingQA/vectordb"
copy_dir "$HIGHTHINKINGQA_SRC/papers" "$SEED_DIR/highThinkingQA/papers"

copy_dir "$PATENTQA_SRC/vector_db_patent_abstracts" "$SEED_DIR/patentQA/vector_db_patent_abstracts"
copy_dir "$PATENTQA_SRC/vector_db_patent_chunks" "$SEED_DIR/patentQA/vector_db_patent_chunks"

echo "seed-data collection complete"
