#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"
PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec conda run -n agent gunicorn "server_fastapi.app:create_app()" --config "$ROOT_DIR/server_fastapi/gunicorn.conf.py"
