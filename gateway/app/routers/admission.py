"""Passive admission control-plane routes.

These endpoints expose queue/relay state without changing the existing
ask or ask_stream execution paths.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.services.execution_admission import build_admission_status


router = APIRouter(prefix="/api/admission", tags=["admission"])


def _forbidden() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "success": False,
            "code": "ADMISSION_CONTROL_FORBIDDEN",
            "error": "admission_control_forbidden",
            "message": "admission control access is not permitted",
        },
    )


def _authorize(request: Request) -> JSONResponse | None:
    settings = request.app.state.settings
    configured = str(settings.admission.control_api_token or "").strip()
    if configured:
        provided = str(request.headers.get("x-admission-control-token") or "").strip()
        if not secrets.compare_digest(provided, configured):
            return _forbidden()
        return None
    if str(settings.environment or "").strip().lower() in {"dev", "test"}:
        return None
    return _forbidden()


def _not_found(*, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "code": "ADMISSION_REQUEST_NOT_FOUND",
            "error": "admission_request_not_found",
            "message": f"request_id={request_id} was not found",
            "request_id": request_id,
        },
    )


def _conflict(*, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "success": False,
            "code": "ADMISSION_REQUEST_NOT_CANCELLABLE",
            "error": "admission_request_not_cancellable",
            "message": f"request_id={request_id} is not cancellable in the current state",
            "request_id": request_id,
        },
    )


@router.get("/status")
async def admission_status(request: Request) -> dict:
    denied = _authorize(request)
    if denied is not None:
        return denied
    queue_store = request.app.state.execution_queue_status_store
    relay_store = request.app.state.execution_event_relay_store
    slot_lease_store = request.app.state.execution_slot_lease_store
    return {
        "success": True,
        "admission": build_admission_status(
            settings=request.app.state.settings,
            redis_runtime=request.app.state.redis_runtime,
            queue_status_store=queue_store,
            slot_lease_store=slot_lease_store,
        ),
        "queue_status_store": queue_store.describe(),
        "event_relay_store": relay_store.describe(),
        "slot_lease_store": slot_lease_store.describe(),
    }


@router.get("/requests/{request_id}")
async def admission_request_detail(request_id: str, request: Request):
    denied = _authorize(request)
    if denied is not None:
        return denied
    queue_store = request.app.state.execution_queue_status_store
    relay_store = request.app.state.execution_event_relay_store
    record = queue_store.get_request(request_id)
    if record is None:
        return _not_found(request_id=request_id)
    result = queue_store.get_result(request_id)
    return {
        "success": True,
        "request_id": request_id,
        "request": record,
        "result": result,
        "result_available": result is not None,
        "relay": relay_store.describe_request(request_id),
    }


@router.post("/requests/{request_id}/cancel")
async def admission_cancel_request(request_id: str, request: Request):
    denied = _authorize(request)
    if denied is not None:
        return denied
    queue_store = request.app.state.execution_queue_status_store
    record = queue_store.get_request(request_id)
    if record is None:
        return _not_found(request_id=request_id)
    cancelled = queue_store.cancel_request(
        request_id,
        cancelled_at=datetime.now(timezone.utc).isoformat(),
    )
    if cancelled is None:
        return _conflict(request_id=request_id)
    return {
        "success": True,
        "request_id": request_id,
        "request": cancelled,
    }


@router.get("/requests/{request_id}/frames")
async def admission_request_frames(
    request_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
) -> dict:
    denied = _authorize(request)
    if denied is not None:
        return denied
    relay_store = request.app.state.execution_event_relay_store
    return {
        "success": True,
        "request_id": request_id,
        "after_sequence": int(after_sequence),
        "frames": relay_store.get_frames(request_id, after_sequence=after_sequence),
        "relay": relay_store.describe_request(request_id),
    }
