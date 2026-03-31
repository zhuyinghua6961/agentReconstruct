"""Health endpoints for the gateway and its backend roles."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app.services.execution_admission import build_admission_status
from app.services.proxy import ProxyService

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    registry = request.app.state.backend_registry
    settings = request.app.state.settings
    proxy_service: ProxyService = request.app.state.proxy_service
    queue_store = request.app.state.execution_queue_status_store
    relay_store = request.app.state.execution_event_relay_store
    slot_lease_store = request.app.state.execution_slot_lease_store

    probes = await asyncio.gather(
        *(proxy_service.probe_health(target=target) for target in registry.all().values())
    )
    components = dict(getattr(request.app.state, "component_status", {}))
    redis_status = dict(request.app.state.redis_runtime.status.to_dict())
    redis_status["live_available"] = bool(request.app.state.redis_runtime.service.probe())
    components["redis"] = redis_status
    components["admission"] = build_admission_status(
        settings=settings,
        redis_runtime=request.app.state.redis_runtime,
        queue_status_store=queue_store,
        slot_lease_store=slot_lease_store,
    )
    components["queue_status_store"] = queue_store.describe()
    components["event_relay_store"] = relay_store.describe()
    components["slot_lease_store"] = slot_lease_store.describe()
    return {
        "success": True,
        "service": settings.app_name,
        "environment": settings.environment,
        "conversation_file_provider": request.app.state.conversation_file_service.provider_name,
        "runtime_role": settings.admission.runtime_role,
        "components": components,
        "backend_config_warnings": list(settings.backend_config_warnings),
        "strict_backend_config": bool(settings.strict_backend_config),
        "backends": {name: target.base_url for name, target in registry.all().items()},
        "upstreams": {item["backend"]: item for item in probes},
    }
