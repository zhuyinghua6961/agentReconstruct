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


@router.get("/healthz")
@router.get("/api/health")
def healthz(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    redis_status = dict(request.app.state.component_status.get("redis") or {})
    generation_runtime_status = dict(request.app.state.component_status.get("generation_runtime") or {})
    graph_kb_status = dict(request.app.state.component_status.get("graph_kb") or {})
    shared_llm_pool_status = _shared_llm_pool_status(request)
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
            },
        },
    )
