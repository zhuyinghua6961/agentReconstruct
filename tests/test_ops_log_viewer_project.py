from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPS_ROOT = ROOT / "ops-log-viewer"


EXPECTED_LOGS = {
    "fastqa-app": "resource/logs/dev/fastQA/fastqa-app.log",
    "gateway-access": "resource/logs/dev/gateway/gateway-access.log",
    "gateway-admission-worker-startup": "resource/logs/dev/gateway/gateway-admission-worker-startup.log",
    "highthinkingqa-app": "resource/logs/dev/highThinkingQA/highThinkingQA-app.log",
    "patent-app": "resource/logs/dev/patent/patent-app.log",
    "public-service-access": "resource/logs/dev/public-service/public-service-access.log",
}


def test_ops_log_viewer_manifest_lists_required_logs():
    manifest = (OPS_ROOT / "local-pusher" / "logs.manifest").read_text(encoding="utf-8")

    rows = [
        tuple(part.strip() for part in line.split("|"))
        for line in manifest.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(rows) == len(EXPECTED_LOGS)
    assert {row[0] for row in rows} == set(EXPECTED_LOGS)
    for key, source_path in EXPECTED_LOGS.items():
        matching = [row for row in rows if row[0] == key]
        assert matching == [(key, source_path, f"{key}.log")]


def test_local_pusher_uses_tail_follow_and_remote_append_over_ssh():
    script = (OPS_ROOT / "local-pusher" / "push_logs.sh").read_text(encoding="utf-8")

    assert "REMOTE_USER_HOST=" in script
    assert "SSH_KEY_PATH=" in script
    assert "REMOTE_ROOT=" in script
    assert "MAX_REMOTE_LOG_BYTES=" in script
    assert "MAX_LOCAL_PUSHER_LOG_BYTES=" in script
    assert "tail -n 0 -F" in script
    assert "mkdir -p '$REMOTE_ROOT/data'" in script
    assert "touch '$REMOTE_ROOT/data/$remote_name'" in script
    assert "cat >> '$REMOTE_ROOT/data/$remote_name'" in script
    assert "trim_remote_log_if_needed" in script
    assert "trim_local_pusher_log_if_needed" in script
    assert "trap cleanup INT TERM EXIT" in script


def test_nginx_template_protects_page_and_logs_with_basic_auth():
    template = (OPS_ROOT / "nginx" / "ops-log-viewer.nginx.conf").read_text(encoding="utf-8")

    assert "listen 18088" in template
    assert "auth_basic" in template
    assert "auth_basic_user_file" in template
    assert "access_log off" in template
    assert "alias /home/qdbot/ops-log-viewer/data/" in template
    assert 'add_header Cache-Control "no-store" always' in template
    assert 'add_header X-Frame-Options "DENY" always' in template
    assert "limit_except GET" in template
    assert "try_files $uri =404" in template


def test_static_viewer_polls_log_files_and_handles_truncation():
    html = (OPS_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (OPS_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    for key in EXPECTED_LOGS:
        assert f"/data/{key}.log" in app

    assert "setInterval" in app
    assert "Range" in app
    assert "MAX_RENDERED_CHARS" in app
    assert "trimRenderedOutput" in app
    assert "fileSize < state.offset" in app
    assert "paused" in app
    assert "autoScroll" in app
    assert "Clear" in html


def test_static_viewer_keeps_sidebar_fixed_while_log_output_scrolls():
    css = (OPS_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert "overflow: hidden;" in css
    assert "height: 100dvh;" in css
    assert ".sidebar" in css
    assert "height: 100dvh;" in css
    assert "overflow: auto;" in css
    assert ".viewer" in css
    assert "min-height: 0;" in css
    assert "overflow: hidden;" in css
    assert ".log-output" in css
    assert "min-height: 0;" in css


def test_readme_documents_ram_access_keys_and_retention_caps():
    readme = (OPS_ROOT / "README.md").read_text(encoding="utf-8")

    assert "No Alibaba Cloud RAM AccessKey" in readme
    assert "Basic Auth over plain HTTP" in readme
    assert "MAX_REMOTE_LOG_BYTES" in readme
    assert "MAX_RENDERED_CHARS" in readme


def test_local_preview_nginx_script_serves_static_page_and_real_logs_on_9094():
    script = (OPS_ROOT / "nginx" / "start_local_preview.sh").read_text(encoding="utf-8")

    assert "OPS_LOG_VIEWER_LOCAL_PORT:-9094" in script
    assert "OPS_LOG_VIEWER_LOCAL_HOST:-127.0.0.1" in script
    assert "location = /data/%s" in script
    assert "alias %s" in script
    assert "nginx started" in script
