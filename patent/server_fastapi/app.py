import logging
import os
import inspect
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import get_settings
from server.patent.executor import PatentExecutor
from server.patent.graph_kb import bootstrap_patent_neo4j_client
from server.patent.graph_kb.service import route_patent_graph_kb_v2, try_patent_graph_kb_answer
from server.patent.hybrid_synthesis import PatentHybridSynthesisClient
from server.patent.original_service import OriginalViewService
from server.patent.planning_hot_pool import PatentPlanningHotPool, PatentPlanningHotPoolConfig
from server.patent.pdf_service import PatentPdfAnswerClient, PatentPdfService
from server.patent.browse_search import build_patent_browse_search_service
from server.patent.runtime import (
    build_default_patent_runtime,
    build_patent_planning_runtime_inputs,
    resolve_patent_planning_runtime_model,
)
from server.patent.tabular_service import PatentTabularAnswerClient, PatentTabularService
from server.patent.upstream_gate import PatentPlanningUpstreamGate
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


def _default_shared_llm_pool_status(settings) -> dict[str, object]:
    llm_http = getattr(settings, "llm_http", None)
    enabled = bool(getattr(llm_http, "shared_pool_enabled", False))
    return {
        "enabled": enabled,
        "ready": False,
        "status": "disabled" if not enabled else "degraded",
        "detail": "shared llm pool disabled" if not enabled else "shared llm pool not initialized",
        "error": "",
        "pool_owner": "app",
        "client_owner": "disabled" if not enabled else "shared",
        "shared_client_id": "",
        "pid": os.getpid(),
        "bootstrap_source": "startup",
        "pool_timeout_count": 0,
        "pool_wait_ms": 0.0,
        "max_connections": int(getattr(llm_http, "max_connections", 0) or 0),
        "max_keepalive_connections": int(getattr(llm_http, "max_keepalive_connections", 0) or 0),
        "keepalive_expiry_seconds": float(getattr(llm_http, "keepalive_expiry_seconds", 0.0) or 0.0),
    }


def _shared_llm_pool_status_from_provider(
    *,
    provider,
    shared_http_client,
    default_status: dict[str, object],
) -> dict[str, object]:
    status = dict(default_status or {})
    snapshot = dict(getattr(provider, "snapshot", lambda: {})() or {})
    status.update(snapshot)
    enabled = bool(getattr(provider, "enabled", shared_http_client is not None or status.get("enabled", False)))
    ready = bool(enabled and shared_http_client is not None)
    status.update(
        {
            "enabled": enabled,
            "ready": ready,
            "status": "ok" if ready else ("disabled" if not enabled else "degraded"),
            "detail": "" if ready else ("shared llm pool disabled" if not enabled else "shared llm pool client unavailable"),
            "error": "",
        }
    )
    return status


def _degraded_shared_llm_pool_status(*, settings, error: Exception, default_status: dict[str, object]) -> dict[str, object]:
    status = dict(default_status or _default_shared_llm_pool_status(settings))
    enabled = bool(status.get("enabled", False))
    status.update(
        {
            "enabled": enabled,
            "ready": False,
            "status": "degraded" if enabled else "disabled",
            "detail": "patent shared upstream provider bootstrap failed",
            "error": str(error),
            "shared_client_id": "",
            "pool_timeout_count": 0,
            "pool_wait_ms": 0.0,
        }
    )
    return status


