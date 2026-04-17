from __future__ import annotations

import logging
from pathlib import Path

from app.core.logging import APP_LOG_FORMAT, BeijingFormatter, GUNICORN_ACCESS_LOG_FORMAT


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_public_service_beijing_formatter_formats_epoch_with_offset():
    formatter = BeijingFormatter("%(asctime)s")
    record = logging.LogRecord("public.test", logging.INFO, __file__, 1, "message", (), None)
    record.created = 0

    assert formatter.formatTime(record) == "1970-01-01T08:00:00.000+08:00"


def test_public_service_app_log_format_includes_pid_and_trace_fields():
    assert APP_LOG_FORMAT == "%(asctime)s %(levelname)s [pid=%(process)d] [%(name)s] [trace=%(trace_id)s] %(message)s"


def test_public_service_gunicorn_access_log_format_is_structured():
    assert "event=gunicorn_access" in GUNICORN_ACCESS_LOG_FORMAT
    assert "trace_id=%({x-trace-id}i)s" in GUNICORN_ACCESS_LOG_FORMAT
    assert "duration_us=%(D)s" in GUNICORN_ACCESS_LOG_FORMAT


def test_public_service_start_script_uses_gunicorn_config_file():
    script = (ROOT_DIR / "scripts" / "start_gunicorn.sh").read_text(encoding="utf-8")

    assert "gunicorn.conf.py" in script
