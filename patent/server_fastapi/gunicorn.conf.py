from config import get_settings


settings = get_settings()

bind = f"{settings.http.host}:{settings.http.port}"
logger_class = "server_fastapi.logging.BeijingGunicornLogger"
access_log_format = (
    'event=gunicorn_access remote_addr=%(h)s method=%(m)s path=%(U)s query=%(q)s status=%(s)s '
    'bytes=%(B)s duration_us=%(D)s protocol=%(H)s trace_id=%({x-trace-id}i)s request_id=%({x-request-id}i)s '
    'referer="%(f)s" user_agent="%(a)s"'
)
worker_class = settings.gunicorn.worker_class
workers = settings.gunicorn.workers
threads = settings.gunicorn.threads
timeout = settings.gunicorn.timeout
keepalive = settings.gunicorn.keepalive
max_requests = settings.gunicorn.max_requests
max_requests_jitter = settings.gunicorn.max_requests_jitter
