from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Header
from itsdangerous import URLSafeTimedSerializer
from itsdangerous.exc import BadData

from config import get_settings
from server.errors import codes
from server.errors.core import APIError


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    bearer_token: str
    payload: dict[str, Any]



def get_bearer_token(authorization: str | None = Header(default=None, alias="Authorization")) -> str | None:
    auth_header = str(authorization or "").strip()
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise APIError(
            code=codes.TOKEN_INVALID,
            message="authorization header must be a bearer token",
            status_code=401,
            error="token_invalid",
            retriable=False,
        )
    return parts[1].strip()



def _build_serializer() -> tuple[URLSafeTimedSerializer, tuple[str, ...], int]:
    settings = get_settings()
    secret = settings.auth.jwt_secret
    if not secret:
        raise APIError(
            code=codes.SERVICE_NOT_READY,
            message="JWT_SECRET is required for forwarded auth validation",
            status_code=503,
            error="service_not_ready",
            retriable=False,
        )
    salts = ("highthinking.auth.access", *settings.auth.jwt_compatible_access_salts)
    return URLSafeTimedSerializer(secret), tuple(dict.fromkeys(salts)), settings.auth.jwt_expire_seconds



def _decode_token(token: str) -> dict[str, Any]:
    serializer, salts, expire_seconds = _build_serializer()

    for salt in salts:
        try:
            payload = serializer.loads(token, salt=salt, max_age=expire_seconds)
        except BadData:
            continue
        if isinstance(payload, dict):
            return payload

    raise APIError(
        code=codes.TOKEN_INVALID,
        message="authorization token is invalid",
        status_code=401,
        error="token_invalid",
        retriable=False,
    )



def _coerce_positive_user_id(value: Any) -> int:
    try:
        user_id = int(value)
    except (TypeError, ValueError) as exc:
        raise APIError(
            code=codes.TOKEN_INVALID,
            message="forwarded auth user_id is invalid",
            status_code=401,
            error="token_invalid",
            retriable=False,
        ) from exc
    if user_id <= 0:
        raise APIError(
            code=codes.TOKEN_INVALID,
            message="forwarded auth user_id must be positive",
            status_code=401,
            error="token_invalid",
            retriable=False,
        )
    return user_id



def derive_user_id(payload: dict[str, Any]) -> int:
    for key in ("user_id", "uid", "sub"):
        if key in payload:
            return _coerce_positive_user_id(payload.get(key))
    raise APIError(
        code=codes.TOKEN_INVALID,
        message="unable to derive user_id from forwarded authorization",
        status_code=401,
        error="token_invalid",
        retriable=False,
    )



def require_auth_context(authorization: str | None = Header(default=None, alias="Authorization")) -> AuthContext:
    token = get_bearer_token(authorization)
    if not token:
        raise APIError(
            code=codes.TOKEN_MISSING,
            message="authorization header is required",
            status_code=401,
            error="token_missing",
            retriable=False,
        )
    payload = _decode_token(token)
    user_id = derive_user_id(payload)
    return AuthContext(user_id=user_id, bearer_token=token, payload=payload)
