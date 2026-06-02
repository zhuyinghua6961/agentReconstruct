#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  echo "hint: cp deploy/.env.production.example deploy/.env" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
load_env_files_preserving_process_env "$ENV_FILE"

DATA_DIR="${DEPLOY_DATA_DIR:-$DEPLOY_DIR/data}"
STAGING_ROOT="${DEPLOY_DATA_STAGING_DIR:-$DEPLOY_DIR/.runtime/data-packages}"
DATA_PACKAGE_VERSION="${DATA_PACKAGE_VERSION:-$(date +%F)}"
MINIO_BUCKET="${MINIO_BUCKET:-agentcode}"
PYTHON_BIN="${PYTHON_BIN:-python}"
ZSTD_LEVEL="${DATA_PACKAGE_ZSTD_LEVEL:-10}"

FASTQA_SRC="${FASTQA_SRC:-$ROOT_DIR/resource/fastqa}"
HIGHTHINKINGQA_SRC="${HIGHTHINKINGQA_SRC:-$ROOT_DIR/resource/highThinkingQA}"
PATENTQA_SRC="${PATENTQA_SRC:-$ROOT_DIR/resource/patentQA}"
PUBLIC_SERVICE_SRC="${PUBLIC_SERVICE_SRC:-$ROOT_DIR/public-service/data/runtime}"
MINIO_SEED_CONTEXT="${DEPLOY_MINIO_SEED_CONTEXT:-$DEPLOY_DIR/minio-seed/$MINIO_BUCKET}"

NEO4J_LITERATURE_DUMP_SRC="${NEO4J_LITERATURE_DUMP_SRC:-}"
NEO4J_PATENT_DUMP_SRC="${NEO4J_PATENT_DUMP_SRC:-}"
PACKAGE_NEO4J_DUMPS="${PACKAGE_NEO4J_DUMPS:-1}"

require_dir() {
  local path="$1"
  if [[ ! -d "$path" ]]; then
    echo "required directory not found: $path" >&2
    exit 1
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "required file not found: $path" >&2
    exit 1
  fi
}

copy_dir_if_exists() {
  local src="$1"
  local dest="$2"
  if [[ -d "$src" ]]; then
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
  fi
}

copy_file_if_exists() {
  local src="$1"
  local dest="$2"
  if [[ -f "$src" ]]; then
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
  fi
}

make_tar_zst() {
  local src_dir="$1"
  local out_file="$2"
  rm -f "$out_file"
  tar -C "$src_dir" -I "zstd -T0 -${ZSTD_LEVEL}" -cf "$out_file" .
}

compress_dump_zst() {
  local src_file="$1"
  local out_file="$2"
  rm -f "$out_file"
  zstd -T0 "-${ZSTD_LEVEL}" -f -c "$src_file" > "$out_file"
}

prepare_staging() {
  rm -rf "$STAGING_ROOT"
  mkdir -p "$STAGING_ROOT" "$DATA_DIR"
}

stage_minio_originals() {
  echo "staging minio originals from $MINIO_SEED_CONTEXT"
  require_dir "$MINIO_SEED_CONTEXT/papers"
  require_dir "$MINIO_SEED_CONTEXT/patent/originals"
  local source_table_count seed_table_count
  source_table_count="$(find "$PATENTQA_SRC" -type f -name '*_tables.json' | wc -l | tr -d ' ')"
  seed_table_count="$(find "$MINIO_SEED_CONTEXT/patent/originals" -path '*/structured/tables.json' -type f | wc -l | tr -d ' ')"
  if [[ "$source_table_count" != "$seed_table_count" ]]; then
    echo "MinIO originals tables mismatch: source=$source_table_count seed=$seed_table_count" >&2
    echo "hint: rerun bash deploy/scripts/collect_minio_seed.sh $MINIO_BUCKET --clean before packaging" >&2
    exit 1
  fi
  mkdir -p "$STAGING_ROOT"
  ln -sfn "$MINIO_SEED_CONTEXT" "$STAGING_ROOT/minio-originals"
}

stage_fastqa_ref() {
  local target="$STAGING_ROOT/fastqa-ref"
  mkdir -p "$target"
  copy_dir_if_exists "$FASTQA_SRC/vector_database" "$target/vector_database"
  copy_dir_if_exists "$FASTQA_SRC/vector_database_md" "$target/vector_database_md"
  copy_dir_if_exists "$FASTQA_SRC/community_vector_database" "$target/community_vector_database"
  copy_file_if_exists "$FASTQA_SRC/vector_db_topic_index.json" "$target/vector_db_topic_index.json"
}

stage_highthinking_ref() {
  local target="$STAGING_ROOT/highthinking-ref"
  mkdir -p "$target/papers"
  copy_dir_if_exists "$HIGHTHINKINGQA_SRC/vectordb" "$target/vectordb"
}

stage_public_service_ref() {
  local target="$STAGING_ROOT/public-service-ref"
  mkdir -p "$target"
  copy_dir_if_exists "$PUBLIC_SERVICE_SRC/vector_database" "$target/vector_database"
}

