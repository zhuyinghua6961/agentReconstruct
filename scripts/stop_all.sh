#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/_service_common.sh"

STOP_ORDER=(gateway patent highThinkingQA fastQA public-service)

run_gateway_admission_worker stop >/dev/null 2>&1 || true
wait_for_pid_state "$(gateway_admission_worker_pid_file)" 0 15 || true

for service in "${STOP_ORDER[@]}"; do
  port="$(service_port "$service")"
  echo "[stop] $service on :$port"
  run_service_script "$service" stop >/dev/null 2>&1 || true
  force_cleanup_service "$service"
  if ! wait_for_port_state "$port" 0 15; then
    echo "[error] $service port $port is still occupied"
    exit 1
  fi
done

echo "all backend services stopped"
