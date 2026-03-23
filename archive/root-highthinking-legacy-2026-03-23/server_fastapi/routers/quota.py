"""FastAPI quota routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from server.services.quota_service import quota_service
from server_fastapi.auth.deps import AuthContext, require_admin_context, require_auth_context
from server_fastapi.quota_schemas import CreateQuotaConfigRequest, UpdateQuotaConfigRequest

router = APIRouter()


def _status(result: dict, *, ok_status: int) -> int:
    if result.get("success"):
        return ok_status
    code = str(result.get("code") or "")
    if code == "VALIDATION_ERROR":
        return 400
    if code == "NOT_FOUND":
        return 404
    if code == "ALREADY_EXISTS":
        return 409
    if code == "DB_UNAVAILABLE":
        return 503
    return 500


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(status_code=_status(result, ok_status=ok_status), content=jsonable_encoder(result))


@router.get("/api/v1/quota/my")
@router.get("/api/quota/my")
async def get_my_quotas(context: AuthContext = Depends(require_auth_context)):
    return _respond(quota_service.get_user_quotas(user_id=context.user_id), ok_status=200)


@router.get("/api/v1/quota/configs")
@router.get("/api/quota/configs")
async def get_quota_configs(_context: AuthContext = Depends(require_admin_context)):
    return _respond(quota_service.get_all_configs(), ok_status=200)


@router.post("/api/v1/quota/configs")
@router.post("/api/quota/configs")
async def create_quota_config(
    payload: CreateQuotaConfigRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        quota_service.create_config(
            quota_type=payload.quota_type,
            quota_name=payload.quota_name,
            default_limit=payload.default_limit,
            daily_limit=payload.daily_limit,
            weekly_limit=payload.weekly_limit,
            monthly_limit=payload.monthly_limit,
            is_active=payload.is_active,
            period=payload.period,
            period_days=payload.period_days,
            multi_limits_provided=any(
                value is not None for value in [payload.daily_limit, payload.weekly_limit, payload.monthly_limit]
            ),
        ),
        ok_status=201,
    )


@router.put("/api/v1/quota/configs/{quota_type:path}")
@router.put("/api/quota/configs/{quota_type:path}")
async def update_quota_config(
    quota_type: str,
    payload: UpdateQuotaConfigRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        quota_service.update_config(
            quota_type=quota_type,
            default_limit=payload.default_limit,
            daily_limit=payload.daily_limit,
            weekly_limit=payload.weekly_limit,
            monthly_limit=payload.monthly_limit,
            is_active=payload.is_active,
            period=payload.period,
            period_days=payload.period_days,
            multi_limits_provided=any(
                value is not None for value in [payload.daily_limit, payload.weekly_limit, payload.monthly_limit]
            ),
        ),
        ok_status=200,
    )


@router.post("/api/v1/quota/reset/{target_user_id}/{quota_type:path}")
@router.post("/api/quota/reset/{target_user_id}/{quota_type:path}")
async def reset_user_quota(
    target_user_id: int,
    quota_type: str,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(quota_service.reset_user_quota(user_id=target_user_id, quota_type=quota_type), ok_status=200)


@router.get("/api/v1/quota/users/{target_user_id}")
@router.get("/api/quota/users/{target_user_id}")
async def get_user_quotas(
    target_user_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(quota_service.get_user_quotas(user_id=target_user_id), ok_status=200)
