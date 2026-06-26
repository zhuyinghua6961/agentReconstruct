from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.deps import AuthContext
from app.modules.auth.deps import require_admin_context, require_auth_context
from app.modules.usage_stats import service as usage_stats_service_module
from app.modules.usage_stats.schemas import HeartbeatRequest


router = APIRouter(tags=["activity"])


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


@router.post("/api/v1/activity/heartbeat")
@router.post("/api/activity/heartbeat")
def activity_heartbeat(
    payload: HeartbeatRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        usage_stats_service_module.usage_stats_service.process_heartbeat(
            user_id=int(context.user_id),
            session_id=payload.session_id,
            finalize=bool(payload.finalize),
            last_interaction_at=payload.last_interaction_at,
        )
    )
