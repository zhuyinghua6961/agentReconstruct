#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/resource/runtime/dev/highThinkingQA"
LOG_DIR="$RUNTIME_DIR/logs"
PID_FILE="$RUNTIME_DIR/local-embedding-server.pid"
LOG_FILE="$LOG_DIR/local-embedding-server.log"

CONDA_ENV="${CONDA_ENV:-agent}"
export QWEN3_EMBEDDING_MODEL_PATH="${QWEN3_EMBEDDING_MODEL_PATH:-/home/cqy/qwen3_embedding_8b}"
export QWEN3_EMBEDDING_MODEL_NAME="${QWEN3_EMBEDDING_MODEL_NAME:-qwen3-embedding-8b}"
export QWEN3_EMBEDDING_DIMENSIONS="${QWEN3_EMBEDDING_DIMENSIONS:-4096}"
export QWEN3_EMBEDDING_ALLOW_DIMENSIONS="${QWEN3_EMBEDDING_ALLOW_DIMENSIONS:-0}"
export QWEN3_EMBEDDING_PORT="${QWEN3_EMBEDDING_PORT:-8014}"

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "local embedding server already running: pid=$old_pid"
    echo "url=http://127.0.0.1:$QWEN3_EMBEDDING_PORT/v1"
    exit 0
  fi
fi

cd "$SERVICE_DIR"
nohup conda run --no-capture-output -n "$CONDA_ENV" python -m local_embedding_server >"$LOG_FILE" 2>&1 &
pid="$!"
printf "%s\n" "$pid" >"$PID_FILE"

echo "local embedding server starting: pid=$pid"
echo "url=http://127.0.0.1:$QWEN3_EMBEDDING_PORT/v1"
echo "log=$LOG_FILE"
