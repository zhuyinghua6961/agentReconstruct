from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/nginx/frontend-vue-gateway.nginx.conf.template"
BUILD_SCRIPT = ROOT / "scripts/build_frontend.sh"
START_SCRIPT = ROOT / "scripts/start_nginx_frontend.sh"
STOP_SCRIPT = ROOT / "scripts/stop_nginx_frontend.sh"
STATUS_SCRIPT = ROOT / "scripts/status_nginx_frontend.sh"
TEST_SCRIPT = ROOT / "scripts/test_nginx_frontend.sh"
SYNC_ALIYUN_SCRIPT = ROOT / "scripts/sync_frontend_to_aliyun.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_frontend_nginx_scripts_and_template_exist() -> None:
    expected = [
        TEMPLATE,
        BUILD_SCRIPT,
        START_SCRIPT,
        STOP_SCRIPT,
        STATUS_SCRIPT,
        TEST_SCRIPT,
        SYNC_ALIYUN_SCRIPT,
    ]
    missing = [str(path) for path in expected if not path.exists()]
    assert not missing, f"missing expected deploy assets: {missing}"


def test_frontend_nginx_template_preserves_spa_and_streaming_requirements() -> None:
    content = _read(TEMPLATE)
    assert "try_files $uri $uri/ /index.html;" in content
    assert "location /api/" in content
    assert "location /health" in content
    assert "client_max_body_size 128m;" in content
    assert "proxy_buffering off;" in content
    assert "proxy_request_buffering off;" in content
    assert "gzip off;" in content
    assert "proxy_http_version 1.1;" in content
    assert "proxy_read_timeout 3600s;" in content
    assert "proxy_send_timeout 3600s;" in content
    assert "add_header X-Accel-Buffering no always;" in content


def test_frontend_nginx_start_script_uses_user_space_runtime_and_nginx_t() -> None:
    content = _read(START_SCRIPT)
    assert "FRONTEND_NGINX_PORT" in content
    assert "GATEWAY_UPSTREAM_URL" in content
    assert "FRONTEND_DIST_DIR" in content
    assert "NGINX_RUNTIME_ROOT" in content
    assert "NGINX_LOG_ROOT" in content
    assert "NGINX_BIN" in content
    assert "resource/runtime/dev/frontend-nginx" in content
    assert "resource/logs/dev/frontend-nginx" in content
    assert "BOOTSTRAP_ERROR_LOG" in content
    assert "error_log $BOOTSTRAP_ERROR_LOG notice;" in content
    assert " -t " in content
    assert "-p \"$NGINX_RUNTIME_ROOT\"" in content


def test_frontend_nginx_status_and_stop_scripts_use_runtime_metadata() -> None:
    status_content = _read(STATUS_SCRIPT)
    stop_content = _read(STOP_SCRIPT)
    for content in [status_content, stop_content]:
        assert "NGINX_RUNTIME_ROOT" in content
    assert "frontend nginx running" in status_content.lower()
    assert "frontend nginx not running" in status_content.lower()
    assert "nginx.pid" in stop_content


def test_frontend_nginx_test_script_checks_static_proxy_stream_and_task_recovery() -> None:
    content = _read(TEST_SCRIPT)
    assert "/health" in content
    assert "nonexistent-route" in content
    assert "text/event-stream" in content
    assert "after_seq" in content
    assert "AUTH_BEARER_TOKEN" in content
    assert "REDIS" in content


def test_frontend_sync_to_aliyun_script_syncs_source_builds_remotely_and_switches_dist_atomically() -> None:
    content = _read(SYNC_ALIYUN_SCRIPT)
    assert "SSH_KEY_PATH" in content
    assert "REMOTE_USER_HOST" in content
    assert "REMOTE_HOME" in content
    assert "REMOTE_SOURCE_DIR" in content
    assert "REMOTE_NGINX_ROOT" in content
    assert 'printf %s "$HOME"' in content
    assert "normalize_remote_path" in content
    assert "rsync" in content
    assert "--delete" in content
    assert "package-lock.json" in content
    assert "npm ci --no-audit --no-fund" in content
    assert "npm run build" in content
    assert "releases" in content
    assert "ln -sfn" in content or "mv -Tf" in content
    assert "/health" in content
