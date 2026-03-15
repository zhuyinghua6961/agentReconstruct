"""FastAPI app factory for highThinking HTTP service."""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import config
from server.runtime.request_context import clear_trace_id, generate_trace_id, get_trace_id, set_trace_id
from server_fastapi.errors import register_exception_handlers
from server_fastapi.routers import register_routers


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    settings = config.HTTP_SETTINGS
    app.logger = logging.getLogger("server_fastapi")
    app.logger.setLevel(getattr(logging, settings.app_log_level, logging.INFO))

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
    app.state.ask_slots = threading.BoundedSemaphore(
        value=int(app.state.config["ASK_STREAM_MAX_CONCURRENT"])
    )

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
