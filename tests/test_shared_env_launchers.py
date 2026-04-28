from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _run_bash(script: str, *, env: dict[str, str] | None = None) -> str:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT_DIR,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def test_shared_env_loader_preserves_process_env_and_allows_service_override(tmp_path):
    shared = tmp_path / "shared.env"
    service = tmp_path / "service.env"
    shared.write_text("REDIS_HOST=shared-value\nSCRIPT_DEFAULT=shared-value\n", encoding="utf-8")
    service.write_text("REDIS_HOST=service-value\nSCRIPT_DEFAULT=service-value\n", encoding="utf-8")

    script = f"""
source scripts/env_file_loader.sh
capture_env_file_loader_process_keys
export SCRIPT_DEFAULT=script-default
load_env_files_preserving_process_env "{shared}:{service}"
printf '%s|%s\\n' "$REDIS_HOST" "$SCRIPT_DEFAULT"
"""

    assert _run_bash(script) == "service-value|service-value"
    assert _run_bash(script, env={"REDIS_HOST": "process-value"}) == "process-value|service-value"


def test_gateway_service_common_env_files_include_shared_before_service_files():
    output = _run_bash("source scripts/_service_common.sh; gateway_env_files")
    files = output.split(":")

    assert files[:6] == [
        str(ROOT_DIR / "resource/config/shared/infrastructure.shared.env"),
        str(ROOT_DIR / "resource/config/shared/model-endpoints.shared.env"),
        str(ROOT_DIR / "resource/config/shared/infrastructure.secret.env"),
        str(ROOT_DIR / "resource/config/services/gateway/config.env"),
        str(ROOT_DIR / "resource/config/services/gateway/config.shared.env"),
        str(ROOT_DIR / "resource/config/services/gateway/config.secret.env"),
    ]


def test_public_service_start_all_branch_includes_shared_before_legacy_files():
    content = (ROOT_DIR / "scripts/_service_common.sh").read_text(encoding="utf-8")

    shared = (
        "$RESOURCE_DIR/config/shared/infrastructure.shared.env:"
        "$RESOURCE_DIR/config/shared/model-endpoints.shared.env:"
        "$RESOURCE_DIR/config/shared/infrastructure.secret.env:"
    )
    legacy = "$ROOT_DIR/public-service/config.shared.env:$ROOT_DIR/public-service/config.secret.env"
    assert f'PUBLIC_SERVICE_ENV_FILES="${{PUBLIC_SERVICE_ENV_FILES:-{shared}{legacy}}}"' in content


def test_public_service_and_patent_launchers_default_to_shared_first():
    public_script = (ROOT_DIR / "public-service/scripts/start_gunicorn.sh").read_text(encoding="utf-8")
    patent_script = (ROOT_DIR / "patent/scripts/start_gunicorn.sh").read_text(encoding="utf-8")

    assert (
        "$RESOURCE_DIR/config/shared/infrastructure.shared.env:"
        "$RESOURCE_DIR/config/shared/model-endpoints.shared.env:"
        "$RESOURCE_DIR/config/shared/infrastructure.secret.env"
    ) in public_script
    assert (
        "$RESOURCE_DIR/config/shared/infrastructure.shared.env:"
        "$RESOURCE_DIR/config/shared/model-endpoints.shared.env:"
        "$RESOURCE_DIR/config/shared/infrastructure.secret.env"
    ) in patent_script
    assert "$PATENT_SHARED_ENV_FILES:$CONFIG_DIR_DEFAULT/config.env" in patent_script


def test_launchers_do_not_build_root_shared_paths_when_resource_dir_is_absent():
    launchers = [
        ROOT_DIR / "fastQA/scripts/start_gunicorn.sh",
        ROOT_DIR / "gateway/scripts/start_gunicorn.sh",
        ROOT_DIR / "gateway/scripts/start_admission_worker.sh",
        ROOT_DIR / "gateway/scripts/run_gunicorn_foreground.sh",
        ROOT_DIR / "gateway/scripts/run_admission_worker_foreground.sh",
    ]

    for launcher in launchers:
        content = launcher.read_text(encoding="utf-8")
        assert 'SHARED_ENV_FILES="${' in content
        assert "SHARED_ENV_FILES_DEFAULT=\"\"" in content
        assert "SHARED_ENV_FILES_DEFAULT=\"$SHARED_CONFIG_DIR_DEFAULT/infrastructure.shared.env:" in content
        assert "SHARED_ENV_FILES:-$SHARED_CONFIG_DIR_DEFAULT/infrastructure.shared.env" not in content
