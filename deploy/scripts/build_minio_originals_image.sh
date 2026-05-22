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

echo "warning: build_minio_originals_image.sh is a legacy/debug path; recommended releases use deploy/data/minio-originals.tar.zst" >&2

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

MINIO_BUCKET="${MINIO_BUCKET:-agentcode}"
MINIO_ORIGINALS_IMAGE="${MINIO_ORIGINALS_IMAGE:-lifeo4agent/minio-originals:$(date +%F)}"
MINIO_MC_IMAGE_TAG="${MINIO_MC_IMAGE_TAG:-latest}"
SEED_CONTEXT="${DEPLOY_MINIO_SEED_CONTEXT:-$DEPLOY_DIR/minio-seed/$MINIO_BUCKET}"
PATENT_ORIGINALS_SRC="${PATENT_ORIGINALS_SRC:-$ROOT_DIR/resource/patentQA}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if [[ ! -d "$SEED_CONTEXT" ]]; then
  echo "minio seed context not found: $SEED_CONTEXT" >&2
  echo "hint: bash deploy/scripts/collect_minio_seed.sh $MINIO_BUCKET --clean" >&2
  exit 1
fi
if [[ ! -d "$SEED_CONTEXT/papers" ]]; then
  echo "missing papers seed directory: $SEED_CONTEXT/papers" >&2
  exit 1
fi
if [[ ! -d "$SEED_CONTEXT/patent/originals" ]]; then
  echo "missing patent originals seed directory: $SEED_CONTEXT/patent/originals" >&2
  exit 1
fi

papers_count="$(find "$SEED_CONTEXT/papers" -maxdepth 1 -type f | wc -l | tr -d ' ')"
patent_count="$(find "$SEED_CONTEXT/patent/originals" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
tables_count="$(find "$SEED_CONTEXT/patent/originals" -path '*/structured/tables.json' -type f | wc -l | tr -d ' ')"

if [[ "$papers_count" == "0" ]]; then
  echo "papers seed directory is empty: $SEED_CONTEXT/papers" >&2
  exit 1
fi
if [[ "$patent_count" == "0" ]]; then
  echo "patent originals seed directory is empty: $SEED_CONTEXT/patent/originals" >&2
  exit 1
fi

"$PYTHON_BIN" - "$SEED_CONTEXT" "$PATENT_ORIGINALS_SRC" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

seed_context = Path(sys.argv[1]).resolve()
source_root = Path(sys.argv[2]).resolve()
patent_seed_root = seed_context / "patent" / "originals"
seed_table_files = sorted(patent_seed_root.glob("*/structured/tables.json"))
errors: list[str] = []

for tables_path in seed_table_files:
    patent_dir = tables_path.parents[1]
    patent_id = patent_dir.name
    tables_ref = f"patent/originals/{patent_id}/structured/tables.json"
    manifest_path = patent_dir / "manifest.json"
    if not manifest_path.is_file():
        errors.append(f"missing manifest for {tables_ref}")
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

if source_root.is_dir():
    source_table_files = sorted(path for path in source_root.rglob("*_tables.json") if path.is_file())
    if len(seed_table_files) != len(source_table_files):
        errors.append(f"tables count mismatch: source={len(source_table_files)} seed={len(seed_table_files)}")

if errors:
    for item in errors[:50]:
        print(f"error: {item}", file=sys.stderr)
    if len(errors) > 50:
        print(f"error: ... {len(errors) - 50} more seed consistency errors", file=sys.stderr)
    raise SystemExit(1)

print(f"seed consistency ok: patent_tables={len(seed_table_files)}")
PY

echo "building MinIO originals image: $MINIO_ORIGINALS_IMAGE"
echo "seed context: $SEED_CONTEXT"
echo "seed counts: papers=$papers_count patent_dirs=$patent_count patent_tables=$tables_count"

docker build \
  -f "$DEPLOY_DIR/docker/Dockerfile.minio-originals" \
  --build-arg "MINIO_MC_IMAGE_TAG=$MINIO_MC_IMAGE_TAG" \
  -t "$MINIO_ORIGINALS_IMAGE" \
  "$SEED_CONTEXT"

echo "built MinIO originals image: $MINIO_ORIGINALS_IMAGE"
