import logging
from dataclasses import replace
from pathlib import Path

import config
import server_fastapi.app as app_module


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


def test_fastapi_app_persists_business_logs_to_runtime_file(tmp_path, monkeypatch):
    logs_dir = tmp_path / "runtime" / "dev" / "highThinkingQA" / "logs"
    settings = replace(
        config.HTTP_SETTINGS,
        runtime_logs_dir=str(logs_dir),
        app_log_level="INFO",
    )
    monkeypatch.setattr(app_module.config, "HTTP_SETTINGS", settings)

    names = ["", "server", "server_fastapi", "agent_core"]
    saved_state = {name: _logger_state(name) for name in names}

    try:
        app_module.create_app()
        logging.getLogger("server.services.ask_service").info("fastapi logging file probe")
        _flush_handlers(names)

        log_file = Path(logs_dir) / "highThinkingQA-app.log"
        assert log_file.exists()
        assert "fastapi logging file probe" in log_file.read_text(encoding="utf-8")
    finally:
        for name, state in saved_state.items():
            _restore_logger_state(name, state)
