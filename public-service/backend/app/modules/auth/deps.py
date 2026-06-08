from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, Query

from app.core.deps import AuthContext
from app.core.errors import AppError, PermissionDeniedError
from app.modules.auth import service as auth_service_module


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


def _disabled_personnel_error(user: dict[str, Any]) -> dict[str, Any] | None:
    build_error = getattr(auth_service_module.auth_service, "build_disabled_personnel_login_error", None)
    if not callable(build_error):
        return None
    result = build_error(user)
    if isinstance(result, dict) and not result.get("success") and result.get("code") == "PERSONNEL_DISABLED":
        return result
    return None


def get_bearer_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
    token: str | None = Query(default=None),
) -> str | None:
    auth_header = str(authorization or "").strip()
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    query_token = str(token or "").strip()
    return query_token or None


def get_optional_auth_context(token: str | None = Depends(get_bearer_token)) -> AuthContext | None:
    if not token:
        return None
    payload = auth_service_module.auth_service.decode_token(token)
    if not payload:
        return None
    user_id = int(payload.get("user_id", 0) or 0)
    if not user_id:
        return None
    try:
        user = auth_service_module.auth_service.get_user_by_id(user_id)
    except Exception as exc:
        if _is_db_unavailable_error(exc):
            raise AppError(message=str(exc), code="DB_UNAVAILABLE", status_code=503) from exc
        raise
    if not user or str(user.get("status") or "") != "active":
        return None
    if _disabled_personnel_error(user):
        return None
    return AuthContext(
        user_id=user_id,
        role=str(user.get("role") or payload.get("role") or "user"),
        username=str(user.get("username") or ""),
    )


def require_auth_context(token: str | None = Depends(get_bearer_token)) -> AuthContext:
    if not token:
        raise AppError(message="token_missing", code="TOKEN_MISSING", status_code=401)
    payload = auth_service_module.auth_service.decode_token(token)
    if not payload:
        raise AppError(message="token_invalid", code="TOKEN_INVALID", status_code=401)
    user_id = int(payload.get("user_id", 0) or 0)
    try:
        user = auth_service_module.auth_service.get_user_by_id(user_id)
    except Exception as exc:
        if _is_db_unavailable_error(exc):
            raise AppError(message=str(exc), code="DB_UNAVAILABLE", status_code=503) from exc
        raise
    if not user:
        raise AppError(message="user_not_found", code="USER_NOT_FOUND", status_code=401)
    if str(user.get("status") or "") != "active":
        raise AppError(message="您的账号已被停用，请联系管理员", code="ACCOUNT_DISABLED", status_code=403)
    disabled_personnel_error = _disabled_personnel_error(user)
    if disabled_personnel_error:
        exc = AppError(
            message=str(disabled_personnel_error.get("error") or "账号所属人员已停用，请联系管理员"),
            code="PERSONNEL_DISABLED",
            status_code=403,
            details=None,
        )
        exc.extra_payload = {"data": disabled_personnel_error.get("data")}
        raise exc
    return AuthContext(
        user_id=user_id,
        role=str(user.get("role") or payload.get("role") or "user"),
        username=str(user.get("username") or ""),
    )


def require_admin_context(context: AuthContext = Depends(require_auth_context)) -> AuthContext:
    role = str(context.role or "").strip().lower()
    if role != "admin":
        raise PermissionDeniedError("admin_required")
    return context
