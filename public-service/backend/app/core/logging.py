from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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


def configure_logging(*, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    formatter = BeijingFormatter(APP_LOG_FORMAT)
    context_filter = _ContextDefaultsFilter()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
