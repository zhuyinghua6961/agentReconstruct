"""Gunicorn config for the FastAPI service."""

from __future__ import annotations

import config as app_config

bind = f"{app_config.GUNICORN_BIND_HOST}:{app_config.GUNICORN_BIND_PORT}"
worker_class = app_config.GUNICORN_WORKER_CLASS
workers = app_config.GUNICORN_WORKERS
threads = app_config.GUNICORN_THREADS
timeout = app_config.GUNICORN_TIMEOUT
keepalive = app_config.GUNICORN_KEEPALIVE
max_requests = app_config.GUNICORN_MAX_REQUESTS
max_requests_jitter = app_config.GUNICORN_MAX_REQUESTS_JITTER
