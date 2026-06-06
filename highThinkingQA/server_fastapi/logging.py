from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import WatchedFileHandler
from pathlib import Path

from server.runtime.request_context import get_trace_id

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

_STDOUT_HANDLER_NAME = "highThinkingQA.app.stdout"
_FILE_HANDLER_NAME = "highThinkingQA.app.file"


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
            record.trace_id = get_trace_id()
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


def _resolve_log_file(log_file: str | os.PathLike[str] | None) -> Path | None:
    raw = str(log_file or os.getenv("HIGHTHINKINGQA_APP_LOG_FILE") or os.getenv("APP_LOG_FILE") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def configure_logging(level: str = "INFO", *, log_file: str | os.PathLike[str] | None = None) -> logging.Logger:
    log_level = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    formatter = BeijingFormatter(APP_LOG_FORMAT)
    context_filter = _ContextDefaultsFilter()

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", "") not in {_STDOUT_HANDLER_NAME, _FILE_HANDLER_NAME}:
            continue
        root.removeHandler(handler)
        handler.close()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.set_name(_STDOUT_HANDLER_NAME)
    stdout_handler.setLevel(log_level)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(context_filter)
    root.addHandler(stdout_handler)

    log_path = _resolve_log_file(log_file)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = WatchedFileHandler(log_path, encoding="utf-8")
        file_handler.set_name(_FILE_HANDLER_NAME)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        root.addHandler(file_handler)

    root.setLevel(log_level)
    return logging.getLogger("server_fastapi")
