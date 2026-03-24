from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

START_SCRIPTS = [
    ROOT / "fastQA/scripts/start_gunicorn.sh",
    ROOT / "gateway/scripts/start_gunicorn.sh",
    ROOT / "public-service/scripts/start_gunicorn.sh",
    ROOT / "highThinkingQA/scripts/start_fastapi_gunicorn.sh",
]

LOG_PATH_SCRIPTS = {
    ROOT / "fastQA/scripts/start_gunicorn.sh": "$RESOURCE_DIR/logs/dev/fastQA",
    ROOT / "fastQA/scripts/status_gunicorn.sh": "$RESOURCE_DIR/logs/dev/fastQA",
    ROOT / "gateway/scripts/start_gunicorn.sh": "$RESOURCE_DIR/logs/dev/gateway",
    ROOT / "gateway/scripts/status_gunicorn.sh": "$RESOURCE_DIR/logs/dev/gateway",
    ROOT / "public-service/scripts/start_gunicorn.sh": "$RESOURCE_DIR/logs/dev/public-service",
    ROOT / "public-service/scripts/status_gunicorn.sh": "$RESOURCE_DIR/logs/dev/public-service",
    ROOT / "highThinkingQA/scripts/start_fastapi_gunicorn.sh": "$RESOURCE_DIR/logs/dev/highThinkingQA",
    ROOT / "highThinkingQA/scripts/status_fastapi_gunicorn.sh": "$RESOURCE_DIR/logs/dev/highThinkingQA",
}


def test_start_scripts_use_nohup_instead_of_daemon_mode() -> None:
    for script in START_SCRIPTS:
        content = script.read_text(encoding="utf-8")
        assert "nohup" in content, f"{script} should use nohup"
        assert "--pid" in content, f"{script} should still write a gunicorn pid file"
        assert "--daemon" not in content, f"{script} should not use gunicorn daemon mode"


def test_service_logs_live_under_resource_logs() -> None:
    for script, expected_fragment in LOG_PATH_SCRIPTS.items():
        content = script.read_text(encoding="utf-8")
        assert expected_fragment in content, f"{script} should write logs under {expected_fragment}"
