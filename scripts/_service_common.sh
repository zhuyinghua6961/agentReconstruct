#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$ROOT_DIR/resource"

SERVICES=(public-service fastQA highThinkingQA gateway)

service_port() {
  case "$1" in
    gateway) echo 8101 ;;
    public-service) echo 8102 ;;
    fastQA) echo 8008 ;;
    highThinkingQA) echo 8009 ;;
    *) return 1 ;;
  esac
}

service_health_url() {
  case "$1" in
    gateway) echo "http://127.0.0.1:8101/docs" ;;
    public-service) echo "http://127.0.0.1:8102/api/health" ;;
    fastQA) echo "http://127.0.0.1:8008/api/health" ;;
    highThinkingQA) echo "http://127.0.0.1:8009/api/health" ;;
    *) return 1 ;;
  esac
}

service_pid_file() {
  case "$1" in
    gateway) echo "$ROOT_DIR/gateway/.runtime/gateway-gunicorn.pid" ;;
    public-service) echo "$ROOT_DIR/public-service/.runtime/public-service-gunicorn.pid" ;;
    fastQA) echo "$RESOURCE_DIR/runtime/dev/fastQA/fastqa-gunicorn.pid" ;;
    highThinkingQA) echo "$RESOURCE_DIR/runtime/dev/highThinkingQA/gunicorn.pid" ;;
    *) return 1 ;;
  esac
}

run_service_script() {
  local service="$1"
  local action="$2"

  case "$service:$action" in
    gateway:start)
      GATEWAY_PORT=8101 \
      PUBLIC_BACKEND_BASE_URL="http://127.0.0.1:8102" \
      FAST_BACKEND_BASE_URL="http://127.0.0.1:8008" \
      THINKING_BACKEND_BASE_URL="http://127.0.0.1:8009" \
      bash "$ROOT_DIR/gateway/scripts/start_gunicorn.sh"
      ;;
    gateway:stop)
      GATEWAY_PORT=8101 \
      bash "$ROOT_DIR/gateway/scripts/stop_gunicorn.sh"
      ;;
    gateway:status)
      GATEWAY_PORT=8101 \
      bash "$ROOT_DIR/gateway/scripts/status_gunicorn.sh"
      ;;
    public-service:start)
      PUBLIC_SERVICE_PORT=8102 \
      PUBLIC_SERVICE_ENV_FILES="$ROOT_DIR/public-service/config.shared.env:$ROOT_DIR/public-service/config.secret.env" \
      bash "$ROOT_DIR/public-service/scripts/start_gunicorn.sh"
      ;;
    public-service:stop)
      PUBLIC_SERVICE_PORT=8102 \
      bash "$ROOT_DIR/public-service/scripts/stop_gunicorn.sh"
      ;;
    public-service:status)
      PUBLIC_SERVICE_PORT=8102 \
      bash "$ROOT_DIR/public-service/scripts/status_gunicorn.sh"
      ;;
    fastQA:start)
      FASTQA_SERVICE_CONFIG_ROOT="$RESOURCE_DIR/config/services/fastQA" \
      FASTQA_SERVICE_STATE_ROOT="$RESOURCE_DIR/state/dev/fastQA" \
      FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \
      FASTQA_SERVICE_ASSET_ROOT="$RESOURCE_DIR/assets" \
      APP_PORT=8008 \
      FASTAPI_PORT=8008 \
      BACKEND_PORT=8008 \
      bash "$ROOT_DIR/fastQA/scripts/start_gunicorn.sh"
      ;;
    fastQA:stop)
      FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \
      APP_PORT=8008 \
      FASTAPI_PORT=8008 \
      bash "$ROOT_DIR/fastQA/scripts/stop_gunicorn.sh"
      ;;
    fastQA:status)
      FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \
      APP_PORT=8008 \
      FASTAPI_PORT=8008 \
      bash "$ROOT_DIR/fastQA/scripts/status_gunicorn.sh"
      ;;
    highThinkingQA:start)
      HIGHTHINKINGQA_SERVICE_CONFIG_ROOT="$RESOURCE_DIR/config/services/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_STATE_ROOT="$RESOURCE_DIR/state/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_ASSET_ROOT="$RESOURCE_DIR/assets" \
      APP_PORT=8009 \
      bash "$ROOT_DIR/highThinkingQA/scripts/start_fastapi_gunicorn.sh"
      ;;
    highThinkingQA:stop)
      HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \
      APP_PORT=8009 \
      bash "$ROOT_DIR/highThinkingQA/scripts/stop_fastapi_gunicorn.sh"
      ;;
    highThinkingQA:status)
      HIGHTHINKINGQA_SERVICE_CONFIG_ROOT="$RESOURCE_DIR/config/services/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_STATE_ROOT="$RESOURCE_DIR/state/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_ASSET_ROOT="$RESOURCE_DIR/assets" \
      APP_PORT=8009 \
      bash "$ROOT_DIR/highThinkingQA/scripts/status_fastapi_gunicorn.sh"
      ;;
    *)
      echo "unsupported service/action: $service $action" >&2
      return 1
      ;;
  esac
}

wait_for_port_state() {
  local port="$1"
  local expected="$2"
  local timeout="${3:-30}"

  for _ in $(seq 1 "$timeout"); do
    local active="0"
    if ss -ltn "( sport = :$port )" 2>/dev/null | rg -q ":${port}\\b"; then
      active="1"
    fi
    if [[ "$active" == "$expected" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

force_cleanup_service() {
  local service="$1"
  local port
  local pid_file
  port="$(service_port "$service")"
  pid_file="$(service_pid_file "$service")"

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 2
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pid_file"
  fi

  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" 2>/dev/null || true
  fi
}

probe_health() {
  local service="$1"
  local url
  url="$(service_health_url "$service")"
  curl -fsS --max-time 5 "$url" >/dev/null 2>&1
}

wait_for_service_health() {
  local service="$1"
  local timeout="${2:-60}"

  for _ in $(seq 1 "$timeout"); do
    if probe_health "$service"; then
      return 0
    fi
    sleep 1
  done
  return 1
}
