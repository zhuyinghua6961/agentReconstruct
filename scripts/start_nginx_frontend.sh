#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$(cd "$ROOT_DIR/resource" 2>/dev/null && pwd || true)"

FRONTEND_DIST_DIR="${FRONTEND_DIST_DIR:-$ROOT_DIR/frontend-vue/dist}"
FRONTEND_NGINX_PORT="${FRONTEND_NGINX_PORT:-9093}"
GATEWAY_UPSTREAM_URL="${GATEWAY_UPSTREAM_URL:-http://127.0.0.1:8101}"
NGINX_BIN="${NGINX_BIN:-$(command -v nginx || echo /usr/sbin/nginx)}"
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

TEMPLATE_FILE="$ROOT_DIR/deploy/nginx/frontend-vue-gateway.nginx.conf.template"
RENDERED_CONF="$NGINX_RUNTIME_ROOT/frontend-vue-gateway.nginx.conf"
PID_FILE="$NGINX_RUNTIME_ROOT/nginx.pid"
BOOTSTRAP_ERROR_LOG="$NGINX_LOG_ROOT/bootstrap-error.log"

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[&|]/\\&/g'
}

print_status() {
  echo "frontend_dist=$FRONTEND_DIST_DIR"
  echo "gateway_upstream=$GATEWAY_UPSTREAM_URL"
  echo "port=$FRONTEND_NGINX_PORT"
  echo "runtime_root=$NGINX_RUNTIME_ROOT"
  echo "log_root=$NGINX_LOG_ROOT"
  echo "config=$RENDERED_CONF"
  echo "pid_file=$PID_FILE"
}

if [[ ! -x "$NGINX_BIN" ]]; then
  echo "nginx binary not found or not executable: $NGINX_BIN" >&2
  exit 1
fi

if [[ ! -f "$TEMPLATE_FILE" ]]; then
  echo "nginx template not found: $TEMPLATE_FILE" >&2
  exit 1
fi

if [[ ! -f "$FRONTEND_DIST_DIR/index.html" ]]; then
  echo "frontend dist is missing index.html: $FRONTEND_DIST_DIR/index.html" >&2
  echo "run scripts/build_frontend.sh first" >&2
  exit 1
fi

mkdir -p \
  "$NGINX_RUNTIME_ROOT" \
  "$NGINX_LOG_ROOT" \
  "$NGINX_RUNTIME_ROOT/client_body_temp" \
  "$NGINX_RUNTIME_ROOT/proxy_temp"

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "frontend nginx already running: pid=$EXISTING_PID"
    print_status
    exit 0
  fi
  rm -f "$PID_FILE"
fi

FRONTEND_DIST_ESCAPED="$(escape_sed_replacement "$FRONTEND_DIST_DIR")"
GATEWAY_UPSTREAM_ESCAPED="$(escape_sed_replacement "$GATEWAY_UPSTREAM_URL")"
NGINX_RUNTIME_ESCAPED="$(escape_sed_replacement "$NGINX_RUNTIME_ROOT")"
NGINX_LOG_ESCAPED="$(escape_sed_replacement "$NGINX_LOG_ROOT")"
PORT_ESCAPED="$(escape_sed_replacement "$FRONTEND_NGINX_PORT")"

sed \
  -e "s|__FRONTEND_DIST_DIR__|$FRONTEND_DIST_ESCAPED|g" \
  -e "s|__GATEWAY_UPSTREAM_URL__|$GATEWAY_UPSTREAM_ESCAPED|g" \
  -e "s|__NGINX_RUNTIME_ROOT__|$NGINX_RUNTIME_ESCAPED|g" \
  -e "s|__NGINX_LOG_ROOT__|$NGINX_LOG_ESCAPED|g" \
  -e "s|__FRONTEND_NGINX_PORT__|$PORT_ESCAPED|g" \
  "$TEMPLATE_FILE" >"$RENDERED_CONF"

"$NGINX_BIN" -g "error_log $BOOTSTRAP_ERROR_LOG notice;" -t -p "$NGINX_RUNTIME_ROOT" -c "$RENDERED_CONF"
"$NGINX_BIN" -g "error_log $BOOTSTRAP_ERROR_LOG notice;" -p "$NGINX_RUNTIME_ROOT" -c "$RENDERED_CONF"

for _ in $(seq 1 10); do
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "frontend nginx started: pid=$PID"
      print_status
      exit 0
    fi
  fi
  sleep 1
done

echo "frontend nginx failed to start" >&2
print_status
exit 1
