from __future__ import annotations

import os
import time
from typing import Any

from app.integrations.redis import RedisService


def _quota_cache_epoch() -> str:
    return str(os.getenv("QUOTA_CACHE_EPOCH", "0") or "0").strip() or "0"


def _config_ttl_seconds() -> int:
    raw = str(os.getenv("QUOTA_CONFIG_CACHE_TTL_SECONDS", "600") or "600").strip()
    try:
        return max(30, int(raw))
    except Exception:
        return 600


def _active_list_ttl_seconds() -> int:
    raw = str(os.getenv("QUOTA_ACTIVE_LIST_CACHE_TTL_SECONDS", "300") or "300").strip()
    try:
        return max(30, int(raw))
    except Exception:
        return 300


def _all_list_ttl_seconds() -> int:
    raw = str(os.getenv("QUOTA_ALL_LIST_CACHE_TTL_SECONDS", "300") or "300").strip()
    try:
        return max(30, int(raw))
    except Exception:
        return 300


def _override_ttl_seconds() -> int:
    raw = str(os.getenv("QUOTA_OVERRIDE_CACHE_TTL_SECONDS", "600") or "600").strip()
    try:
        return max(30, int(raw))
    except Exception:
        return 600


def build_quota_config_cache_key(*, redis_service: RedisService, quota_type: str) -> str:
    return redis_service.key_factory.cache("quota", "config", _quota_cache_epoch(), str(quota_type or "").strip().lower())


def build_quota_active_configs_cache_key(*, redis_service: RedisService) -> str:
    return redis_service.key_factory.cache("quota", "active-configs", _quota_cache_epoch())


def build_quota_all_configs_cache_key(*, redis_service: RedisService) -> str:
    return redis_service.key_factory.cache("quota", "all-configs", _quota_cache_epoch())


def build_quota_override_cache_key(*, redis_service: RedisService, user_id: int, quota_type: str) -> str:
    return redis_service.key_factory.cache(
        "quota",
        "user-override",
        _quota_cache_epoch(),
        int(user_id),
        str(quota_type or "").strip().lower(),
    )


def get_cached_quota_config(*, redis_service: RedisService | None, quota_type: str) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(build_quota_config_cache_key(redis_service=redis_service, quota_type=quota_type), default=None)
    return payload if isinstance(payload, dict) else None


def cache_quota_config(*, redis_service: RedisService | None, quota_type: str, payload: dict[str, Any] | None) -> bool:
    if redis_service is None or not redis_service.available or payload is None:
        return False
    return redis_service.set_json(
        build_quota_config_cache_key(redis_service=redis_service, quota_type=quota_type),
        payload,
        ttl_seconds=_config_ttl_seconds(),
    )


def get_cached_quota_active_configs(*, redis_service: RedisService | None) -> list[dict[str, Any]] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(build_quota_active_configs_cache_key(redis_service=redis_service), default=None)
    return payload if isinstance(payload, list) else None


def cache_quota_active_configs(*, redis_service: RedisService | None, payload: list[dict[str, Any]]) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    return redis_service.set_json(
        build_quota_active_configs_cache_key(redis_service=redis_service),
        list(payload or []),
        ttl_seconds=_active_list_ttl_seconds(),
    )


def get_cached_quota_all_configs(*, redis_service: RedisService | None) -> list[dict[str, Any]] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(build_quota_all_configs_cache_key(redis_service=redis_service), default=None)
    return payload if isinstance(payload, list) else None


def cache_quota_all_configs(*, redis_service: RedisService | None, payload: list[dict[str, Any]]) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    return redis_service.set_json(
        build_quota_all_configs_cache_key(redis_service=redis_service),
        list(payload or []),
        ttl_seconds=_all_list_ttl_seconds(),
    )


def get_cached_quota_override(*, redis_service: RedisService | None, user_id: int, quota_type: str) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(
        build_quota_override_cache_key(redis_service=redis_service, user_id=user_id, quota_type=quota_type),
        default=None,
    )
    return payload if isinstance(payload, dict) else None


def cache_quota_override(*, redis_service: RedisService | None, user_id: int, quota_type: str, custom_limit: int | None) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    return redis_service.set_json(
        build_quota_override_cache_key(redis_service=redis_service, user_id=user_id, quota_type=quota_type),
        {"custom_limit": custom_limit},
        ttl_seconds=_override_ttl_seconds(),
    )


def invalidate_quota_config_cache(*, redis_service: RedisService | None, quota_type: str) -> int:
    if redis_service is None or not redis_service.available:
        return 0
    return redis_service.delete(build_quota_config_cache_key(redis_service=redis_service, quota_type=quota_type))


def invalidate_quota_config_lists_cache(*, redis_service: RedisService | None) -> int:
    if redis_service is None or not redis_service.available:
        return 0
    return redis_service.delete(
        build_quota_active_configs_cache_key(redis_service=redis_service),
        build_quota_all_configs_cache_key(redis_service=redis_service),
    )


def invalidate_quota_override_cache(*, redis_service: RedisService | None, user_id: int, quota_type: str) -> int:
    if redis_service is None or not redis_service.available:
        return 0
    return redis_service.delete(
        build_quota_override_cache_key(redis_service=redis_service, user_id=user_id, quota_type=quota_type)
    )


def bump_quota_epoch_marker(*, redis_service: RedisService | None) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    return redis_service.set_json(
        redis_service.key_factory.cache("quota", "epoch-marker"),
        {"updated_at_ns": time.time_ns(), "epoch": _quota_cache_epoch()},
    )
