import io
import logging
import sys
from dataclasses import replace
from pathlib import Path

import config
import server_fastapi.app as app_module
from server_fastapi.logging import APP_LOG_FORMAT, BeijingFormatter, GUNICORN_ACCESS_LOG_FORMAT


def _logger_state(name: str) -> tuple[int, bool, list[logging.Handler]]:
    logger = logging.getLogger(name)
    return logger.level, logger.propagate, list(logger.handlers)


def _restore_logger_state(name: str, state: tuple[int, bool, list[logging.Handler]]) -> None:
    level, propagate, handlers = state
    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        if handler not in handlers:
            handler.close()
    logger.setLevel(level)
    logger.propagate = propagate
    for handler in handlers:
        if handler not in logger.handlers:
            logger.addHandler(handler)


def _flush_handlers(names: list[str]) -> None:
    for name in names:
        for handler in logging.getLogger(name).handlers:
            handler.flush()


def test_highthinking_beijing_formatter_formats_epoch_with_offset():
    formatter = BeijingFormatter("%(asctime)s")
    record = logging.LogRecord("highthinking.test", logging.INFO, __file__, 1, "message", (), None)
    record.created = 0

    assert formatter.formatTime(record) == "1970-01-01T08:00:00.000+08:00"


def test_highthinking_app_log_format_includes_pid_and_trace_fields():
    assert APP_LOG_FORMAT == "%(asctime)s %(levelname)s [pid=%(process)d] [%(name)s] [trace=%(trace_id)s] %(message)s"


def test_highthinking_gunicorn_access_log_format_is_structured():
    assert "event=gunicorn_access" in GUNICORN_ACCESS_LOG_FORMAT
    assert "trace_id=%({x-trace-id}i)s" in GUNICORN_ACCESS_LOG_FORMAT
    assert "duration_us=%(D)s" in GUNICORN_ACCESS_LOG_FORMAT


def test_fastapi_app_streams_business_logs_to_stdout_and_runtime_file(tmp_path, monkeypatch):
    logs_dir = tmp_path / "runtime" / "dev" / "highThinkingQA" / "logs"
    settings = replace(
        config.HTTP_SETTINGS,
        runtime_logs_dir=str(logs_dir),
        app_log_level="INFO",
    )
    monkeypatch.setattr(app_module.config, "HTTP_SETTINGS", settings)
    stdout_buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_buffer)

    names = ["", "server", "server_fastapi", "agent_core"]
    saved_state = {name: _logger_state(name) for name in names}

    try:
        app_module.create_app()
        logging.getLogger("server.services.ask_service").info("fastapi logging file probe")
        _flush_handlers(names)

        log_file = Path(logs_dir) / "highThinkingQA-app.log"
        assert log_file.exists()
        assert "fastapi logging file probe" in log_file.read_text(encoding="utf-8")
        assert "fastapi logging file probe" in stdout_buffer.getvalue()
        assert "[trace=req_unknown]" in stdout_buffer.getvalue()
    finally:
        for name, state in saved_state.items():
            _restore_logger_state(name, state)
