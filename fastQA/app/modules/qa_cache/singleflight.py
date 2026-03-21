from __future__ import annotations

import os
import time
from typing import Any, Callable

from app.integrations.redis import RedisLockManager, RedisService
from app.modules.qa_cache.metrics import increment_cache_metric


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _cache_lock_enabled() -> bool:
    return _env_bool("QA_CACHE_LOCK_ENABLED", True)


def _cache_wait_ms() -> int:
    raw = str(os.getenv("QA_CACHE_WAIT_MS", "400") or "400").strip()
    try:
        return max(0, min(int(raw), 5000))
    except Exception:
        return 400


def _cache_lock_ttl_seconds() -> int:
    raw = str(os.getenv("QA_CACHE_LOCK_TTL_SECONDS", "30") or "30").strip()
    try:
        return max(1, min(int(raw), 600))
    except Exception:
        return 30


def run_singleflight(
    *,
    redis_service: RedisService | None,
    lock_key: str,
    namespace: str,
    read_cached_fn: Callable[[], Any],
    compute_fn: Callable[[], Any],
) -> Any:
    if redis_service is None or not redis_service.available or not _cache_lock_enabled():
        increment_cache_metric(namespace, "lock_skipped")
        return compute_fn()

    lock_manager = RedisLockManager(redis_service.client)
    if not lock_manager.available:
        increment_cache_metric(namespace, "lock_skipped")
        return compute_fn()

    handle = lock_manager.acquire(lock_key, ttl_seconds=_cache_lock_ttl_seconds())
    if handle is not None:
        increment_cache_metric(namespace, "lock_acquired")
        try:
            return compute_fn()
        finally:
            lock_manager.release(handle)

    deadline = time.monotonic() + (_cache_wait_ms() / 1000.0)
    poll_seconds = 0.05
    while time.monotonic() < deadline:
        cached = read_cached_fn()
        if cached is not None:
            increment_cache_metric(namespace, "lock_wait_hit")
            return cached
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))

    increment_cache_metric(namespace, "lock_fallback_compute")
    return compute_fn()

