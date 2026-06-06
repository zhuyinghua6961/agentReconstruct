#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
PID_FILE="$ROOT_DIR/resource/runtime/dev/highThinkingQA/local-embedding-server.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "local embedding server is not running"
  exit 0
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "local embedding server is not running"
  exit 0
fi

kill "$pid"
rm -f "$PID_FILE"
echo "local embedding server stopped: pid=$pid"
