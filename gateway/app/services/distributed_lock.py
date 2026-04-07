from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.integrations.redis.service import RedisService


_MEMORY_LOCK_GUARD = threading.Lock()
_MEMORY_LOCKS: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class DistributedLockHandle:
    key: str
    value: dict[str, Any]


class DistributedLockManager:
    def __init__(self, *, redis_service: RedisService) -> None:
        self.redis_service = redis_service

    def acquire(
        self,
        *segments: object,
        owner: str,
        ttl_seconds: int = 5,
        wait_timeout_seconds: float = 2.0,
        retry_interval_seconds: float = 0.01,
    ) -> DistributedLockHandle | None:
        key = self.redis_service.key_factory.lock(*segments)
        value = {
            "owner": str(owner or "").strip(),
            "token": uuid4().hex,
        }
        deadline = time.monotonic() + max(0.0, float(wait_timeout_seconds))
        while True:
            if self._try_acquire(key=key, value=value, ttl_seconds=ttl_seconds):
                return DistributedLockHandle(key=key, value=value)
            if time.monotonic() >= deadline:
                return None
            time.sleep(max(0.001, float(retry_interval_seconds)))

    def release(self, handle: DistributedLockHandle | None) -> bool:
        if handle is None:
            return False
        return self._release(key=handle.key, value=handle.value)

    def _try_acquire(self, *, key: str, value: dict[str, Any], ttl_seconds: int) -> bool:
        if self.redis_service.available:
            return self.redis_service.set_json_if_absent(
                key,
                value,
                ttl_seconds=max(1, int(ttl_seconds)),
            )
        with _MEMORY_LOCK_GUARD:
            current = _MEMORY_LOCKS.get(key)
            now = time.monotonic()
            if isinstance(current, dict) and float(current.get("expires_at") or 0.0) > now:
                return False
            _MEMORY_LOCKS[key] = {
                "value": dict(value),
                "expires_at": now + max(1, int(ttl_seconds)),
            }
            return True

    def _release(self, *, key: str, value: dict[str, Any]) -> bool:
        if self.redis_service.available:
            return self.redis_service.delete_if_json_matches(key, expected_value=value)
        with _MEMORY_LOCK_GUARD:
            current = _MEMORY_LOCKS.get(key)
            if not isinstance(current, dict) or dict(current.get("value") or {}) != dict(value or {}):
                return False
            _MEMORY_LOCKS.pop(key, None)
            return True
