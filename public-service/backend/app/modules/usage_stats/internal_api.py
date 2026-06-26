from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.errors import AppError
from app.modules.conversation.internal_api import InternalAuthorityCaller, require_internal_authority
from app.modules.usage_stats import service as usage_stats_service_module
from app.modules.usage_stats.schemas import InternalActivityRecordRequest


router = APIRouter(tags=["usage-stats-internal"])


def _require_gateway_internal_caller(
    caller: InternalAuthorityCaller = Depends(require_internal_authority),
) -> InternalAuthorityCaller:
    if str(caller.service_name or "").strip().lower() != "gateway":
        raise AppError(
            message="internal_source_service_forbidden",
            code="INTERNAL_SOURCE_SERVICE_FORBIDDEN",
            status_code=403,
        )
    return caller


def _respond(result: dict, *, ok_status: int = 200) -> JSONResponse:
    status = ok_status
    if not result.get("success"):
        code = str(result.get("code") or "")
        if code == "VALIDATION_ERROR":
            status = 400
        elif code == "DB_UNAVAILABLE":
            status = 503
        else:
            status = 500
    return JSONResponse(status_code=status, content=jsonable_encoder(result))


@router.post("/internal/activity/record")
def record_activity_event(
    payload: InternalActivityRecordRequest,
    _caller: InternalAuthorityCaller = Depends(_require_gateway_internal_caller),
):
    return _respond(
        usage_stats_service_module.usage_stats_service.record_event(
            user_id=int(payload.user_id),
            event_type=payload.event_type,
            trace_id=payload.trace_id,
            conversation_id=payload.conversation_id,
            metadata=payload.metadata,
        )
    )
