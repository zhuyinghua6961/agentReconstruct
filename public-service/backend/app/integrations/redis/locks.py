from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any


_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

_EXTEND_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


class RedisLeaseLostError(RuntimeError):
    def __init__(self, *, key: str, label: str) -> None:
        super().__init__(f"{str(label or 'redis_lock')}_lease_lost:{str(key or '')}")
        self.key = str(key or "")
        self.label = str(label or "redis_lock")


@dataclass(frozen=True)
class RedisLockHandle:
    key: str
    token: str
    ttl_seconds: int


class RedisLockManager:
    def __init__(self, client: Any | None) -> None:
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None

    def acquire(self, key: str, *, ttl_seconds: int) -> RedisLockHandle | None:
        if self._client is None:
            return None
        token = secrets.token_hex(16)
        try:
            acquired = self._client.set(
                str(key),
                token,
                ex=max(1, int(ttl_seconds)),
                nx=True,
            )
        except Exception:
            return None
        if not acquired:
            return None
        return RedisLockHandle(
            key=str(key),
            token=token,
            ttl_seconds=max(1, int(ttl_seconds)),
        )

    def _eval(self, script: str, *, keys: list[str], args: list[Any]) -> Any | None:
        if self._client is None:
            return None
        eval_fn = getattr(self._client, "eval", None)
        if not callable(eval_fn):
            return None
        return eval_fn(script, len(keys), *(list(keys) + list(args)))

    def release(self, handle: RedisLockHandle | None) -> bool:
        if self._client is None or handle is None:
            return False
        try:
            released = self._eval(_RELEASE_SCRIPT, keys=[handle.key], args=[handle.token])
            if released is not None:
                return bool(released)
            current = self._client.get(handle.key)
            if isinstance(current, bytes):
                current = current.decode("utf-8")
            if str(current or "") != handle.token:
                return False
            return bool(self._client.delete(handle.key))
        except Exception:
            return False

    def extend(self, handle: RedisLockHandle | None, *, ttl_seconds: int | None = None) -> bool:
        if self._client is None or handle is None:
            return False
        ttl = max(1, int(ttl_seconds or handle.ttl_seconds))
        try:
            extended = self._eval(_EXTEND_SCRIPT, keys=[handle.key], args=[handle.token, ttl])
            if extended is not None:
                return bool(extended)
            current = self._client.get(handle.key)
            if isinstance(current, bytes):
                current = current.decode("utf-8")
            if str(current or "") != handle.token:
                return False
            return bool(self._client.expire(handle.key, ttl))
        except Exception:
            return False


class RedisRenewingLock:
    def __init__(
        self,
        *,
        lock_manager: RedisLockManager,
        handle: RedisLockHandle | None,
        logger: Any | None = None,
        label: str = "redis_lock",
        refresh_interval_seconds: float | None = None,
    ) -> None:
        self._lock_manager = lock_manager
        self._handle = handle
        self._logger = logger
        self._label = str(label or "redis_lock")
        default_interval = max(1.0, float(getattr(handle, "ttl_seconds", 1)) / 3.0)
        self._refresh_interval_seconds = max(0.5, float(refresh_interval_seconds or default_interval))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lost = False

    @property
    def key(self) -> str:
        return str(self._handle.key) if self._handle is not None else ""

    @property
    def token(self) -> str:
        return str(self._handle.token) if self._handle is not None else ""

    @property
    def ttl_seconds(self) -> int:
        return int(self._handle.ttl_seconds) if self._handle is not None else 0

    @property
    def lost(self) -> bool:
        return bool(self._lost)

    def start(self) -> "RedisRenewingLock":
        if self._handle is None or not self._lock_manager.available or self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run,
            name=f"{self._label}-renew",
            daemon=True,
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop_event.wait(self._refresh_interval_seconds):
            if self._handle is None:
                return
            if self._lock_manager.extend(self._handle, ttl_seconds=self._handle.ttl_seconds):
                continue
            self._lost = True
            if self._logger is not None:
                self._logger.warning("%s lease renewal failed: key=%s", self._label, self.key)
            return

    def ensure_healthy(self) -> None:
        if self._handle is None:
            return
        if self._lost:
            raise RedisLeaseLostError(key=self.key, label=self._label)

    def release(self) -> bool:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self._refresh_interval_seconds + 1.0))
        return self._lock_manager.release(self._handle)


__all__ = ["RedisLeaseLostError", "RedisLockHandle", "RedisLockManager", "RedisRenewingLock"]
