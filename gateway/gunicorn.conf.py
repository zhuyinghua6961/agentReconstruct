logger_class = "app.core.logging.BeijingGunicornLogger"
access_log_format = (
    'event=gunicorn_access remote_addr=%(h)s method=%(m)s path=%(U)s query=%(q)s status=%(s)s '
    'bytes=%(B)s duration_us=%(D)s protocol=%(H)s trace_id=%({x-trace-id}i)s request_id=%({x-request-id}i)s '
    'referer="%(f)s" user_agent="%(a)s"'
)
