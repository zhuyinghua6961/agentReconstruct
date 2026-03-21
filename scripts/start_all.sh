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
  if ! wait_for_port_state "$port" 1 60; then
    echo "[error] $service did not bind to :$port"
    exit 1
  fi
  if ! probe_health "$service"; then
    echo "[error] $service bound to :$port but health probe failed"
    exit 1
  fi
done

echo "all backend services started"
