#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
DEFAULT_BUCKET="${MINIO_BUCKET:-agentcode}"
TARGET_BUCKET="$DEFAULT_BUCKET"
MINIO_SEED_ROOT="${DEPLOY_MINIO_SEED_DIR:-$DEPLOY_DIR/minio-seed}"
TARGET_DIR="$MINIO_SEED_ROOT/$TARGET_BUCKET"

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
PATENT_ORIGINALS_DEFAULT="$ROOT_DIR/resource/patentQA"

PUBLIC_SERVICE_PAPERS_SRC="${PUBLIC_SERVICE_PAPERS_SRC:-$PUBLIC_SERVICE_PAPERS_DEFAULT}"
FASTQA_PAPERS_SRC="${FASTQA_PAPERS_SRC:-$FASTQA_PAPERS_DEFAULT}"
FASTQA_LOCAL_PAPERS_SRC="${FASTQA_LOCAL_PAPERS_SRC:-$FASTQA_LOCAL_PAPERS_DEFAULT}"
HIGHTHINKINGQA_PAPERS_SRC="${HIGHTHINKINGQA_PAPERS_SRC:-$HIGHTHINKINGQA_PAPERS_DEFAULT}"
PATENT_ORIGINALS_SRC="${PATENT_ORIGINALS_SRC:-$PATENT_ORIGINALS_DEFAULT}"
PATENT_ORIGINALS_PROVIDER="${PATENT_ORIGINALS_PROVIDER:-patent_source_x}"
PYTHON_BIN="${PYTHON_BIN:-python}"

CLEAN=0
BUCKET_SET=0
PAPERS_ENABLED=1
PATENT_ORIGINALS_ENABLED=1

usage() {
  cat <<USAGE
Usage: bash deploy/scripts/collect_minio_seed.sh [bucket] [--clean] [--papers-only|--patent-only]

Collects local papers and patent originals into deploy/minio-seed/<bucket> so
deployment can auto-import them into MinIO.

Environment overrides:
  DEPLOY_MINIO_SEED_DIR
  PUBLIC_SERVICE_PAPERS_SRC
  FASTQA_PAPERS_SRC
  FASTQA_LOCAL_PAPERS_SRC
  HIGHTHINKINGQA_PAPERS_SRC
  PATENT_ORIGINALS_SRC
  PATENT_ORIGINALS_PROVIDER
  PATENT_TABLES_BACKFILL_ENABLED
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

backfill_patent_tables_into_seed() {
  if [[ "${PATENT_TABLES_BACKFILL_ENABLED:-1}" != "1" ]]; then
    echo "skip: patent tables backfill disabled"
    return 0
  fi

  "$PYTHON_BIN" "$ROOT_DIR/scripts/patent_original_tables_backfill.py" \
    --target file \
    --bucket-dir "$TARGET_DIR" \
    --source-root "$PATENT_ORIGINALS_SRC" \
    --summary-only
}

check_patent_tables_seed_consistency() {
  if [[ "${PATENT_TABLES_BACKFILL_ENABLED:-1}" != "1" ]]; then
    return 0
  fi

  "$PYTHON_BIN" - "$PATENT_ORIGINALS_SRC" "$TARGET_DIR" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
bucket_dir = Path(sys.argv[2]).resolve()
patent_seed_root = bucket_dir / "patent" / "originals"

source_table_files = sorted(path for path in source_root.rglob("*_tables.json") if path.is_file())
source_patent_ids = {path.parent.name.strip().upper() for path in source_table_files}
seed_table_files = sorted(patent_seed_root.glob("*/structured/tables.json")) if patent_seed_root.is_dir() else []

errors: list[str] = []
if len(seed_table_files) != len(source_table_files):
    errors.append(f"tables count mismatch: source={len(source_table_files)} seed={len(seed_table_files)}")

for patent_id in sorted(source_patent_ids):
    prefix = f"patent/originals/{patent_id}"
    tables_ref = f"{prefix}/structured/tables.json"
    tables_path = bucket_dir / tables_ref
    manifest_path = bucket_dir / prefix / "manifest.json"
    if not tables_path.is_file():
        errors.append(f"missing seed tables object: {tables_ref}")
        continue
    if not manifest_path.is_file():
        errors.append(f"missing manifest for tables object: {prefix}/manifest.json")
        continue
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid manifest json for {patent_id}: {exc}")
        continue
    structured = dict((manifest.get("objects") or {}).get("structured") or {})
    availability = dict(manifest.get("availability") or {})
    if structured.get("tables") != tables_ref:
        errors.append(f"manifest does not register tables for {patent_id}")
    if "tables" not in availability:
        errors.append(f"manifest availability missing tables for {patent_id}")

if errors:
    for item in errors[:50]:
        print(f"error: {item}", file=sys.stderr)
    if len(errors) > 50:
        print(f"error: ... {len(errors) - 50} more consistency errors", file=sys.stderr)
    raise SystemExit(1)

print(f"patent tables seed consistency ok: tables={len(seed_table_files)} manifests={len(source_patent_ids)}")
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN=1
      shift
      ;;
    --papers-only)
      PAPERS_ENABLED=1
      PATENT_ORIGINALS_ENABLED=0
      shift
      ;;
    --patent-only|--patents-only)
      PAPERS_ENABLED=0
      PATENT_ORIGINALS_ENABLED=1
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
      TARGET_DIR="$MINIO_SEED_ROOT/$TARGET_BUCKET"
      BUCKET_SET=1
      shift
      ;;
  esac