def _default_planning_hot_pool_status(settings=None) -> dict[str, object]:
    planning_settings = getattr(settings, "planning_hot_pool", None)
    config = (
        PatentPlanningHotPoolConfig.from_env()
        if planning_settings is None
        else PatentPlanningHotPoolConfig(
            enabled=bool(getattr(planning_settings, "enabled", False)),
            lane_count=int(getattr(planning_settings, "lane_count", 2) or 2),
            connect_timeout_seconds=float(getattr(getattr(settings, "llm_http", settings), "connect_timeout_seconds", 15.0)),
            read_timeout_seconds=float(getattr(getattr(settings, "llm_http", settings), "read_timeout_seconds", 180.0)),
            stream_read_timeout_seconds=float(
                getattr(getattr(settings, "llm_http", settings), "stream_read_timeout_seconds", 600.0)
            ),
            write_timeout_seconds=float(getattr(getattr(settings, "llm_http", settings), "write_timeout_seconds", 180.0)),
            pool_timeout_seconds=float(getattr(getattr(settings, "llm_http", settings), "pool_timeout_seconds", 30.0)),
            keepalive_expiry_seconds=float(
                getattr(getattr(settings, "llm_http", settings), "keepalive_expiry_seconds", 120.0)
            ),
            warmup_enabled=bool(getattr(planning_settings, "warmup_enabled", False)),
            warm_interval_seconds=float(getattr(planning_settings, "warm_interval_seconds", 7200.0)),
            warm_timeout_seconds=float(getattr(planning_settings, "warm_timeout_seconds", 30.0)),
            warm_jitter_seconds=float(getattr(planning_settings, "warm_jitter_seconds", 0.0)),
            lane_degraded_after_seconds=float(getattr(planning_settings, "lane_degraded_after_seconds", 7200.0)),
            warm_active_start_hour=int(getattr(planning_settings, "warm_active_start_hour", 8)),
            warm_active_end_hour=int(getattr(planning_settings, "warm_active_end_hour", 18)),
        )
    )
    enabled = bool(config.enabled)
    return {
        "enabled": enabled,
        "ready": False,
        "status": "disabled" if not enabled else "degraded",
        "detail": "planning hot pool disabled" if not enabled else "planning hot pool not initialized",
        "error": "",
        "total_lanes": int(config.lane_count),
        "ready_lanes": 0,
        "warming_lanes": 0,
        "degraded_lanes": 0,
        "busy_lanes": 0,
    }


def _planning_hot_pool_status_from_pool(
    *,
    pool,
    default_status: dict[str, object],
) -> dict[str, object]:
    status = dict(default_status or {})
    snapshot = dict(getattr(pool, "snapshot", lambda: {})() or {})
    status.update(snapshot)
    enabled = bool(status.get("enabled", False))
    ready = bool(enabled and int(status.get("ready_lanes", 0) or 0) > 0)
    status.update(
        {
            "enabled": enabled,
            "ready": ready,
            "status": "ok" if ready else ("disabled" if not enabled else "degraded"),
            "detail": "" if ready else ("planning hot pool disabled" if not enabled else "planning hot pool has no ready lanes"),
            "error": "",
        }
    )
    return status


def _default_planning_upstream_gate_status(settings=None) -> dict[str, object]:
    gate_settings = getattr(settings, "planning_upstream_gate", None)
    enabled = bool(getattr(gate_settings, "enabled", False))
    limit = int(getattr(gate_settings, "limit", 1) or 1) if gate_settings is not None else 1
    return {
        "enabled": enabled,
        "ready": enabled,
        "status": "disabled" if not enabled else "degraded",
        "detail": "planning upstream gate disabled" if not enabled else "planning upstream gate not initialized",
        "error": "",
        "name": "planning",
        "limit": limit,
        "effective_limit": 0,
        "in_flight": 0,
    }


def _planning_upstream_gate_status_from_gate(
    *,
    gate,
    default_status: dict[str, object],
) -> dict[str, object]:
    status = dict(default_status or {})
    snapshot = dict(getattr(gate, "snapshot", lambda: {})() or {})
    status.update(snapshot)
    enabled = bool(status.get("enabled", False))
    status.update(
        {
            "enabled": enabled,
            "ready": enabled,
            "status": "ok" if enabled else "disabled",
            "detail": "" if enabled else "planning upstream gate disabled",
            "error": "",
        }
    )
    return status


def _degraded_planning_hot_pool_status(*, error: Exception, default_status: dict[str, object]) -> dict[str, object]:
    status = dict(default_status or _default_planning_hot_pool_status())
    enabled = bool(status.get("enabled", False))
    status.update(
        {
            "enabled": enabled,
            "ready": False,
            "status": "degraded" if enabled else "disabled",
            "detail": "planning hot pool bootstrap failed" if enabled else "planning hot pool disabled",
            "error": str(error),
            "ready_lanes": 0,
            "warming_lanes": 0,
            "degraded_lanes": 0,
            "busy_lanes": 0,
        }
    )
    return status


def _degraded_planning_upstream_gate_status(*, error: Exception, default_status: dict[str, object]) -> dict[str, object]:
    status = dict(default_status or _default_planning_upstream_gate_status())
    enabled = bool(status.get("enabled", False))
    status.update(
        {
            "enabled": enabled,
            "ready": False,
            "status": "degraded" if enabled else "disabled",
            "detail": "planning upstream gate bootstrap failed" if enabled else "planning upstream gate disabled",
            "error": str(error),
            "effective_limit": 0,
            "in_flight": 0,
        }
    )
    return status



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


