"""FastAPI health routes."""

from fastapi import APIRouter, Request

from server.runtime.request_context import get_trace_id

router = APIRouter()


@router.get("/api/v1/health")
@router.get("/api/health")
async def health_check(request: Request):
    redis_status = dict(getattr(request.app.state, "component_status", {}).get("redis") or {})
    return {
        "success": True,
        "service": "highThinking-api",
        "version": "v1",
        "status": "ok",
        "trace_id": get_trace_id(),
        "components": {
            "redis": redis_status,
        },
    }