done

mkdir -p "$TARGET_DIR"

if [[ "$CLEAN" == "1" ]]; then
  find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

if [[ "$PAPERS_ENABLED" == "1" ]]; then
  mkdir -p "$TARGET_DIR/papers"
  copy_papers_from_dir "$FASTQA_PAPERS_SRC" "$TARGET_DIR/papers"
  copy_papers_from_dir "$HIGHTHINKINGQA_PAPERS_SRC" "$TARGET_DIR/papers"
  copy_papers_from_dir "$FASTQA_LOCAL_PAPERS_SRC" "$TARGET_DIR/papers"
  copy_papers_from_dir "$PUBLIC_SERVICE_PAPERS_SRC" "$TARGET_DIR/papers"
fi

if [[ "$PATENT_ORIGINALS_ENABLED" == "1" ]]; then
  if [[ ! -d "$PATENT_ORIGINALS_SRC" ]]; then
    echo "skip: patent originals source not found: $PATENT_ORIGINALS_SRC"
  else
    PYTHONPATH="$ROOT_DIR/patent${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" - "$PATENT_ORIGINALS_SRC" "$TARGET_DIR" "$PATENT_ORIGINALS_PROVIDER" <<'PY'
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from server.patent.original_assets_tooling import (
    build_patent_original_backfill_plan,
    discover_patent_source_dirs,
)


class FileSeedTarget:
    def __init__(self, bucket_dir: Path) -> None:
        self.bucket_dir = bucket_dir

    def object_exists(self, *, object_name: str) -> bool:
        return (self.bucket_dir / object_name).exists()

    def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
        target = self.bucket_dir / object_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def upload_file(self, *, object_name: str, source_path: str, content_type: str) -> None:
        target = self.bucket_dir / object_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)

    def read_object_bytes(self, *, object_name: str) -> bytes | None:
        target = self.bucket_dir / object_name
        if not target.exists():
            return None
        return target.read_bytes()


source_root = Path(sys.argv[1]).resolve()
bucket_dir = Path(sys.argv[2]).resolve()
provider = sys.argv[3]
source_dirs = discover_patent_source_dirs(source_root)
target = FileSeedTarget(bucket_dir)

uploaded_objects = 0
failed = 0
for index, source_dir in enumerate(source_dirs, start=1):
    try:
        plan = build_patent_original_backfill_plan(source_dir, provider=provider)
        for upload in plan.uploads:
            if upload.content_bytes is not None:
                target.upload_bytes(
                    object_name=upload.object_name,
                    payload=upload.content_bytes,
                    content_type=upload.content_type,
                )
            elif upload.source_path is not None:
                target.upload_file(
                    object_name=upload.object_name,
                    source_path=upload.source_path,
                    content_type=upload.content_type,
                )
            else:
                raise ValueError(f"upload spec has no payload source: {upload.object_name}")
            uploaded_objects += 1
        print(f"patent {index}/{len(source_dirs)}: {plan.canonical_patent_id} objects={len(plan.uploads)}")
    except Exception as exc:
        failed += 1
        print(f"failed: {source_dir}: {exc}", file=sys.stderr)

print(
    "patent originals collection complete: "
    f"plans={len(source_dirs)} failed={failed} objects={uploaded_objects} target={bucket_dir / 'patent' / 'originals'}"
)
raise SystemExit(1 if failed else 0)
PY
    backfill_patent_tables_into_seed
    check_patent_tables_seed_consistency
  fi
fi

echo "minio seed collection complete: $TARGET_DIR"
