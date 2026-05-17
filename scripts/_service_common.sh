#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$ROOT_DIR/resource"
source "$ROOT_DIR/scripts/env_file_loader.sh"
capture_env_file_loader_process_keys

SERVICES=(public-service fastQA highThinkingQA patent gateway)

env_bool() {
  local value
  value="$(printf '%s' "${1:-0}" | tr '[:upper:]' '[:lower:]')"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

shared_env_files() {
  echo "$RESOURCE_DIR/config/shared/infrastructure.shared.env:$RESOURCE_DIR/config/shared/model-endpoints.shared.env:$RESOURCE_DIR/config/shared/infrastructure.secret.env:$RESOURCE_DIR/config/shared/model-endpoints.secret.env:$RESOURCE_DIR/config/shared/graph.shared.env:$RESOURCE_DIR/config/shared/graph.secret.env"
}

load_shared_service_port_defaults() {
  local file="$RESOURCE_DIR/config/shared/infrastructure.shared.env"
  local raw_line line name value
  [[ -f "$file" ]] || return 0
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="${raw_line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || continue
    name="${line%%=*}"
    value="${line#*=}"
    name="${name#"${name%%[![:space:]]*}"}"
    name="${name%"${name##*[![:space:]]}"}"
    case "$name" in
      GATEWAY_PORT|PUBLIC_SERVICE_PORT|FASTQA_PORT|FASTQA_FASTAPI_PORT|HIGHTHINKINGQA_PORT|PATENT_PORT) ;;
      *) continue ;;
    esac
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
      value="${value:1:${#value}-2}"
    fi
    if [[ -z "${ENV_FILE_LOADER_PROCESS_KEYS[$name]+x}" ]]; then
      export "${name}=${value}"
    fi
  done < "$file"
}

load_shared_service_port_defaults

gateway_env_files() {
  local config_dir_default="$ROOT_DIR/gateway"
  local shared_env_files
  shared_env_files="$(shared_env_files)"
  if [[ -d "$RESOURCE_DIR/config/services/gateway" ]]; then
    config_dir_default="$RESOURCE_DIR/config/services/gateway"
  fi
  echo "${GATEWAY_ENV_FILES:-$ROOT_DIR/gateway/config.env:$ROOT_DIR/gateway/config.shared.env:$ROOT_DIR/gateway/config.secret.env:$ROOT_DIR/gateway/.env:$shared_env_files:$config_dir_default/config.shared.env:$config_dir_default/config.secret.env:$config_dir_default/.env:$config_dir_default/config.env}"
}

load_gateway_env_files() {
  if [[ "${_GATEWAY_ENV_FILES_LOADED:-0}" == "1" ]]; then
    return 0
  fi
  local env_files
  env_files="$(gateway_env_files)"
  load_env_files_preserving_process_env "$env_files"
  _GATEWAY_ENV_FILES_LOADED=1
}

service_port() {
  case "$1" in
    gateway) echo "${GATEWAY_PORT:-8101}" ;;
    public-service) echo "${PUBLIC_SERVICE_PORT:-8102}" ;;
    fastQA) echo "${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-8008}}" ;;
    highThinkingQA) echo "${HIGHTHINKINGQA_PORT:-8009}" ;;
    patent) echo "${PATENT_PORT:-8010}" ;;
    *) return 1 ;;
  esac
}

service_health_url() {
  case "$1" in
    gateway) echo "http://127.0.0.1:$(service_port gateway)/docs" ;;
    public-service) echo "http://127.0.0.1:$(service_port public-service)/api/health" ;;
    # Use /healthz for start_all liveness: /api/health returns 503 until generation_runtime_ready
    # (e.g. when generation runtime is disabled), which would make wait_for_service_health time out.
    fastQA) echo "http://127.0.0.1:$(service_port fastQA)/healthz" ;;
    highThinkingQA) echo "http://127.0.0.1:$(service_port highThinkingQA)/api/health" ;;
    patent) echo "http://127.0.0.1:$(service_port patent)/api/health" ;;
    *) return 1 ;;
  esac
}

service_pid_file() {
  case "$1" in
    gateway) echo "$ROOT_DIR/gateway/.runtime/gateway-gunicorn.pid" ;;
    public-service) echo "$ROOT_DIR/public-service/.runtime/public-service-gunicorn.pid" ;;
    fastQA) echo "$RESOURCE_DIR/runtime/dev/fastQA/fastqa-gunicorn.pid" ;;
    highThinkingQA) echo "$RESOURCE_DIR/runtime/dev/highThinkingQA/gunicorn.pid" ;;
    patent) echo "$RESOURCE_DIR/runtime/dev/patent/patent-gunicorn.pid" ;;
    *) return 1 ;;
  esac
}

