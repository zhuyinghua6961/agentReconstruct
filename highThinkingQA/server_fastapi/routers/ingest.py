"""FastAPI ingest routes."""

# Deprecated: this router is no longer registered in the current architecture.
# Ingest HTTP entrypoints are no longer served by highThinkingQA.


from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from server.errors.core import raise_invalid_request
from server.services.ingest_service import ingest_service

router = APIRouter()


def _status_from_code(code: str) -> int:
    mapping = {
        "VALIDATION_ERROR": 400,
        "NOT_FOUND": 404,
        "INGEST_BUSY": 409,
    }
    return int(mapping.get(str(code or ""), 500))


def _json_result(result: dict[str, Any], *, default_status: int = 200):
    if result.get("success"):
        return JSONResponse(content=result, status_code=default_status)
    status = _status_from_code(str(result.get("code") or ""))
    return JSONResponse(content=result, status_code=status)


@router.post("/api/v1/ingest")
@router.post("/api/ingest")
async def create_ingest_job(payload: dict[str, Any] | None = Body(default=None)):
    if payload is not None and not isinstance(payload, dict):
        raise_invalid_request("request body must be a JSON object")
    result = ingest_service.create_ingest_job(payload=payload or {})
    return _json_result(result, default_status=200)


@router.get("/api/v1/ingest/{job_id}")
@router.get("/api/ingest/{job_id}")
async def get_ingest_job(job_id: str):
    if not str(job_id or "").strip():
        raise_invalid_request("job_id is required")
    result = ingest_service.get_job(job_id=str(job_id).strip())
    return _json_result(result, default_status=200)
