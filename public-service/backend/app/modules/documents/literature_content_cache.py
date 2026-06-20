from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from app.core.config import get_settings
from app.integrations.redis import RedisService, build_redis_bindings
from app.modules.qa_cache.metrics import increment_cache_metric

_LOGGER = logging.getLogger(__name__)
_REDIS_SERVICE: RedisService | None = None
_REDIS_SERVICE_RESOLVED = False
_NAMESPACE = "literature_content"
_DEFAULT_TTL_SECONDS = 259200
_DEFAULT_MAX_BYTES = 524288


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def literature_content_cache_enabled() -> bool:
    return _env_bool("LITERATURE_CONTENT_REDIS_CACHE_ENABLED", True)


def literature_content_cache_epoch() -> str:
    return str(os.getenv("LITERATURE_CONTENT_CACHE_EPOCH", "0") or "0").strip() or "0"


def literature_content_cache_ttl_seconds() -> int:
    raw = str(os.getenv("LITERATURE_CONTENT_CACHE_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS)) or str(_DEFAULT_TTL_SECONDS)).strip()
    try:
        return max(60, int(raw))
    except Exception:
        return _DEFAULT_TTL_SECONDS


def literature_content_cache_max_bytes() -> int:
    raw = str(os.getenv("LITERATURE_CONTENT_CACHE_MAX_BYTES", str(_DEFAULT_MAX_BYTES)) or str(_DEFAULT_MAX_BYTES)).strip()
    try:
        return max(1, int(raw))
    except Exception:
        return _DEFAULT_MAX_BYTES


def get_literature_content_redis_service() -> RedisService | None:
    global _REDIS_SERVICE, _REDIS_SERVICE_RESOLVED
    if _REDIS_SERVICE_RESOLVED:
        return _REDIS_SERVICE
    _REDIS_SERVICE_RESOLVED = True
    if not literature_content_cache_enabled():
        _REDIS_SERVICE = None
        return None
    try:
        settings = get_settings()
        bindings = build_redis_bindings(settings=settings)
        _REDIS_SERVICE = RedisService.from_prefix(
            client=bindings.client,
            key_prefix=str(settings.redis_key_prefix or "agentcode"),
        )
        if not _REDIS_SERVICE.available:
            _REDIS_SERVICE = None
    except Exception:
        _REDIS_SERVICE = None
    return _REDIS_SERVICE


def resolve_literature_content_redis_service(runtime: Any | None = None) -> RedisService | None:
    if runtime is not None:
        redis_service = getattr(runtime, "redis_service", None)
        if redis_service is not None and redis_service.available and literature_content_cache_enabled():
            return redis_service
    return get_literature_content_redis_service()


def build_literature_content_cache_key(*, redis_service: RedisService, normalized_doi: str) -> str:
    return redis_service.key_factory.cache(
        "literature-content",
        literature_content_cache_epoch(),
        str(normalized_doi or "").strip(),
    )


def _unwrap_cached_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    cached = payload.get("payload")
    if not isinstance(cached, dict):
        return None
    return dict(cached)


def get_cached_literature_content(
    *,
    redis_service: RedisService | None,
    cache_key: str,
) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(cache_key, default=None)
    cached = _unwrap_cached_payload(payload)
    if cached is None:
        increment_cache_metric(_NAMESPACE, "cache_miss")
        return None
    increment_cache_metric(_NAMESPACE, "cache_hit")
    response = dict(cached)
    cache_meta = dict(response.get("cache_meta") or {})
    cache_meta["hit"] = True
    if isinstance(payload, dict) and payload.get("cached_at"):
        cache_meta["cached_at"] = payload.get("cached_at")
    response["cache_meta"] = cache_meta
    return response


def _payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def should_cache_literature_content_payload(payload: dict[str, Any]) -> bool:
    code = str(payload.get("code") or "").strip().upper()
    if code in {"RETRIEVAL_RUNTIME_UNAVAILABLE"}:
        return False
    if payload.get("success") is False and code:
        return False
    return True


def cache_literature_content(
    *,
    redis_service: RedisService | None,
    cache_key: str,
    payload: dict[str, Any],
    ttl_seconds: int | None = None,
    logger: Any | None = None,
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not should_cache_literature_content_payload(payload):
        return False
    stored_payload = dict(payload)
    stored_payload.pop("cache_meta", None)
    size_bytes = _payload_size_bytes(stored_payload)
    if size_bytes > literature_content_cache_max_bytes():
        active_logger = logger or _LOGGER
        active_logger.debug(
            "literature_content cache skipped: payload too large (%s bytes > %s)",
            size_bytes,
            literature_content_cache_max_bytes(),
        )
        increment_cache_metric(_NAMESPACE, "cache_skip_large")
        return False
    ok = redis_service.set_json(
        cache_key,
        {
            "payload": stored_payload,
            "cache_epoch": literature_content_cache_epoch(),
            "cached_at": time.time(),
        },
        ttl_seconds=ttl_seconds if ttl_seconds is not None else literature_content_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric(_NAMESPACE, "cache_write")
    return ok


__all__ = [
    "build_literature_content_cache_key",
    "cache_literature_content",
    "get_cached_literature_content",
    "get_literature_content_redis_service",
    "literature_content_cache_enabled",
    "literature_content_cache_epoch",
    "literature_content_cache_max_bytes",
    "literature_content_cache_ttl_seconds",
    "resolve_literature_content_redis_service",
    "should_cache_literature_content_payload",
]