stage_patentqa_ref() {
  local target="$STAGING_ROOT/patentqa-ref"
  mkdir -p "$target"
  copy_dir_if_exists "$PATENTQA_SRC/vector_db_patent_abstracts" "$target/vector_db_patent_abstracts"
  copy_dir_if_exists "$PATENTQA_SRC/vector_db_patent_chunks" "$target/vector_db_patent_chunks"

  local archive
  archive="$(find "$PATENTQA_SRC" -maxdepth 1 -type d -name '__*' | sort | head -n 1 || true)"
  if [[ -z "$archive" ]]; then
    echo "patent archive directory not found under $PATENTQA_SRC" >&2
    exit 1
  fi
  mkdir -p "$target/$(basename "$archive")"
  tar \
    --exclude='*.pdf' \
    --exclude='*.png' \
    --exclude='*.jpg' \
    --exclude='*.jpeg' \
    -C "$archive" \
    -cf - . | tar -C "$target/$(basename "$archive")" -xf -
}

package_all() {
  echo "validating staged data trees"
  "$PYTHON_BIN" "$DEPLOY_DIR/scripts/validate_data_packages.py" \
    --skip-manifest \
    --staging-root "$STAGING_ROOT" >/tmp/lifeo4agent-data-staging-validation.json || {
      cat /tmp/lifeo4agent-data-staging-validation.json >&2
      exit 1
    }

  echo "creating tar.zst packages in $DATA_DIR"
  make_tar_zst "$STAGING_ROOT/minio-originals" "$DATA_DIR/minio-originals.tar.zst"
  make_tar_zst "$STAGING_ROOT/fastqa-ref" "$DATA_DIR/fastqa-ref.tar.zst"
  make_tar_zst "$STAGING_ROOT/highthinking-ref" "$DATA_DIR/highthinking-ref.tar.zst"
  make_tar_zst "$STAGING_ROOT/patentqa-ref" "$DATA_DIR/patentqa-ref.tar.zst"
  make_tar_zst "$STAGING_ROOT/public-service-ref" "$DATA_DIR/public-service-ref.tar.zst"

  if [[ "$PACKAGE_NEO4J_DUMPS" == "1" ]]; then
    if [[ -z "$NEO4J_LITERATURE_DUMP_SRC" || -z "$NEO4J_PATENT_DUMP_SRC" ]]; then
      echo "NEO4J_LITERATURE_DUMP_SRC and NEO4J_PATENT_DUMP_SRC are required when PACKAGE_NEO4J_DUMPS=1" >&2
      echo "hint: create consistent dumps during the Neo4j maintenance window, then rerun this script with both env vars set" >&2
      exit 1
    fi
    require_file "$NEO4J_LITERATURE_DUMP_SRC"
    require_file "$NEO4J_PATENT_DUMP_SRC"
    compress_dump_zst "$NEO4J_LITERATURE_DUMP_SRC" "$DATA_DIR/neo4j-literature.dump.zst"
    compress_dump_zst "$NEO4J_PATENT_DUMP_SRC" "$DATA_DIR/neo4j-patent.dump.zst"
  fi
}

write_manifest() {
  "$PYTHON_BIN" - "$DATA_DIR" "$DATA_PACKAGE_VERSION" "$STAGING_ROOT" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

data_dir = Path(sys.argv[1]).resolve()
version = str(sys.argv[2]).strip() or "latest"
staging_root = Path(sys.argv[3]).resolve()
package_names = [
    ("minio-originals", "minio-originals.tar.zst"),
    ("fastqa-ref", "fastqa-ref.tar.zst"),
    ("highthinking-ref", "highthinking-ref.tar.zst"),
    ("patentqa-ref", "patentqa-ref.tar.zst"),
    ("public-service-ref", "public-service-ref.tar.zst"),
    ("neo4j-literature", "neo4j-literature.dump.zst"),
    ("neo4j-patent", "neo4j-patent.dump.zst"),
]

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())

def package_counts(name: str) -> dict[str, int]:
    root = staging_root / name
    if not root.exists():
        return {}
    counts = {"file_count": count_files(root)}
    if name == "minio-originals":
        papers = root / "papers"
        patents = root / "patent" / "originals"
        counts.update(
            {
                "papers": sum(1 for item in papers.iterdir() if item.is_file()) if papers.is_dir() else 0,
                "patent_dirs": sum(1 for item in patents.iterdir() if item.is_dir()) if patents.is_dir() else 0,
                "patent_tables": len(list(patents.glob("*/structured/tables.json"))) if patents.is_dir() else 0,
            }
        )
    elif name == "patentqa-ref":
        archive_dirs = [item for item in root.iterdir() if item.is_dir() and item.name.startswith("__")]
        patent_json_dirs = 0
        for archive_dir in archive_dirs:
            patent_json_dirs += sum(1 for item in archive_dir.iterdir() if item.is_dir())
        counts.update(
            {
                "archive_dirs": len(archive_dirs),
                "patent_json_dirs": patent_json_dirs,
            }
        )
    elif name.endswith("-ref"):
        counts["chroma_sqlite_files"] = len(list(root.rglob("chroma.sqlite3")))
    return counts

packages = {}
for name, file_name in package_names:
    path = data_dir / file_name
    if not path.is_file():
        continue
    packages[name] = {
        "file": file_name,
        "version": version,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "counts": package_counts(name),
    }

manifest = {
    "schema_version": 1,
    "data_version": version,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "packages": packages,
}
(data_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote manifest: {data_dir / 'manifest.json'}")
PY
}

prepare_staging
stage_minio_originals
stage_fastqa_ref
stage_highthinking_ref
stage_public_service_ref
stage_patentqa_ref
package_all
write_manifest

"$PYTHON_BIN" "$DEPLOY_DIR/scripts/validate_data_packages.py" --data-dir "$DATA_DIR"
echo "data package build complete: $DATA_DIR"
