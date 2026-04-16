#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/resource" 2>/dev/null && pwd || true)"

FRONTEND_DIST_DIR="${FRONTEND_DIST_DIR:-$ROOT_DIR/frontend-vue/dist}"
FRONTEND_NGINX_PORT="${FRONTEND_NGINX_PORT:-9093}"
GATEWAY_UPSTREAM_URL="${GATEWAY_UPSTREAM_URL:-http://127.0.0.1:8101}"
NGINX_RUNTIME_ROOT="${NGINX_RUNTIME_ROOT:-}"
NGINX_LOG_ROOT="${NGINX_LOG_ROOT:-}"

if [[ -z "$NGINX_RUNTIME_ROOT" ]]; then
  if [[ -n "${RESOURCE_DIR:-}" ]]; then
    NGINX_RUNTIME_ROOT="$ROOT_DIR/resource/runtime/dev/frontend-nginx"
  else
    NGINX_RUNTIME_ROOT="$ROOT_DIR/.runtime/frontend-nginx"
  fi
fi

if [[ -z "$NGINX_LOG_ROOT" ]]; then
  if [[ -n "${RESOURCE_DIR:-}" ]]; then
    NGINX_LOG_ROOT="$ROOT_DIR/resource/logs/dev/frontend-nginx"
  else
    NGINX_LOG_ROOT="$ROOT_DIR/.runtime/frontend-nginx/logs"
  fi
fi

PID_FILE="$NGINX_RUNTIME_ROOT/nginx.pid"
RENDERED_CONF="$NGINX_RUNTIME_ROOT/frontend-vue-gateway.nginx.conf"

echo "frontend_dist=$FRONTEND_DIST_DIR"
echo "gateway_upstream=$GATEWAY_UPSTREAM_URL"
echo "port=$FRONTEND_NGINX_PORT"
echo "runtime_root=$NGINX_RUNTIME_ROOT"
echo "log_root=$NGINX_LOG_ROOT"
echo "config=$RENDERED_CONF"
echo "pid_file=$PID_FILE"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "frontend nginx running: pid=$PID"
    exit 0
  fi
fi

echo "frontend nginx not running"
exit 1
