from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.deps import AuthContext
from app.core.errors import AppError
from app.modules.auth.deps import require_admin_context, require_auth_context
from app.modules.conversation.internal_api import InternalAuthorityCaller, require_internal_authority
from app.modules.quota.schemas import (
    CreateQuotaConfigRequest,
    InternalQuotaGrantFinalizeResponse,
    InternalQuotaGrantFinalizeRequest,
    InternalQuotaGrantPrecheckResponse,
    InternalQuotaGrantPrecheckRequest,
    UpdateQuotaConfigRequest,
)
from app.modules.quota import service as quota_service_module


router = APIRouter(tags=["quota"])


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
    if code == "GRANT_ALREADY_ACTIVE":
        return 409
    if code == "DB_UNAVAILABLE":
        return 503
    if code == "QUOTA_EXCEEDED":
        return 429
    if code == "QUOTA_CONFIG_MISSING":
        return 503
    return 500


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(status_code=_status(result, ok_status=ok_status), content=jsonable_encoder(result))


def _require_gateway_internal_caller(caller: InternalAuthorityCaller = Depends(require_internal_authority)) -> InternalAuthorityCaller:
    if str(caller.service_name or "").strip().lower() != "gateway":
        raise AppError(
            message="internal_source_service_forbidden",
            code="INTERNAL_SOURCE_SERVICE_FORBIDDEN",
            status_code=403,
        )
    return caller


@router.get("/api/v1/quota/my")
@router.get("/api/quota/my")
def get_my_quotas(context: AuthContext = Depends(require_auth_context)):
    return _respond(quota_service_module.quota_service.get_user_quotas(user_id=context.user_id), ok_status=200)


@router.get("/api/v1/quota/configs")
@router.get("/api/quota/configs")
def get_quota_configs(_context: AuthContext = Depends(require_admin_context)):
    return _respond(quota_service_module.quota_service.get_all_configs(), ok_status=200)


@router.post("/api/v1/quota/configs")
@router.post("/api/quota/configs")
def create_quota_config(payload: CreateQuotaConfigRequest, _context: AuthContext = Depends(require_admin_context)):
    return _respond(
        quota_service_module.quota_service.create_config(
            quota_type=payload.quota_type,
            quota_name=payload.quota_name,
            default_limit=payload.default_limit,
            daily_limit=payload.daily_limit,
            weekly_limit=payload.weekly_limit,
            monthly_limit=payload.monthly_limit,
            is_active=payload.is_active,
            period=payload.period,
            period_days=payload.period_days,
            multi_limits_provided=any(value is not None for value in [payload.daily_limit, payload.weekly_limit, payload.monthly_limit]),
        ),
        ok_status=201,
    )


@router.put("/api/v1/quota/configs/{quota_type:path}")
@router.put("/api/quota/configs/{quota_type:path}")
def update_quota_config(
    quota_type: str,
    payload: UpdateQuotaConfigRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        quota_service_module.quota_service.update_config(
            quota_type=quota_type,
            default_limit=payload.default_limit,
            daily_limit=payload.daily_limit,
            weekly_limit=payload.weekly_limit,
            monthly_limit=payload.monthly_limit,
            is_active=payload.is_active,
            period=payload.period,
            period_days=payload.period_days,
            multi_limits_provided=any(value is not None for value in [payload.daily_limit, payload.weekly_limit, payload.monthly_limit]),
        ),
        ok_status=200,
    )


@router.post("/api/v1/quota/reset/{target_user_id}/{quota_type:path}")
@router.post("/api/quota/reset/{target_user_id}/{quota_type:path}")
def reset_user_quota(
    target_user_id: int,
    quota_type: str,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(quota_service_module.quota_service.reset_user_quota(user_id=target_user_id, quota_type=quota_type), ok_status=200)


@router.get("/api/v1/quota/users/{target_user_id}")
@router.get("/api/quota/users/{target_user_id}")
def get_user_quotas(target_user_id: int, _context: AuthContext = Depends(require_admin_context)):
    return _respond(quota_service_module.quota_service.get_user_quotas(user_id=target_user_id), ok_status=200)


@router.post("/internal/quota/grants/precheck", response_model=InternalQuotaGrantPrecheckResponse)
def precheck_internal_quota_grant(
    payload: InternalQuotaGrantPrecheckRequest,
    _caller: InternalAuthorityCaller = Depends(_require_gateway_internal_caller),
):
    return _respond(
        quota_service_module.quota_service.create_internal_quota_grant(
            user_id=payload.user_id,
            quota_type=payload.quota_type,
            strict_config=payload.strict_config,
        ),
        ok_status=200,
    )


@router.post("/internal/quota/grants/{grant_id}/finalize", response_model=InternalQuotaGrantFinalizeResponse)
def finalize_internal_quota_grant(
    grant_id: str,
    payload: InternalQuotaGrantFinalizeRequest,
    _caller: InternalAuthorityCaller = Depends(_require_gateway_internal_caller),
):
    return _respond(
        quota_service_module.quota_service.finalize_internal_quota_grant(
            grant_id=grant_id,
            success=payload.success,
        ),
        ok_status=200,
    )
