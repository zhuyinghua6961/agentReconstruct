"""FastAPI app factory for highThinking HTTP service."""

from __future__ import annotations

import logging
import threading
from logging.handlers import WatchedFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
from server.runtime.request_context import clear_trace_id, generate_trace_id, get_trace_id, set_trace_id
from server.services.redis_client import bootstrap_redis_state
from server_fastapi.errors import register_exception_handlers
from server_fastapi.routers import register_routers


_APP_LOG_FILE_NAME = "highThinkingQA-app.log"
_APP_LOG_HANDLER_NAME = "highThinkingQA.app.file"
_APP_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _configure_application_logging(settings: config.HttpServiceSettings) -> logging.Logger:
    log_level = getattr(logging, settings.app_log_level, logging.INFO)
    log_dir = Path(settings.runtime_logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / _APP_LOG_FILE_NAME

    root_logger = logging.getLogger()
    current_root_level = root_logger.level if root_logger.level != logging.NOTSET else log_level
    root_logger.setLevel(min(current_root_level, log_level))

    file_handler = None
    for handler in list(root_logger.handlers):
        if getattr(handler, "name", "") != _APP_LOG_HANDLER_NAME:
            continue
        if Path(getattr(handler, "baseFilename", "")) == log_path:
            file_handler = handler
            break
        root_logger.removeHandler(handler)
        handler.close()

    if file_handler is None:
        file_handler = WatchedFileHandler(log_path, encoding="utf-8")
        file_handler.set_name(_APP_LOG_HANDLER_NAME)
        root_logger.addHandler(file_handler)

    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(_APP_LOG_FORMAT))

    for logger_name in ("server", "server_fastapi", "agent_core", "retriever"):
        package_logger = logging.getLogger(logger_name)
        current_level = package_logger.level if package_logger.level != logging.NOTSET else log_level
        package_logger.setLevel(min(current_level, log_level))
        package_logger.propagate = True

    return logging.getLogger("server_fastapi")


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    settings = config.HTTP_SETTINGS
    app.logger = _configure_application_logging(settings)

    app.state.config = {
        "APP_ENV": settings.app_env,
        "APP_HOST": settings.app_host,
        "APP_PORT": settings.app_port,
        "UPLOAD_DIR": settings.upload_dir,
        "ASK_STREAM_MAX_CONCURRENT": settings.ask_stream_max_concurrent,
        "ASK_EXECUTOR_MAX_WORKERS": settings.ask_executor_max_workers,
        "ASK_TIMEOUT_SECONDS": settings.ask_timeout_seconds,
        "SSE_HEARTBEAT_SECONDS": settings.sse_heartbeat_seconds,
        "CHAT_PERSIST_ENABLED": settings.chat_persist_enabled,
        "CHAT_PERSIST_ASYNC": settings.chat_persist_async,
        "CHAT_PERSIST_ASYNC_WORKERS": settings.chat_persist_async_workers,
        "ENABLE_CORS": settings.enable_cors,
        "CORS_ORIGINS": settings.cors_origins,
    }
    app.state.component_status = {}
    app.state.redis_bindings = None
    app.state.redis_service = None
    app.state.ask_slots = threading.BoundedSemaphore(
        value=int(app.state.config["ASK_STREAM_MAX_CONCURRENT"])
    )

    bootstrap_redis_state(app.state)

    if app.state.config["ENABLE_CORS"]:
        origins = app.state.config["CORS_ORIGINS"]
        allow_origins = ["*"] if origins == "*" else [item.strip() for item in origins.split(",") if item.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins or ["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _trace_context_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID") or request.headers.get("X-Trace-ID")
        set_trace_id(str(incoming).strip() if incoming else generate_trace_id())
        try:
            response = await call_next(request)
        finally:
            current_trace_id = get_trace_id()
            clear_trace_id()
        response.headers["X-Trace-ID"] = current_trace_id
        return response

    register_exception_handlers(app)
    register_routers(app)

    @app.get("/")
    async def _index():
        return JSONResponse(
            content={
                "service": "highThinking-api",
                "version": "v1",
                "endpoints": [
                    "/api/v1/health",
                    "/api/v1/ask",
                    "/api/v1/ask_stream",
                ],
            }
        )

    return app
