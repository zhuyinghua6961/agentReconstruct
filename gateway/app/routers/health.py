"""Health endpoints for the gateway and its backend roles."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app.services.proxy import ProxyService

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    registry = request.app.state.backend_registry
    settings = request.app.state.settings
    proxy_service: ProxyService = request.app.state.proxy_service

    probes = await asyncio.gather(
        *(proxy_service.probe_health(target=target) for target in registry.all().values())
    )
    return {
        "success": True,
        "service": settings.app_name,
        "environment": settings.environment,
        "conversation_file_provider": request.app.state.conversation_file_service.provider_name,
        "backend_config_warnings": list(settings.backend_config_warnings),
        "strict_backend_config": bool(settings.strict_backend_config),
        "backends": {name: target.base_url for name, target in registry.all().items()},
        "upstreams": {item["backend"]: item for item in probes},
    }
