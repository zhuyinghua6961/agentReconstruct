from __future__ import annotations

import json
import os
from typing import Any

from app.core.config import get_settings
from app.integrations.redis import RedisLockManager, RedisService, build_redis_bindings


_REDIS_SERVICE: RedisService | None = None
_REDIS_SERVICE_RESOLVED = False


def resolve_usage_stats_redis_service() -> RedisService | None:
    global _REDIS_SERVICE, _REDIS_SERVICE_RESOLVED
    if _REDIS_SERVICE_RESOLVED:
        return _REDIS_SERVICE
    _REDIS_SERVICE_RESOLVED = True
    enabled = str(os.getenv("REDIS_ENABLED", "0") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        _REDIS_SERVICE = None
        return None
    bindings = build_redis_bindings(settings=get_settings())
    if bindings.client is None:
        _REDIS_SERVICE = None
        return None
    prefix = str(os.getenv("REDIS_KEY_PREFIX", "public_service") or "public_service").strip() or "public_service"
    _REDIS_SERVICE = RedisService.from_prefix(client=bindings.client, key_prefix=prefix)
    return _REDIS_SERVICE


def resolve_usage_stats_lock_manager() -> RedisLockManager:
    redis_service = resolve_usage_stats_redis_service()
    client = getattr(redis_service, "client", None)
    return RedisLockManager(client)
