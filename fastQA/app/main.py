from __future__ import annotations

import os
from functools import partial
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.runtime import bootstrap_generation_runtime, bootstrap_redis, close_generation_runtime, close_redis
from app.modules.documents.api import router as documents_router
from app.routers.health import router as health_router
from app.routers.qa import router as qa_router
from app.services.chat_persistence import persist_assistant_summary, persist_user_message
from app.services.limits import AskConcurrencyLimiter


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        yield
    finally:
        close = getattr(getattr(app.state, "shared_llm_adapter", None), "close", None)
        if callable(close):
            close()
        close_generation_runtime(app.state)
        close_redis(app.state)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(str(os.getenv("LOG_LEVEL", "INFO") or "INFO"))
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        docs_url=settings.docs_url,
        openapi_url=settings.openapi_url,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.logger = get_logger(settings.app_name)
    app.state.logger = app.logger
    app.state.ask_limiter = AskConcurrencyLimiter(max_concurrent=settings.ask_stream_max_concurrent)
    app.state.component_status = {}
    app.state.health_flags = {}
    app.state.redis_bindings = None
    app.state.redis_client = None
    app.state.redis_service = None
    app.state.generation_runtime = None
    app.state.generation_runtime_ready = False
    app.state.shared_llm_adapter = None
    app.state.shared_llm_adapter_ready = False
    app.state.pdf_web_bindings = None
    app.state.persist_user_message_hook = partial(persist_user_message, async_enabled=settings.chat_persist_async) if settings.chat_persist_enabled else None
    app.state.persist_assistant_summary_hook = partial(persist_assistant_summary, async_enabled=settings.chat_persist_async) if settings.chat_persist_enabled else None
    bootstrap_redis(app.state)
    bootstrap_generation_runtime(app.state)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(qa_router)
    return app


app = create_app()
