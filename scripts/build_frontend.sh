#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${FRONTEND_DIR:-$ROOT_DIR/frontend-vue}"

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "frontend directory not found: $FRONTEND_DIR" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to build the frontend" >&2
  exit 1
fi

cd "$FRONTEND_DIR"
npm run build

echo "frontend build ready: $FRONTEND_DIR/dist"
