"""Gunicorn config for the FastAPI service."""

from __future__ import annotations

import config as app_config

bind = f"{app_config.GUNICORN_BIND_HOST}:{app_config.GUNICORN_BIND_PORT}"
logger_class = "server_fastapi.logging.BeijingGunicornLogger"
access_log_format = (
    'event=gunicorn_access remote_addr=%(h)s method=%(m)s path=%(U)s query=%(q)s status=%(s)s '
    'bytes=%(B)s duration_us=%(D)s protocol=%(H)s trace_id=%({x-trace-id}i)s request_id=%({x-request-id}i)s '
    'referer="%(f)s" user_agent="%(a)s"'
)
worker_class = app_config.GUNICORN_WORKER_CLASS
workers = app_config.GUNICORN_WORKERS
threads = app_config.GUNICORN_THREADS
timeout = app_config.GUNICORN_TIMEOUT
keepalive = app_config.GUNICORN_KEEPALIVE
max_requests = app_config.GUNICORN_MAX_REQUESTS
max_requests_jitter = app_config.GUNICORN_MAX_REQUESTS_JITTER
