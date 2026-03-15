"""FastAPI system/info routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.services.system_service import system_service

router = APIRouter()


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/api/v1/kb_info")
@router.get("/api/kb_info")
async def kb_info():
    payload, status_code = system_service.build_kb_info()
    return _json(payload, status_code)


@router.post("/api/v1/refresh_kb")
@router.post("/api/refresh_kb")
async def refresh_kb():
    payload, status_code = system_service.refresh_kb()
    return _json(payload, status_code)


@router.post("/api/v1/clear_cache")
@router.post("/api/clear_cache")
async def clear_cache():
    payload, status_code = system_service.clear_cache()
    return _json(payload, status_code)
