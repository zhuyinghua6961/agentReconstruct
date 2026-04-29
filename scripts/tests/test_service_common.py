from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / '_service_common.sh'


def _run_bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['bash', '-lc', script],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def test_wait_for_service_health_retries_until_success() -> None:
    result = _run_bash(
        f"""
        set -euo pipefail
        source '{SCRIPT}'
        attempts=0
        probe_health() {{
          attempts=$((attempts + 1))
          if [[ $attempts -lt 3 ]]; then
            return 1
          fi
          return 0
        }}
        wait_for_service_health public-service 5
        echo "attempts=$attempts"
        """
    )
    assert result.returncode == 0, result.stderr
    assert 'attempts=3' in result.stdout


def test_wait_for_service_health_times_out_when_probe_never_succeeds() -> None:
    result = _run_bash(
        f"""
        set -euo pipefail
        source '{SCRIPT}'
        probe_health() {{
          return 1
        }}
        if wait_for_service_health public-service 2; then
          echo unexpected-success
          exit 1
        fi
        echo expected-timeout
        """
    )
    assert result.returncode == 0, result.stderr
    assert 'expected-timeout' in result.stdout


def test_patent_service_is_registered_in_common_helpers() -> None:
    result = _run_bash(
        f"""
        set -euo pipefail
        source '{SCRIPT}'
        [[ " ${{SERVICES[*]}} " == *" patent "* ]]
        [[ "$(service_port patent)" == "8010" ]]
        [[ "$(service_health_url patent)" == "http://127.0.0.1:8010/api/health" ]]
        [[ "$(service_pid_file patent)" == "{ROOT}/resource/runtime/dev/patent/patent-gunicorn.pid" ]]
        echo registered
        """
    )
    assert result.returncode == 0, result.stderr
    assert 'registered' in result.stdout


def test_service_ports_honor_environment_overrides() -> None:
    result = _run_bash(
        f"""
        set -euo pipefail
        export PATENT_PORT=18110
        export FASTQA_FASTAPI_PORT=18108
        export GATEWAY_PORT=18101
        source '{SCRIPT}'
        [[ "$(service_port patent)" == "18110" ]]
        [[ "$(service_health_url fastQA)" == "http://127.0.0.1:18108/api/health" ]]
        [[ "$(service_health_url gateway)" == "http://127.0.0.1:18101/docs" ]]
        echo env-ports
        """
    )

    assert result.returncode == 0, result.stderr
    assert "env-ports" in result.stdout
