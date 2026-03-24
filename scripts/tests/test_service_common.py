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
