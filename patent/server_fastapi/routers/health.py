from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from server.errors import codes
from server.errors.core import APIError
from server_fastapi.auth.deps import require_auth_context
from server_fastapi.http import read_bool_query


router = APIRouter()
_FILE_ROUTES = {"pdf_qa", "tabular_qa", "hybrid_qa"}



def _copy_components(request: Request) -> dict:
    source = getattr(request.app.state, "component_status", {})
    components = {name: dict(value or {}) for name, value in dict(source).items()}
    dispatcher = getattr(request.app.state, "runtime_dispatcher", None)
    if dispatcher is not None:
        runtime = dict(components.get("runtime") or {})
        dynamic_runtime = dict(dispatcher.runtime_state())
        runtime_ready = bool(runtime.get("ready", True)) and bool(dynamic_runtime.get("ready", True))
        runtime.update(dynamic_runtime)
        runtime["ready"] = runtime_ready
        components["runtime"] = runtime
    shared_llm_pool = getattr(request.app.state, "shared_llm_pool", None)
    if shared_llm_pool is not None:
        shared = dict(components.get("shared_llm_pool") or {})
        dynamic_shared = dict(getattr(shared_llm_pool, "snapshot", lambda: {})() or {})
        shared.update(dynamic_shared)
        enabled = bool(shared.get("enabled", getattr(shared_llm_pool, "enabled", False)))
        ready = bool(enabled and getattr(shared_llm_pool, "client", lambda: None)() is not None)
        shared["enabled"] = enabled
        shared["ready"] = ready
        shared["status"] = "ok" if ready else ("disabled" if not enabled else "degraded")
        if ready:
            shared["detail"] = ""
            shared["error"] = ""
        elif not enabled:
            shared["detail"] = "shared llm pool disabled"
        else:
            shared["detail"] = "shared llm pool client unavailable"
        components["shared_llm_pool"] = shared
    planning_hot_pool = getattr(request.app.state, "planning_hot_pool", None)
    if planning_hot_pool is not None:
        planning = dict(components.get("planning_hot_pool") or {})
        dynamic_planning = dict(getattr(planning_hot_pool, "snapshot", lambda: {})() or {})
        planning.update(dynamic_planning)
        enabled = bool(planning.get("enabled", False))
        ready = bool(enabled and int(planning.get("ready_lanes", 0) or 0) > 0)
        planning["enabled"] = enabled
        planning["ready"] = ready
        planning["status"] = "ok" if ready else ("disabled" if not enabled else "degraded")
        if ready:
            planning["detail"] = ""
            planning["error"] = ""
        elif not enabled:
            planning["detail"] = "planning hot pool disabled"
        else:
            planning["detail"] = "planning hot pool has no ready lanes"
        components["planning_hot_pool"] = planning
    planning_upstream_gate = getattr(request.app.state, "planning_upstream_gate", None)
    if planning_upstream_gate is not None:
        gate = dict(components.get("planning_upstream_gate") or {})
        dynamic_gate = dict(getattr(planning_upstream_gate, "snapshot", lambda: {})() or {})
        gate.update(dynamic_gate)
        gate["enabled"] = True
        gate["ready"] = True
        gate["status"] = "ok"
        gate["detail"] = ""
        gate["error"] = ""
        components["planning_upstream_gate"] = gate
    return components



def _runtime_ready(components: dict) -> bool:
    return bool(dict(components.get("runtime") or {}).get("ready", False))



def _durable_dependencies_ready(components: dict) -> bool:
    return all(bool(dict(components.get(name) or {}).get("ready", False)) for name in ("runtime", "redis", "authority"))


def _route_requires_runtime(*, route: str, source_scope: str) -> bool:
    normalized_route = str(route or "").strip()
    normalized_scope = str(source_scope or "").strip()
    if not normalized_route and not normalized_scope:
        return True
    if normalized_route == "kb_qa":
        return True
    return "kb" in normalized_scope.split("+")


def _file_route_gate_enabled(*, route: str, settings) -> bool:
    normalized_route = str(route or "").strip()
    if normalized_route not in _FILE_ROUTES:
        return True
    return bool(getattr(settings, "patent_file_routes_enabled", False))


@router.get("/api/v1/health")
@router.get("/api/health")
async def health_check(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    durable_requested = read_bool_query(request, "durable", default=False)
    route = str(request.query_params.get("route") or "").strip()
    source_scope = str(request.query_params.get("source_scope") or "").strip()
    components = _copy_components(request)
    settings = request.app.state.settings
    durable_required_components = ["redis", "authority"]
    if _route_requires_runtime(route=route, source_scope=source_scope):
        durable_required_components.append("runtime")
    durable_ready = all(bool(dict(components.get(name) or {}).get("ready", False)) for name in durable_required_components)

    if durable_requested:
        require_auth_context(authorization)
        if not settings.durable_mode_enabled:
            raise APIError(
                code=codes.DURABLE_MODE_DISABLED,
                message="durable patent mode is disabled",
                status_code=503,
                error="durable_mode_disabled",
                retriable=False,
                extra={
                    "status": "degraded",
                    "components": components,
                    "durable_requested": True,
                },
            )
        if not _file_route_gate_enabled(route=route, settings=settings):
            raise APIError(
                code=codes.SERVICE_NOT_READY,
                message="durable patent dependencies are not ready",
                status_code=503,
                error="service_not_ready",
                retriable=True,
                extra={
                    "status": "degraded",
                    "components": components,
                    "durable_requested": True,
                },
            )
        if not durable_ready:
            raise APIError(
                code=codes.SERVICE_NOT_READY,
                message="durable patent dependencies are not ready",
                status_code=503,
                error="service_not_ready",
                retriable=True,
                extra={
                    "status": "degraded",
                    "components": components,
                    "durable_requested": True,
                },
            )

    status_code = 200
    status = "ok"
    runtime_degraded = not _runtime_ready(components)
    if durable_requested:
        runtime_degraded = _route_requires_runtime(route=route, source_scope=source_scope) and runtime_degraded
    if runtime_degraded or (settings.durable_mode_enabled and not durable_ready):
        status_code = 503
        status = "degraded"

    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "service": request.app.state.service_name,
            "status": status,
            "durable_mode_enabled": bool(settings.durable_mode_enabled),
            "durable_requested": durable_requested,
            "patent_graph_kb_enabled": bool(getattr(settings, "graph_kb", None) and settings.graph_kb.enabled),
            "patent_graph_kb_ready": bool(dict(components.get("patent_graph_kb") or {}).get("ready", False)),
            "patent_graph_kb_v2_enabled": bool(
                getattr(settings, "graph_kb", None) and settings.graph_kb.v2_enabled
            ),
            "patent_graph_kb_rag_injection_enabled": bool(
                getattr(settings, "graph_kb", None) and settings.graph_kb.rag_injection_enabled
            ),
            "components": components,
        },
    )
