from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class _UtcFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.utcfromtimestamp(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds") + "Z"


class _ContextDefaultsFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "trace_id"):
            record.trace_id = "-"
        if not hasattr(record, "route"):
            record.route = "-"
        return True


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    formatter = _UtcFormatter(
        "%(asctime)s %(levelname)s [pid=%(process)d] [%(name)s] [trace=%(trace_id)s] %(message)s"
    )
    context_filter = _ContextDefaultsFilter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(context_filter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stdout_handler)

    log_file = str(os.getenv("FASTQA_APP_LOG_FILE", "") or os.getenv("APP_LOG_FILE", "") or "").strip()
    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        root.addHandler(file_handler)

    root.setLevel(getattr(logging, str(level or "INFO").upper(), logging.INFO))
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def log_timing(logger: logging.Logger, action: str, *, level: int = logging.INFO):
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.log(level, "%s finished in %.2fms", action, elapsed_ms)
