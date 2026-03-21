from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.deps import AuthContext
from app.core.deps import get_runtime
from app.core.runtime import PublicServiceRuntime
from app.modules.auth.deps import require_admin_context, require_auth_context
from app.modules.system.service import system_service


router = APIRouter(tags=["system"])


@router.get("/health")
@router.get("/api/health")
@router.get("/api/v1/health")
async def health(runtime: PublicServiceRuntime = Depends(get_runtime)) -> JSONResponse:
    return JSONResponse(status_code=200, content=system_service.build_health(runtime))


@router.get("/api/background_status")
@router.get("/api/v1/background_status")
async def background_status(
    runtime: PublicServiceRuntime = Depends(get_runtime),
    _context: AuthContext = Depends(require_admin_context),
) -> JSONResponse:
    payload, status_code = system_service.build_background_status(runtime)
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/api/kb_info")
@router.get("/api/v1/kb_info")
async def kb_info(
    runtime: PublicServiceRuntime = Depends(get_runtime),
    _context: AuthContext = Depends(require_admin_context),
) -> JSONResponse:
    payload, status_code = system_service.build_kb_info(runtime)
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/api/refresh_kb")
@router.post("/api/v1/refresh_kb")
async def refresh_kb(
    runtime: PublicServiceRuntime = Depends(get_runtime),
    _context: AuthContext = Depends(require_admin_context),
) -> JSONResponse:
    payload, status_code = system_service.refresh_kb(runtime)
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/api/clear_cache")
@router.post("/api/v1/clear_cache")
async def clear_cache(
    runtime: PublicServiceRuntime = Depends(get_runtime),
    _context: AuthContext = Depends(require_admin_context),
) -> JSONResponse:
    payload, status_code = system_service.clear_cache(runtime)
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/api/cache_debug/conversation")
@router.get("/api/v1/cache_debug/conversation")
async def conversation_cache_debug(
    conversation_id: int | None = Query(default=None),
    runtime: PublicServiceRuntime = Depends(get_runtime),
    context: AuthContext = Depends(require_auth_context),
) -> JSONResponse:
    payload, status_code = system_service.build_conversation_cache_debug(
        runtime,
        user_id=context.user_id,
        conversation_id=conversation_id,
    )
    return JSONResponse(status_code=status_code, content=payload)
