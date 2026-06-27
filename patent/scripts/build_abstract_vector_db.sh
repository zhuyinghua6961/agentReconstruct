#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export EMBEDDING_MODEL_TYPE="${EMBEDDING_MODEL_TYPE:-local}"
export EMBEDDING_MODEL_PATH="${EMBEDDING_MODEL_PATH:-/home/cqy/BGE}"
PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec conda run --no-capture-output -n agent python scripts/build_abstract_vector_db.py "$@"