run_service_script() {
  local service="$1"
  local action="$2"

  case "$service:$action" in
    gateway:start)
      bash "$ROOT_DIR/gateway/scripts/start_gunicorn.sh"
      ;;
    gateway:stop)
      bash "$ROOT_DIR/gateway/scripts/stop_gunicorn.sh"
      ;;
    gateway:status)
      bash "$ROOT_DIR/gateway/scripts/status_gunicorn.sh"
      ;;
    public-service:start)
      PUBLIC_SERVICE_ENV_FILES="${PUBLIC_SERVICE_ENV_FILES:-$ROOT_DIR/public-service/config.shared.env:$ROOT_DIR/public-service/config.secret.env:$ROOT_DIR/public-service/.env:$(shared_env_files):$RESOURCE_DIR/config/services/public-service/config.shared.env:$RESOURCE_DIR/config/services/public-service/config.secret.env:$RESOURCE_DIR/config/services/public-service/.env:$RESOURCE_DIR/config/services/public-service/config.env}" \
      bash "$ROOT_DIR/public-service/scripts/start_gunicorn.sh"
      ;;
    public-service:stop)
      bash "$ROOT_DIR/public-service/scripts/stop_gunicorn.sh"
      ;;
    public-service:status)
      bash "$ROOT_DIR/public-service/scripts/status_gunicorn.sh"
      ;;
    fastQA:start)
      FASTQA_SERVICE_CONFIG_ROOT="$RESOURCE_DIR/config/services/fastQA" \
      FASTQA_SERVICE_STATE_ROOT="$RESOURCE_DIR/state/dev/fastQA" \
      FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \
      FASTQA_SERVICE_ASSET_ROOT="$RESOURCE_DIR/assets" \
      bash "$ROOT_DIR/fastQA/scripts/start_gunicorn.sh"
      ;;
    fastQA:stop)
      FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \
      bash "$ROOT_DIR/fastQA/scripts/stop_gunicorn.sh"
      ;;
    fastQA:status)
      FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \
      bash "$ROOT_DIR/fastQA/scripts/status_gunicorn.sh"
      ;;
    highThinkingQA:start)
      HIGHTHINKINGQA_SERVICE_CONFIG_ROOT="$RESOURCE_DIR/config/services/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_STATE_ROOT="$RESOURCE_DIR/state/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_ASSET_ROOT="$RESOURCE_DIR/assets" \
      bash "$ROOT_DIR/highThinkingQA/scripts/start_fastapi_gunicorn.sh"
      ;;
    highThinkingQA:stop)
      HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \
      bash "$ROOT_DIR/highThinkingQA/scripts/stop_fastapi_gunicorn.sh"
      ;;
    highThinkingQA:status)
      HIGHTHINKINGQA_SERVICE_CONFIG_ROOT="$RESOURCE_DIR/config/services/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_STATE_ROOT="$RESOURCE_DIR/state/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \
      HIGHTHINKINGQA_SERVICE_ASSET_ROOT="$RESOURCE_DIR/assets" \
      bash "$ROOT_DIR/highThinkingQA/scripts/status_fastapi_gunicorn.sh"
      ;;
    patent:start)
      PATENT_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/patent" \
      PATENT_SERVICE_LOG_ROOT="$RESOURCE_DIR/logs/dev/patent" \
      bash "$ROOT_DIR/patent/scripts/start_gunicorn.sh"
      ;;
    patent:stop)
      PATENT_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/patent" \
      bash "$ROOT_DIR/patent/scripts/stop_gunicorn.sh"
      ;;
    patent:status)
      PATENT_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/patent" \
      PATENT_SERVICE_LOG_ROOT="$RESOURCE_DIR/logs/dev/patent" \
      bash "$ROOT_DIR/patent/scripts/status_gunicorn.sh"
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

gateway_admission_worker_enabled() {
  load_gateway_env_files
  return 0
}

gateway_admission_worker_pid_file() {
  echo "$ROOT_DIR/gateway/.runtime/gateway-admission-worker.pid"
}

run_gateway_admission_worker() {
  local action="$1"
  case "$action" in
    start)
      bash "$ROOT_DIR/gateway/scripts/start_admission_worker.sh"
      ;;
    stop)
      bash "$ROOT_DIR/gateway/scripts/stop_admission_worker.sh"
      ;;
    status)
      bash "$ROOT_DIR/gateway/scripts/status_admission_worker.sh"
      ;;
    *)
      echo "unsupported gateway admission worker action: $action" >&2
      return 1
      ;;
  esac
}

wait_for_pid_state() {
  local pid_file="$1"
  local expected="$2"
  local timeout="${3:-30}"
  local stable_checks="${4:-1}"
  local consecutive_matches=0
  for _ in $(seq 1 "$timeout"); do
    local active="0"
    if [[ -f "$pid_file" ]]; then
      local pid
      pid="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
        active="1"
      fi
    fi
    if [[ "$active" == "$expected" ]]; then
      if [[ "$expected" == "1" ]]; then
        consecutive_matches=$((consecutive_matches + 1))
        if (( consecutive_matches >= stable_checks )); then
          return 0
        fi
      else
        return 0
      fi
    else
      consecutive_matches=0
    fi
    sleep 1
  done
  return 1
}
