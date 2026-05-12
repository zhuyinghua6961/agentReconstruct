#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/_service_common.sh"

for service in "${SERVICES[@]}"; do
  port="$(service_port "$service")"
  echo "== $service :$port =="
  run_service_script "$service" status || true
  if probe_health "$service"; then
    echo "health: ok"
  else
    echo "health: failed"
  fi
  echo
done

echo "== gateway-admission-worker =="
run_gateway_admission_worker status || true
echo
