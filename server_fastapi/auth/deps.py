"""FastAPI auth dependency helpers."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Query

from server.errors.core import APIError
from server.services.auth_service import auth_service


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    role: str
    username: str


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
    payload = auth_service.decode_token(token)
    if not payload:
        return None
    user_id = int(payload.get("user_id", 0) or 0)
    if not user_id:
        return None
    user = auth_service.get_user_by_id(user_id)
    if not user or str(user.get("status") or "") != "active":
        return None
    return AuthContext(
        user_id=user_id,
        role=str(user.get("role") or payload.get("role") or "user"),
        username=str(user.get("username") or ""),
    )


def require_auth_context(token: str | None = Depends(get_bearer_token)) -> AuthContext:
    if not token:
        raise APIError(message="token_missing", code="TOKEN_MISSING", status_code=401)
    payload = auth_service.decode_token(token)
    if not payload:
        raise APIError(message="token_invalid", code="TOKEN_INVALID", status_code=401)
    user_id = int(payload.get("user_id", 0) or 0)
    user = auth_service.get_user_by_id(user_id)
    if not user:
        raise APIError(message="user_not_found", code="USER_NOT_FOUND", status_code=401)
    if str(user.get("status") or "") != "active":
        raise APIError(message="account_disabled", code="ACCOUNT_DISABLED", status_code=403)
    return AuthContext(
        user_id=user_id,
        role=str(user.get("role") or payload.get("role") or "user"),
        username=str(user.get("username") or ""),
    )


def require_admin_context(context: AuthContext = Depends(require_auth_context)) -> AuthContext:
    if str(context.role or "").strip().lower() != "admin":
        raise APIError(message="admin_required", code="PERMISSION_DENIED", status_code=403)
    return context
