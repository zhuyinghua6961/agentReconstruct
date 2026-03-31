from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_RUNTIME_DIR = REPO_ROOT / "gateway" / ".runtime"
GATEWAY_PID_FILE = GATEWAY_RUNTIME_DIR / "gateway-admission-worker.pid"


def _run_bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=str(REPO_ROOT),
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_gateway_admission_worker_enabled_loads_gateway_env_files(tmp_path):
    env_file = tmp_path / "gateway.env"
    env_file.write_text("GATEWAY_ADMISSION_WORKER_ENABLED=1\n", encoding="utf-8")

    completed = _run_bash(
        (
            "source scripts/_service_common.sh; "
            "if gateway_admission_worker_enabled; then echo enabled; else echo disabled; fi"
        ),
        env={"GATEWAY_ENV_FILES": str(env_file)},
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "enabled"


def test_gateway_admission_worker_enabled_preserves_explicit_env_over_env_file(tmp_path):
    env_file = tmp_path / "gateway.env"
    env_file.write_text("GATEWAY_ADMISSION_WORKER_ENABLED=1\n", encoding="utf-8")

    completed = _run_bash(
        (
            "source scripts/_service_common.sh; "
            "if gateway_admission_worker_enabled; then echo enabled; else echo disabled; fi"
        ),
        env={
            "GATEWAY_ENV_FILES": str(env_file),
            "GATEWAY_ADMISSION_WORKER_ENABLED": "0",
        },
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "disabled"


def test_wait_for_pid_state_requires_stable_running_process(tmp_path):
    pid_file = tmp_path / "worker.pid"
    completed = _run_bash(
        (
            f"pid_file='{pid_file}'; "
            "(sleep 0.2) & pid=$!; "
            "echo \"$pid\" > \"$pid_file\"; "
            "source scripts/_service_common.sh; "
            "if wait_for_pid_state \"$pid_file\" 1 3 2; then echo stable; else echo unstable; fi"
        )
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "unstable"


def test_start_admission_worker_fails_when_worker_exits_during_bootstrap(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_conda = fake_bin / "conda"
    fake_conda.write_text("#!/usr/bin/env bash\nsleep 0.2\nexit 3\n", encoding="utf-8")
    fake_conda.chmod(0o755)
    env_file = tmp_path / "gateway.env"
    env_file.write_text("", encoding="utf-8")

    GATEWAY_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if GATEWAY_PID_FILE.exists():
        GATEWAY_PID_FILE.unlink()

    completed = _run_bash(
        (
            "bash gateway/scripts/start_admission_worker.sh"
        ),
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GATEWAY_ENV_FILES": str(env_file),
        },
    )

    assert completed.returncode != 0
    assert "failed to start" in completed.stdout
    assert not GATEWAY_PID_FILE.exists()


def test_run_admission_worker_foreground_preserves_explicit_runtime_role_over_env_file(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_conda = fake_bin / "conda"
    fake_conda.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$GATEWAY_RUNTIME_ROLE\"\n", encoding="utf-8")
    fake_conda.chmod(0o755)
    env_file = tmp_path / "gateway.env"
    env_file.write_text("GATEWAY_RUNTIME_ROLE=web\n", encoding="utf-8")

    completed = _run_bash(
        "bash gateway/scripts/run_admission_worker_foreground.sh",
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GATEWAY_ENV_FILES": str(env_file),
            "GATEWAY_RUNTIME_ROLE": "admission_worker",
        },
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "admission_worker"


def test_run_service_script_gateway_start_preserves_explicit_env_values():
    completed = _run_bash(
        (
            "bash() { printf '%s|%s|%s|%s\\n' "
            "\"$GATEWAY_PORT\" "
            "\"$PUBLIC_BACKEND_BASE_URL\" "
            "\"$FAST_BACKEND_BASE_URL\" "
            "\"$THINKING_BACKEND_BASE_URL\"; }\n"
            "source scripts/_service_common.sh\n"
            "run_service_script gateway start"
        ),
        env={
            "GATEWAY_PORT": "9101",
            "PUBLIC_BACKEND_BASE_URL": "http://public.test",
            "FAST_BACKEND_BASE_URL": "http://fast.test",
            "THINKING_BACKEND_BASE_URL": "http://thinking.test",
        },
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "9101|http://public.test|http://fast.test|http://thinking.test"
