#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT_DIR/.pytest_cache" "$ROOT_DIR/.tmp"

cd "$ROOT_DIR"
PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" TMPDIR="$ROOT_DIR/.tmp" exec conda run -n agent pytest -o cache_dir="$ROOT_DIR/.pytest_cache" "$@"
