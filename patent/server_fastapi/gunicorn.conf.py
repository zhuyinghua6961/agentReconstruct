from config import get_settings


settings = get_settings()

bind = f"{settings.http.host}:{settings.http.port}"
worker_class = settings.gunicorn.worker_class
workers = settings.gunicorn.workers
threads = settings.gunicorn.threads
timeout = settings.gunicorn.timeout
keepalive = settings.gunicorn.keepalive
max_requests = settings.gunicorn.max_requests
max_requests_jitter = settings.gunicorn.max_requests_jitter