def _build_patent_runtime_with_optional_dependencies(
    *,
    execution_cache,
    http_client,
    planning_hot_pool,
    planning_upstream_gate,
):
    kwargs = {
        "execution_cache": execution_cache,
        "http_client": http_client,
    }
    try:
        parameters = inspect.signature(build_default_patent_runtime).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "planning_hot_pool" in parameters:
        kwargs["planning_hot_pool"] = planning_hot_pool
    if "planning_upstream_gate" in parameters:
        kwargs["planning_upstream_gate"] = planning_upstream_gate
    return build_default_patent_runtime(**kwargs)



def _bootstrap_service_state(app: FastAPI) -> None:
    settings = app.state.settings
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
    patent_planning_hot_pool = None
    patent_planning_upstream_gate = None
    shared_http_client = None
    component_status = dict(getattr(app.state, "component_status", {}) or {})
    shared_llm_pool_status = dict(
        component_status.get("shared_llm_pool") or _default_shared_llm_pool_status(settings)
    )
    planning_hot_pool_status = dict(
        component_status.get("planning_hot_pool") or _default_planning_hot_pool_status(settings)
    )
    planning_upstream_gate_status = dict(
        component_status.get("planning_upstream_gate") or _default_planning_upstream_gate_status(settings)
    )
    try:
        try:
            provider_from_settings = getattr(PatentSharedUpstreamHttpProvider, "from_settings", None)
            if callable(provider_from_settings):
                patent_shared_upstream_provider = provider_from_settings(settings)
            else:
                patent_shared_upstream_provider = PatentSharedUpstreamHttpProvider.from_env()
            shared_http_client = (
                patent_shared_upstream_provider.client()
                if patent_shared_upstream_provider is not None
                else None
            )
            if patent_shared_upstream_provider is not None:
                shared_llm_pool_status = _shared_llm_pool_status_from_provider(
                    provider=patent_shared_upstream_provider,
                    shared_http_client=shared_http_client,
                    default_status=shared_llm_pool_status,
                )
        except Exception as exc:
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
            shared_llm_pool_status = _degraded_shared_llm_pool_status(
                settings=settings,
                error=exc,
                default_status=shared_llm_pool_status,
            )
            patent_shared_upstream_provider = None
            shared_http_client = None
        component_status["shared_llm_pool"] = shared_llm_pool_status
        app.state.component_status = component_status
        app.state.shared_llm_pool = patent_shared_upstream_provider
        try:
            if bool(planning_hot_pool_status.get("enabled", False)):
                planning_warm_model = resolve_patent_planning_runtime_model()

                def _build_lane_client(*, http_client):
                    client, _ = build_patent_planning_runtime_inputs(http_client=http_client)
                    if client is None:
                        raise RuntimeError("planning hot pool requires patent planning client configuration")
                    return client

                hot_pool_from_settings = getattr(PatentPlanningHotPool, "from_settings", None)
                hot_pool_from_env = getattr(PatentPlanningHotPool, "from_env", None)
                if callable(hot_pool_from_settings):
                    patent_planning_hot_pool = hot_pool_from_settings(
                        settings,
                        lane_client_builder=_build_lane_client,
                        logger=_LOGGER,
                        warm_model=planning_warm_model,
                    )
                elif callable(hot_pool_from_env):
                    patent_planning_hot_pool = hot_pool_from_env(
                        lane_client_builder=_build_lane_client,
                        logger=_LOGGER,
                        warm_model=planning_warm_model,
                    )
                planning_hot_pool_status = _planning_hot_pool_status_from_pool(
                    pool=patent_planning_hot_pool,
                    default_status=planning_hot_pool_status,
                )
        except Exception as exc:
            _LOGGER.warning(
                "Patent planning hot pool bootstrap failed; degrading to shared planning client",
                exc_info=True,
            )
            close = getattr(patent_planning_hot_pool, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            patent_planning_hot_pool = None
            planning_hot_pool_status = _degraded_planning_hot_pool_status(
                error=exc,
                default_status=planning_hot_pool_status,
            )
        component_status = dict(getattr(app.state, "component_status", {}) or {})
        component_status["planning_hot_pool"] = planning_hot_pool_status
        app.state.component_status = component_status
        app.state.planning_hot_pool = patent_planning_hot_pool
        try:
            gate_limit_provider = (
                None
                if patent_planning_hot_pool is None
                else lambda: int(
                    dict(getattr(patent_planning_hot_pool, "snapshot", lambda: {})() or {}).get("ready_lanes", 0) or 0
                )
            )
            gate_from_settings = getattr(PatentPlanningUpstreamGate, "from_settings", None)
            gate_from_env = getattr(PatentPlanningUpstreamGate, "from_env", None)
            if callable(gate_from_settings):
                patent_planning_upstream_gate = gate_from_settings(
                    settings,
                    name="planning",
                    logger=_LOGGER,
                    limit_provider=gate_limit_provider,
                )
            elif callable(gate_from_env):
                patent_planning_upstream_gate = gate_from_env(
                    name="planning",
                    logger=_LOGGER,
                    limit_provider=gate_limit_provider,
                )
            if patent_planning_upstream_gate is not None:
                planning_upstream_gate_status = _planning_upstream_gate_status_from_gate(
                    gate=patent_planning_upstream_gate,
                    default_status=planning_upstream_gate_status,
                )
        except Exception as exc:
            _LOGGER.warning(
                "Patent planning upstream gate bootstrap failed; disabling planning gate",
                exc_info=True,
            )
            patent_planning_upstream_gate = None
            planning_upstream_gate_status = _degraded_planning_upstream_gate_status(
                error=exc,
                default_status=planning_upstream_gate_status,
            )
        component_status = dict(getattr(app.state, "component_status", {}) or {})
        component_status["planning_upstream_gate"] = planning_upstream_gate_status
        app.state.component_status = component_status
        app.state.planning_upstream_gate = patent_planning_upstream_gate
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
        patent_runtime = _build_patent_runtime_with_optional_dependencies(
            execution_cache=execution_cache,
            http_client=shared_http_client,
            planning_hot_pool=patent_planning_hot_pool,
            planning_upstream_gate=patent_planning_upstream_gate,
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
                    "v2_enabled": bool(graph_settings.v2_enabled),
                    "rag_injection_enabled": bool(graph_settings.rag_injection_enabled),
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
                    "v2_enabled": bool(graph_settings.v2_enabled),
                    "rag_injection_enabled": bool(graph_settings.rag_injection_enabled),
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
                graph_kb_service_v2=route_patent_graph_kb_v2,
                graph_kb_client=patent_graph_kb_client,
                graph_kb_enabled=bool(graph_settings.enabled),
                graph_kb_v2_enabled=bool(graph_settings.v2_enabled),
                graph_kb_rag_injection_enabled=bool(graph_settings.rag_injection_enabled),
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
        close = getattr(patent_planning_hot_pool, "close", None)
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
    app.state.shared_llm_pool = patent_shared_upstream_provider
    app.state.patent_shared_upstream_provider = patent_shared_upstream_provider
    app.state.planning_hot_pool = patent_planning_hot_pool
    app.state.patent_pdf_service = patent_pdf_service
    app.state.patent_tabular_service = patent_tabular_service
    app.state.patent_hybrid_synthesis_client = patent_hybrid_synthesis_client
    app.state.patent_runtime = patent_runtime
    app.state.patent_browse_search_service = build_patent_browse_search_service(patent_runtime)
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
        _close_state_resource(app.state, "shared_llm_pool")
        _close_state_resource(app.state, "planning_hot_pool")
        _close_state_resource(app.state, "planning_upstream_gate")
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
        _close_state_resource(app.state, "shared_llm_pool")
        _close_state_resource(app.state, "planning_hot_pool")
        _close_state_resource(app.state, "planning_upstream_gate")
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
        "shared_llm_pool": _default_shared_llm_pool_status(settings),
        "planning_hot_pool": _default_planning_hot_pool_status(settings),
        "planning_upstream_gate": _default_planning_upstream_gate_status(settings),
        "patent_graph_kb": {
            "ready": False,
            "enabled": bool(settings.graph_kb.enabled),
            "v2_enabled": bool(settings.graph_kb.v2_enabled),
            "rag_injection_enabled": bool(settings.graph_kb.rag_injection_enabled),
            "status": "degraded" if bool(settings.graph_kb.enabled) else "skipped",
        },
    }
    app.state.shared_llm_pool = None
    app.state.planning_hot_pool = None
    app.state.planning_upstream_gate = None
    app.state.original_route_compatibility_enabled = False
    app.state._rebootstrap_on_startup = False

    app.add_middleware(TraceContextMiddleware)

    _bootstrap_app_state(app)
    register_exception_handlers(app)
    register_routers(app)
    return app
