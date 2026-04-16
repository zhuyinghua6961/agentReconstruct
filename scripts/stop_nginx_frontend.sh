#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/resource" 2>/dev/null && pwd || true)"

NGINX_BIN="${NGINX_BIN:-$(command -v nginx || echo /usr/sbin/nginx)}"
NGINX_RUNTIME_ROOT="${NGINX_RUNTIME_ROOT:-}"

if [[ -z "$NGINX_RUNTIME_ROOT" ]]; then
  if [[ -n "${RESOURCE_DIR:-}" ]]; then
    NGINX_RUNTIME_ROOT="$ROOT_DIR/resource/runtime/dev/frontend-nginx"
  else
    NGINX_RUNTIME_ROOT="$ROOT_DIR/.runtime/frontend-nginx"
  fi
fi

RENDERED_CONF="$NGINX_RUNTIME_ROOT/frontend-vue-gateway.nginx.conf"
PID_FILE="$NGINX_RUNTIME_ROOT/nginx.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "frontend nginx not running"
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "${PID:-}" ]] || ! kill -0 "$PID" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "frontend nginx stale pid removed"
  exit 0
fi

if [[ -x "$NGINX_BIN" && -f "$RENDERED_CONF" ]]; then
  "$NGINX_BIN" -p "$NGINX_RUNTIME_ROOT" -c "$RENDERED_CONF" -s stop || true
fi

for _ in $(seq 1 10); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "frontend nginx stopped"
    exit 0
  fi
  sleep 1
done

kill "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "frontend nginx stopped"
