from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "scripts/check_aliyun_gateway_tunnel.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tunnel_watchdog_script_exists() -> None:
    assert CHECK_SCRIPT.exists(), f"missing watchdog script: {CHECK_SCRIPT}"


def test_tunnel_watchdog_script_is_one_shot_and_uses_locking() -> None:
    content = _read(CHECK_SCRIPT)
    assert "flock" in content
    assert "LOCK_FILE" in content
    assert "while true" not in content
    assert "sleep infinity" not in content


def test_tunnel_watchdog_script_uses_bounded_probes_and_retries() -> None:
    content = _read(CHECK_SCRIPT)
    assert "PROBE_RETRIES" in content
    assert "for attempt in $(seq 1 \"$PROBE_RETRIES\")" in content
    assert "--connect-timeout" in content
    assert "--max-time" in content
    assert "ConnectTimeout=" in content


def test_tunnel_watchdog_script_manages_autossh_without_unbounded_process_growth() -> None:
    content = _read(CHECK_SCRIPT)
    assert "AUTOSSH_PID_FILE" in content
    assert "pgrep -af" in content
    assert "kill_stale_tunnel_processes" in content
    assert "nohup" in content
    assert "AUTOSSH_GATETIME=0" in content
    assert "-R 127.0.0.1:18101:127.0.0.1:8101" in content
