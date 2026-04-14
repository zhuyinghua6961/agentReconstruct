import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import get_settings
from server.patent.executor import PatentExecutor
from server.patent.original_service import OriginalViewService
from server.patent.runtime import build_default_patent_runtime
from server.runtime.ordered_task_dispatcher import OrderedTaskDispatcher
from server.runtime.request_context import clear_trace_id, generate_trace_id, get_trace_id, set_trace_id
from server.services.ask_service import AskService
from server.services.chat_persistence import ChatPersistenceService
from server.services.conversation_authority_client import ConversationAuthorityClient
from server.services.execution_cache import ExecutionCache
from server.services.execution_lock import ExecutionLockManager
from server.services.redis_client import bootstrap_redis_state
from server_fastapi.errors import register_exception_handlers
from server_fastapi.logging import configure_logging
from server_fastapi.routers import register_routers


class TraceContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming = headers.get("X-Request-ID") or headers.get("X-Trace-ID")
        token = set_trace_id(str(incoming).strip() if incoming else generate_trace_id())

        async def send_wrapper(message: Message) -> None:
            if message.get("type") == "http.response.start":
                mutable_headers = MutableHeaders(scope=message)
                mutable_headers["X-Trace-ID"] = get_trace_id()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            clear_trace_id(token)



def _bootstrap_authority_state(app: FastAPI) -> None:
    settings = app.state.settings
    authority_enabled = bool(settings.authority.durable_enabled)
    token_configured = bool(str(settings.authority.internal_token or "").strip())
    base_url = str(settings.authority.base_url or "").strip()
    ready = bool(authority_enabled and token_configured and base_url)
    app.state.authority_client = ConversationAuthorityClient() if ready else None
    component_status = dict(getattr(app.state, "component_status", {}) or {})
    component_status["authority"] = {
        "ready": ready,
        "enabled": authority_enabled,
        "base_url": base_url,
        "token_configured": token_configured,
    }
    app.state.component_status = component_status



def _bootstrap_service_state(app: FastAPI) -> None:
    key_factory = app.state.redis_key_factory
    redis_client = getattr(getattr(app.state, "redis_bindings", None), "client", None)
    execution_lock_manager = ExecutionLockManager(redis_client, key_factory=key_factory)
    execution_cache = ExecutionCache(redis_client, key_factory)
    chat_persistence_service = ChatPersistenceService(
        authority_client=getattr(app.state, "authority_client", None),
        execution_lock_manager=execution_lock_manager,
        execution_cache=execution_cache,
        durable_mode_enabled=bool(app.state.settings.durable_mode_enabled),
    )
    patent_runtime = build_default_patent_runtime(execution_cache=execution_cache)
    component_status = dict(getattr(app.state, "component_status", {}) or {})
    runtime_status = dict(component_status.get("runtime") or {})
    runtime_status["ready"] = patent_runtime is not None
    if patent_runtime is None:
        runtime_status["detail"] = "patent runtime bootstrap unavailable"
    else:
        runtime_status.pop("detail", None)
    component_status["runtime"] = runtime_status
    app.state.component_status = component_status
    try:
        ask_service = AskService(
            patent_executor=PatentExecutor(
                runtime=patent_runtime,
                execution_cache=execution_cache,
                runtime_required=True,
            ),
            persistence_service=chat_persistence_service,
        )
        original_service = OriginalViewService(
            execution_cache=execution_cache,
        )
    except Exception:
        close = getattr(patent_runtime, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        raise
    app.state.execution_lock_manager = execution_lock_manager
    app.state.execution_cache = execution_cache
    app.state.chat_persistence_service = chat_persistence_service
    app.state.patent_runtime = patent_runtime
    app.state.ask_service = ask_service
    app.state.original_service = original_service


def _close_state_resource(container: object, attr_name: str) -> None:
    resource = getattr(container, attr_name, None)
    if resource is None:
        return
    try:
        setattr(container, attr_name, None)
    except Exception:
        pass
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return


def _bootstrap_app_state(app: FastAPI) -> None:
    try:
        bootstrap_redis_state(app.state)
        _bootstrap_authority_state(app)
        _bootstrap_service_state(app)
    except Exception:
        _close_state_resource(app.state, "authority_client")
        _close_state_resource(app.state, "patent_runtime")
        redis_bindings = getattr(app.state, "redis_bindings", None)
        if redis_bindings is not None:
            _close_state_resource(redis_bindings, "client")
        raise


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if bool(getattr(app.state, "_rebootstrap_on_startup", False)):
        _bootstrap_app_state(app)
        app.state._rebootstrap_on_startup = False
    try:
        yield
    finally:
        _close_state_resource(app.state, "authority_client")
        _close_state_resource(app.state, "patent_runtime")
        redis_bindings = getattr(app.state, "redis_bindings", None)
        if redis_bindings is not None:
            _close_state_resource(redis_bindings, "client")
        app.state._rebootstrap_on_startup = True



def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(str(os.getenv("PATENT_LOG_LEVEL") or os.getenv("LOG_LEVEL") or "INFO"))
    app = FastAPI(title=settings.service_name, lifespan=_lifespan)
    app.logger = logging.getLogger("patent.server_fastapi")

    dispatcher = OrderedTaskDispatcher(
        stream_max_concurrent=settings.runtime.ask_stream_max_concurrent,
        ask_executor_max_workers=settings.runtime.ask_executor_max_workers,
    )

    app.state.service_name = settings.service_name
    app.state.settings = settings
    app.state.runtime_dispatcher = dispatcher
    app.state.component_status = {
        "redis": {"ready": False},
        "authority": {"ready": False},
        "runtime": dispatcher.runtime_state(),
    }
    app.state.original_route_compatibility_enabled = False
    app.state._rebootstrap_on_startup = False

    app.add_middleware(TraceContextMiddleware)

    _bootstrap_app_state(app)
    register_exception_handlers(app)
    register_routers(app)
    return app
