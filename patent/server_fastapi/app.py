import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import get_settings
from server.patent.executor import PatentExecutor
from server.patent.graph_kb import bootstrap_patent_neo4j_client
from server.patent.graph_kb.service import try_patent_graph_kb_answer
from server.patent.hybrid_synthesis import PatentHybridSynthesisClient
from server.patent.original_service import OriginalViewService
from server.patent.pdf_service import PatentPdfAnswerClient, PatentPdfService
from server.patent.runtime import build_default_patent_runtime
from server.patent.tabular_service import PatentTabularAnswerClient, PatentTabularService
from server.patent.upstream_http import PatentSharedUpstreamHttpProvider
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


_LOGGER = logging.getLogger("patent.server_fastapi")


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
    patent_shared_upstream_provider = None
    patent_pdf_service = None
    patent_tabular_service = None
    patent_hybrid_synthesis_client = None
    patent_runtime = None
    patent_graph_kb_client = None
    shared_http_client = None
    try:
        try:
            patent_shared_upstream_provider = PatentSharedUpstreamHttpProvider.from_env()
            shared_http_client = (
                patent_shared_upstream_provider.client()
                if patent_shared_upstream_provider is not None
                else None
            )
        except Exception:
            _LOGGER.warning(
                "Patent shared upstream provider bootstrap failed; degrading to private clients",
                exc_info=True,
            )
            close = getattr(patent_shared_upstream_provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            patent_shared_upstream_provider = None
            shared_http_client = None
        pdf_answer_client = (
            PatentPdfAnswerClient.from_env(http_client=shared_http_client)
            if shared_http_client is not None
            else PatentPdfAnswerClient.from_env()
        )
        patent_pdf_service = PatentPdfService(answer_client=pdf_answer_client)
        component_status = dict(getattr(app.state, "component_status", {}) or {})
        try:
            tabular_answer_client = (
                PatentTabularAnswerClient.from_env(http_client=shared_http_client)
                if shared_http_client is not None
                else PatentTabularAnswerClient.from_env()
            )
            component_status["patent_tabular_answer_client"] = {
                "ready": tabular_answer_client is not None,
                "status": "ready" if tabular_answer_client is not None else "disabled",
            }
        except Exception:
            _LOGGER.warning(
                "Patent tabular answer client bootstrap failed; degrading to fallback answers",
                exc_info=True,
            )
            tabular_answer_client = None
            component_status["patent_tabular_answer_client"] = {
                "ready": False,
                "status": "degraded",
                "detail": "patent tabular answer client bootstrap failed",
            }
        app.state.component_status = component_status
        patent_tabular_service = PatentTabularService(
            answer_client=tabular_answer_client,
            auto_answer_client=False,
        )
        try:
            patent_hybrid_synthesis_client = (
                PatentHybridSynthesisClient.from_env(http_client=shared_http_client)
                if shared_http_client is not None
                else PatentHybridSynthesisClient.from_env()
            )
            component_status = dict(getattr(app.state, "component_status", {}) or {})
            component_status["patent_hybrid_synthesis_client"] = {
                "ready": patent_hybrid_synthesis_client is not None,
                "status": "ready" if patent_hybrid_synthesis_client is not None else "disabled",
            }
        except Exception:
            _LOGGER.warning(
                "Patent hybrid synthesis client bootstrap failed; degrading to fallback hybrid synthesis",
                exc_info=True,
            )
            patent_hybrid_synthesis_client = None
            component_status = dict(getattr(app.state, "component_status", {}) or {})
            component_status["patent_hybrid_synthesis_client"] = {
                "ready": False,
                "status": "degraded",
                "detail": "patent hybrid synthesis client bootstrap failed",
            }
        app.state.component_status = component_status
        patent_runtime = build_default_patent_runtime(
            execution_cache=execution_cache,
            http_client=shared_http_client,
        )
        component_status = dict(getattr(app.state, "component_status", {}) or {})
        runtime_status = dict(component_status.get("runtime") or {})
        runtime_status["ready"] = patent_runtime is not None
        if patent_runtime is None:
            runtime_status["detail"] = "patent runtime bootstrap unavailable"
        else:
            runtime_status.pop("detail", None)
        component_status["runtime"] = runtime_status
        graph_settings = app.state.settings.graph_kb
        graph_status = dict(component_status.get("patent_graph_kb") or {})
        if not bool(graph_settings.enabled):
            graph_status.update(
                {
                    "ready": False,
                    "enabled": False,
                    "status": "skipped",
                    "url": str(graph_settings.neo4j_url or ""),
                    "database": str(graph_settings.neo4j_database or "neo4j"),
                }
            )
        else:
            patent_graph_kb_client = bootstrap_patent_neo4j_client(
                url=str(graph_settings.neo4j_url or ""),
                username=str(graph_settings.neo4j_username or "neo4j"),
                password=str(graph_settings.neo4j_password or ""),
                database=str(graph_settings.neo4j_database or "neo4j"),
                logger=_LOGGER,
            )
            graph_ready = bool(getattr(patent_graph_kb_client, "available", False)) and not bool(
                getattr(patent_graph_kb_client, "degraded", False)
            )
            graph_status.update(
                {
                    "ready": graph_ready,
                    "enabled": True,
                    "status": "ok" if graph_ready else "degraded",
                    "url": str(graph_settings.neo4j_url or ""),
                    "database": str(graph_settings.neo4j_database or "neo4j"),
                    "error": str(getattr(patent_graph_kb_client, "error", "") or ""),
                }
            )
        component_status["patent_graph_kb"] = graph_status
        app.state.component_status = component_status
        ask_service = AskService(
            patent_executor=PatentExecutor(
                runtime=patent_runtime,
                execution_cache=execution_cache,
                runtime_required=True,
                pdf_service=patent_pdf_service,
                tabular_service=patent_tabular_service,
                hybrid_synthesis_service=patent_hybrid_synthesis_client,
                graph_kb_service=try_patent_graph_kb_answer,
                graph_kb_client=patent_graph_kb_client,
                graph_kb_enabled=bool(graph_settings.enabled),
                graph_kb_max_rows=int(graph_settings.max_rows or 20),
                graph_kb_timeout_ms=int(graph_settings.timeout_ms or 3000),
            ),
            persistence_service=chat_persistence_service,
        )
        original_service = OriginalViewService(
            execution_cache=execution_cache,
        )
    except Exception:
        close = getattr(patent_hybrid_synthesis_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        close = getattr(patent_tabular_service, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        close = getattr(patent_pdf_service, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        close = getattr(patent_runtime, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        close = getattr(patent_graph_kb_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        close = getattr(patent_shared_upstream_provider, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        raise
    app.state.execution_lock_manager = execution_lock_manager
    app.state.execution_cache = execution_cache
    app.state.chat_persistence_service = chat_persistence_service
    app.state.patent_shared_upstream_provider = patent_shared_upstream_provider
    app.state.patent_pdf_service = patent_pdf_service
    app.state.patent_tabular_service = patent_tabular_service
    app.state.patent_hybrid_synthesis_client = patent_hybrid_synthesis_client
    app.state.patent_runtime = patent_runtime
    app.state.patent_graph_kb_client = patent_graph_kb_client
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
        _close_state_resource(app.state, "patent_pdf_service")
        _close_state_resource(app.state, "patent_tabular_service")
        _close_state_resource(app.state, "patent_hybrid_synthesis_client")
        _close_state_resource(app.state, "patent_shared_upstream_provider")
        _close_state_resource(app.state, "patent_runtime")
        _close_state_resource(app.state, "patent_graph_kb_client")
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
        _close_state_resource(app.state, "patent_pdf_service")
        _close_state_resource(app.state, "patent_tabular_service")
        _close_state_resource(app.state, "patent_hybrid_synthesis_client")
        _close_state_resource(app.state, "patent_shared_upstream_provider")
        _close_state_resource(app.state, "patent_runtime")
        _close_state_resource(app.state, "patent_graph_kb_client")
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
        "patent_graph_kb": {
            "ready": False,
            "enabled": bool(settings.graph_kb.enabled),
            "status": "degraded" if bool(settings.graph_kb.enabled) else "skipped",
        },
    }
    app.state.original_route_compatibility_enabled = False
    app.state._rebootstrap_on_startup = False

    app.add_middleware(TraceContextMiddleware)

    _bootstrap_app_state(app)
    register_exception_handlers(app)
    register_routers(app)
    return app
