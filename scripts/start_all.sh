#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/_service_common.sh"

for service in "${SERVICES[@]}"; do
  port="$(service_port "$service")"
  echo "[start] $service on :$port"
  run_service_script "$service" stop >/dev/null 2>&1 || true
  force_cleanup_service "$service"
  if ! wait_for_port_state "$port" 0 15; then
    echo "[error] $service port $port was not released before start"
    exit 1
  fi
  run_service_script "$service" start
  if ! wait_for_port_state "$port" 1 90; then
    echo "[error] $service did not bind to :$port"
    exit 1
  fi
  if ! wait_for_service_health "$service" 120; then
    echo "[error] $service bound to :$port but did not become healthy within timeout"
    exit 1
  fi
done

if gateway_admission_worker_enabled; then
  echo "[start] gateway-admission-worker"
  run_gateway_admission_worker stop >/dev/null 2>&1 || true
  if ! wait_for_pid_state "$(gateway_admission_worker_pid_file)" 0 15; then
    echo "[error] gateway-admission-worker pid was not released before start"
    exit 1
  fi
  run_gateway_admission_worker start
  if ! wait_for_pid_state "$(gateway_admission_worker_pid_file)" 1 30 3; then
    echo "[error] gateway-admission-worker did not stay running"
    exit 1
  fi
fi

echo "all backend services started"
