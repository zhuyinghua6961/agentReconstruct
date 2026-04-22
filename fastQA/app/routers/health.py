from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import RESOURCE_ROOT, SERVICE_RUNTIME_ROOT, SERVICE_STATE_ROOT

router = APIRouter(tags=["health"])


def _shared_llm_pool_status(request: Request) -> dict[str, object]:
    status = dict(request.app.state.component_status.get("shared_llm_pool") or {})
    shared_pool = getattr(request.app.state, "shared_llm_http_pool", None)
    snapshot = dict(getattr(shared_pool, "snapshot", lambda: {})() or {})
    if not snapshot:
        return status
    for field in (
        "shared_client_id",
        "pid",
        "bootstrap_source",
        "pool_timeout_count",
        "pool_wait_ms",
        "max_connections",
        "max_keepalive_connections",
        "keepalive_expiry_seconds",
    ):
        if field in snapshot:
            status[field] = snapshot[field]
    return status


def _stage2_hot_pool_status(request: Request, *, component_name: str, state_attr: str) -> dict[str, object]:
    status = dict(request.app.state.component_status.get(component_name) or {})
    pool = getattr(request.app.state, state_attr, None)
    snapshot = dict(getattr(pool, "snapshot", lambda: {})() or {})
    if not snapshot:
        return status
    for field in (
        "total_lanes",
        "ready_lanes",
        "warming_lanes",
        "degraded_lanes",
        "last_any_warm_success_at",
        "last_any_error_at",
        "last_error_summary",
        "next_keepalive_at",
    ):
        if field in snapshot:
            status[field] = snapshot[field]
    enabled = bool(status.get("enabled", False))
    ready_lanes = int(status.get("ready_lanes") or 0)
    warming_lanes = int(status.get("warming_lanes") or 0)
    degraded_lanes = int(status.get("degraded_lanes") or 0)
    if enabled and ready_lanes > 0:
        status["status"] = "ok"
        status["ready"] = True
    elif enabled and warming_lanes > 0:
        status["status"] = "pending"
        status["ready"] = False
    elif enabled and degraded_lanes > 0:
        status["status"] = "degraded"
        status["ready"] = False
    return status


@router.get("/healthz")
@router.get("/api/health")
def healthz(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    redis_status = dict(request.app.state.component_status.get("redis") or {})
    generation_runtime_status = dict(request.app.state.component_status.get("generation_runtime") or {})
    graph_kb_status = dict(request.app.state.component_status.get("graph_kb") or {})
    shared_llm_pool_status = _shared_llm_pool_status(request)
    stage2_chat_hot_pool_status = _stage2_hot_pool_status(
        request,
        component_name="stage2_chat_hot_pool",
        state_attr="stage2_chat_hot_pool",
    )
    stage2_rerank_hot_pool_status = _stage2_hot_pool_status(
        request,
        component_name="stage2_rerank_hot_pool",
        state_attr="stage2_rerank_hot_pool",
    )
    generation_ready = bool(getattr(request.app.state, "generation_runtime_ready", False))
    graph_kb_ready = bool(getattr(request.app.state, "graph_kb_ready", False))
    is_readiness_probe = str(getattr(request.url, "path", "") or "").endswith("/api/health")
    status_code = 200
    success = True
    if is_readiness_probe and not generation_ready:
        status_code = 503
        success = False
    return JSONResponse(
        status_code=status_code,
        content={
            "success": success,
            "service": "fastQA",
            "environment": settings.app_env,
            "resource_root": str(RESOURCE_ROOT) if RESOURCE_ROOT is not None else None,
            "service_state_root": str(SERVICE_STATE_ROOT),
            "service_runtime_root": str(SERVICE_RUNTIME_ROOT),
            "api_prefix": settings.api_prefix,
            "generation_runtime_enabled": settings.generation_runtime_enabled,
            "generation_runtime_ready": generation_ready,
            "graph_kb_enabled": settings.graph_kb_enabled,
            "graph_kb_ready": graph_kb_ready,
            "runtime_mode": "generation" if generation_ready else "placeholder",
            "supported_routes": ["kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"],
            "placeholder_fallback_enabled": settings.allow_placeholder_fallback,
            "file_context_fallback_enabled": settings.file_context_fallback_enabled,
            "ask_stream_max_concurrent": settings.ask_stream_max_concurrent,
            "sse_heartbeat_sec": settings.sse_heartbeat_sec,
            "components": {
                "redis": redis_status,
                "generation_runtime": generation_runtime_status,
                "graph_kb": graph_kb_status,
                "shared_llm_pool": shared_llm_pool_status,
                "stage2_chat_hot_pool": stage2_chat_hot_pool_status,
                "stage2_rerank_hot_pool": stage2_rerank_hot_pool_status,
            },
        },
    )
