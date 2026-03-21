from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.core.runtime import PublicServiceRuntime
from app.integrations.redis import RedisService
from app.modules.auth.service import AuthService, auth_service
from app.modules.quota.service import QuotaService, quota_service


@dataclass
class AuthContext:
    user_id: int
    role: str
    username: str = ""


def get_runtime(request: Request) -> PublicServiceRuntime:
    return request.app.state.runtime


def get_auth_service(request: Request) -> AuthService:
    return getattr(request.app.state, "auth_service", None) or auth_service


def get_quota_service(request: Request) -> QuotaService:
    return getattr(request.app.state, "quota_service", None) or quota_service


def get_redis_service(request: Request) -> RedisService | None:
    return getattr(request.app.state, "redis_service", None)
