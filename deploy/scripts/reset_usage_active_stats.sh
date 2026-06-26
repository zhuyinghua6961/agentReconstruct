#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"
SQL_FILE="$DEPLOY_DIR/mysql-init/005_reset_usage_active_stats.sql"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-lifeo4agent}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  exit 1
fi
if [[ ! -f "$SQL_FILE" ]]; then
  echo "sql file not found: $SQL_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/env_file_loader.sh"
capture_env_file_loader_process_keys
load_env_files_preserving_process_env "$ENV_FILE"

MYSQL_DATABASE="${MYSQL_DATABASE:-agentcode}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_KEY_PREFIX="${REDIS_KEY_PREFIX:-public_service}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-deploy-mysql}"
REDIS_CONTAINER="${REDIS_CONTAINER:-deploy-redis}"

mysql_exec() {
  if docker ps --format '{{.Names}}' | grep -qx "$MYSQL_CONTAINER"; then
    docker exec -i "$MYSQL_CONTAINER" \
      mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" "$@"
    return
  fi
  MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
  MYSQL_PORT="${MYSQL_PUBLISH_PORT:-3306}"
  mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" "$@"
}

redis_delete_pattern() {
  local pattern="$1"
  if [[ -z "$REDIS_PASSWORD" ]]; then
    echo "skip redis cleanup: REDIS_PASSWORD not set"
    return 0
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx "$REDIS_CONTAINER"; then
    echo "skip redis cleanup: container $REDIS_CONTAINER not running"
    return 0
  fi
  docker exec "$REDIS_CONTAINER" redis-cli -a "$REDIS_PASSWORD" --no-auth-warning EVAL "
local cursor = '0'
local total = 0
repeat
  local result = redis.call('SCAN', cursor, 'MATCH', ARGV[1], 'COUNT', 200)
  cursor = result[1]
  local keys = result[2]
  for _, key in ipairs(keys) do
    redis.call('DEL', key)
    total = total + 1
  end
until cursor == '0'
return total
" 0 "$pattern"
}

echo "== usage stats active-time cleanup =="
echo "database: $MYSQL_DATABASE"
echo "sql: $SQL_FILE"

before_sessions="$(mysql_exec -N -e "SELECT COUNT(*) FROM user_online_sessions;")"
before_active="$(mysql_exec -N -e "SELECT COALESCE(SUM(active_seconds), 0) FROM user_daily_stats;")"
echo "before: online_sessions=$before_sessions total_active_seconds=$before_active"

mysql_exec < "$SQL_FILE"

after_sessions="$(mysql_exec -N -e "SELECT COUNT(*) FROM user_online_sessions;")"
after_active="$(mysql_exec -N -e "SELECT COALESCE(SUM(active_seconds), 0) FROM user_daily_stats;")"
echo "after mysql: online_sessions=$after_sessions total_active_seconds=$after_active"

deleted_sessions="$(redis_delete_pattern "${REDIS_KEY_PREFIX}:usage_stats:*" || true)"
deleted_locks="$(redis_delete_pattern "${REDIS_KEY_PREFIX}:lock:usage_stats:*" || true)"
echo "redis deleted: session_keys=${deleted_sessions:-0} lock_keys=${deleted_locks:-0}"

echo "cleanup complete"
