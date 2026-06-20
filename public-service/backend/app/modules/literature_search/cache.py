from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Callable

from app.core.config import get_settings
from app.integrations.redis import RedisLockManager, RedisService, build_redis_bindings
from app.modules.literature_search.rerank_service import rerank_configured
from app.modules.qa_cache.metrics import increment_cache_metric

_REDIS_SERVICE: RedisService | None = None
_REDIS_SERVICE_RESOLVED = False
_NAMESPACE = "literature_search"
_DEFAULT_TTL_SECONDS = 259200


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def literature_search_cache_enabled() -> bool:
    return _env_bool("LITERATURE_SEARCH_REDIS_CACHE_ENABLED", True)


def literature_search_cache_epoch() -> str:
    return str(os.getenv("LITERATURE_SEARCH_CACHE_EPOCH", "0") or "0").strip() or "0"


def literature_search_cache_ttl_seconds() -> int:
    raw = str(os.getenv("LITERATURE_SEARCH_CACHE_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS)) or str(_DEFAULT_TTL_SECONDS)).strip()
    try:
        return max(60, int(raw))
    except Exception:
        return _DEFAULT_TTL_SECONDS


def literature_search_cache_lock_enabled() -> bool:
    return _env_bool("LITERATURE_SEARCH_CACHE_LOCK_ENABLED", True)


def _cache_lock_ttl_seconds() -> int:
    raw = str(os.getenv("LITERATURE_SEARCH_CACHE_LOCK_TTL_SECONDS", "30") or "30").strip()
    try:
        return max(1, min(int(raw), 600))
    except Exception:
        return 30


def _cache_wait_ms() -> int:
    raw = str(os.getenv("LITERATURE_SEARCH_CACHE_WAIT_MS", "400") or "400").strip()
    try:
        return max(0, min(int(raw), 5000))
    except Exception:
        return 400


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def normalize_literature_search_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip()).casefold()


def hash_literature_search_query(query: str) -> str:
    normalized = normalize_literature_search_query(query)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _models_hash() -> str:
    payload = "|".join(
        [
            _first_env("QA_EMBEDDING_MODEL", "EMBEDDING_API_MODEL", "EMBEDDING_MODEL_NAME"),
            _first_env("HIGHTHINKINGQA_EMBEDDING_MODEL", default="qwen3-embedding-8b"),
            _first_env("RERANK_MODEL"),
            "1" if rerank_configured() else "0",
        ]
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _normalize_sources(sources: str) -> str:
    value = str(sources or "both").strip().lower()
    if value == "fastqa":
        return "fastqa"
    if value == "fastqa_md":
        return "fastqa_md"
    if value == "highthinking":
        return "highthinking"
    return "fastqa+fastqa_md+highthinking"


def get_literature_search_redis_service() -> RedisService | None:
    global _REDIS_SERVICE, _REDIS_SERVICE_RESOLVED
    if _REDIS_SERVICE_RESOLVED:
        return _REDIS_SERVICE
    _REDIS_SERVICE_RESOLVED = True
    if not literature_search_cache_enabled():
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


def resolve_literature_search_redis_service(runtime: Any | None = None) -> RedisService | None:
    if runtime is not None:
        redis_service = getattr(runtime, "redis_service", None)
        if redis_service is not None and redis_service.available and literature_search_cache_enabled():
            return redis_service
    return get_literature_search_redis_service()


def build_literature_search_cache_key(
    *,
    redis_service: RedisService,
    query: str,
    query_type: str,
    match_mode: str,
    sources: str,
    limit: int,
) -> str:
    return redis_service.key_factory.cache(
        "literature-search",
        literature_search_cache_epoch(),
        str(query_type or "auto").strip().lower(),
        str(match_mode or "semantic").strip().lower(),
        _normalize_sources(sources),
        str(max(1, int(limit or 1))),
        _models_hash(),
        hash_literature_search_query(query),
    )


def build_literature_search_lock_key(
    *,
    redis_service: RedisService,
    query: str,
    query_type: str,
    match_mode: str,
    sources: str,
    limit: int,
) -> str:
    return redis_service.key_factory.lock(
        "literature-search",
        literature_search_cache_epoch(),
        str(query_type or "auto").strip().lower(),
        str(match_mode or "semantic").strip().lower(),
        _normalize_sources(sources),
        str(max(1, int(limit or 1))),
        hash_literature_search_query(query),
    )


def _unwrap_cached_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    cached = payload.get("payload")
    if not isinstance(cached, dict):
        return None
    return dict(cached)


def get_cached_literature_search(
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


def should_cache_literature_search_payload(payload: dict[str, Any]) -> bool:
    code = str(payload.get("code") or "").strip().upper()
    if code in {"EMBEDDING_UNAVAILABLE", "RETRIEVAL_RUNTIME_UNAVAILABLE"}:
        return False
    if payload.get("error") and code:
        return False
    return True


def cache_literature_search(
    *,
    redis_service: RedisService | None,
    cache_key: str,
    payload: dict[str, Any],
    ttl_seconds: int | None = None,
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not should_cache_literature_search_payload(payload):
        return False
    stored_payload = dict(payload)
    stored_payload.pop("cache_meta", None)
    ok = redis_service.set_json(
        cache_key,
        {
            "payload": stored_payload,
            "cache_epoch": literature_search_cache_epoch(),
            "cached_at": time.time(),
        },
        ttl_seconds=ttl_seconds if ttl_seconds is not None else literature_search_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric(_NAMESPACE, "cache_write")
    return ok


def run_literature_search_singleflight(
    *,
    redis_service: RedisService | None,
    lock_key: str,
    read_cached_fn: Callable[[], dict[str, Any] | None],
    compute_fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if redis_service is None or not redis_service.available or not literature_search_cache_lock_enabled():
        increment_cache_metric(_NAMESPACE, "lock_skipped")
        return compute_fn()

    lock_manager = RedisLockManager(redis_service.client)
    if not lock_manager.available:
        increment_cache_metric(_NAMESPACE, "lock_skipped")
        return compute_fn()

    handle = lock_manager.acquire(lock_key, ttl_seconds=_cache_lock_ttl_seconds())
    if handle is not None:
        increment_cache_metric(_NAMESPACE, "lock_acquired")
        try:
            return compute_fn()
        finally:
            lock_manager.release(handle)

    deadline = time.monotonic() + (_cache_wait_ms() / 1000.0)
    poll_seconds = 0.05
    while time.monotonic() < deadline:
        cached = read_cached_fn()
        if cached is not None:
            increment_cache_metric(_NAMESPACE, "lock_wait_hit")
            return cached
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))

    increment_cache_metric(_NAMESPACE, "lock_fallback_compute")
    return compute_fn()


__all__ = [
    "build_literature_search_cache_key",
    "build_literature_search_lock_key",
    "cache_literature_search",
    "get_cached_literature_search",
    "get_literature_search_redis_service",
    "hash_literature_search_query",
    "literature_search_cache_enabled",
    "literature_search_cache_epoch",
    "literature_search_cache_ttl_seconds",
    "resolve_literature_search_redis_service",
    "run_literature_search_singleflight",
    "should_cache_literature_search_payload",
]
