from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from gunicorn.glogging import Logger as _GunicornLogger
except Exception:  # pragma: no cover - optional runtime dependency
    _GunicornLogger = None


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
APP_LOG_FORMAT = "%(asctime)s %(levelname)s [pid=%(process)d] [%(name)s] [trace=%(trace_id)s] %(message)s"
GUNICORN_ERROR_LOG_FORMAT = "%(asctime)s %(levelname)s [pid=%(process)d] [gunicorn.error] %(message)s"
GUNICORN_ACCESS_RECORD_FORMAT = "%(asctime)s %(levelname)s [pid=%(process)d] [gunicorn.access] %(message)s"
GUNICORN_ACCESS_LOG_FORMAT = (
    'event=gunicorn_access remote_addr=%(h)s method=%(m)s path=%(U)s query=%(q)s status=%(s)s '
    'bytes=%(B)s duration_us=%(D)s protocol=%(H)s trace_id=%({x-trace-id}i)s request_id=%({x-request-id}i)s '
    'referer="%(f)s" user_agent="%(a)s"'
)


def beijing_now_iso(*, timespec: str = "milliseconds") -> str:
    return datetime.fromtimestamp(datetime.now(tz=BEIJING_TIMEZONE).timestamp(), tz=BEIJING_TIMEZONE).isoformat(timespec=timespec)


class BeijingFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.fromtimestamp(record.created, tz=BEIJING_TIMEZONE)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds")


class _ContextDefaultsFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "trace_id"):
            record.trace_id = "-"
        if not hasattr(record, "route"):
            record.route = "-"
        return True


class BeijingGunicornLogger(_GunicornLogger if _GunicornLogger is not None else object):
    def setup(self, cfg):  # pragma: no cover - exercised via live gunicorn runtime
        super().setup(cfg)
        error_formatter = BeijingFormatter(GUNICORN_ERROR_LOG_FORMAT)
        access_formatter = BeijingFormatter(GUNICORN_ACCESS_RECORD_FORMAT)
        for handler in self.error_log.handlers:
            handler.setFormatter(error_formatter)
        for handler in self.access_log.handlers:
            handler.setFormatter(access_formatter)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    formatter = BeijingFormatter(APP_LOG_FORMAT)
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
