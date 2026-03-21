from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any


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
            acquired = self._client.set(str(key), token, ex=max(1, int(ttl_seconds)), nx=True)
        except Exception:
            return None
        if not acquired:
            return None
        return RedisLockHandle(key=str(key), token=token, ttl_seconds=max(1, int(ttl_seconds)))

    def release(self, handle: RedisLockHandle | None) -> bool:
        if self._client is None or handle is None:
            return False
        try:
            current = self._client.get(handle.key)
        except Exception:
            return False
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if str(current or "") != handle.token:
            return False
        try:
            return bool(self._client.delete(handle.key))
        except Exception:
            return False
