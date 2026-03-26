from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from server.patent.cache_keys import PatentKeyFactory


@dataclass(frozen=True)
class LockHandle:
    key: str
    token: str
    ttl_seconds: int


class ExecutionLockManager:
    def __init__(self, client: Any | None, *, key_factory: PatentKeyFactory | None = None) -> None:
        self._client = client
        self._key_factory = key_factory or PatentKeyFactory(env="dev")
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    def acquire_conversation_lock(self, conversation_id: int, *, ttl_seconds: int) -> LockHandle | None:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return None
        key = self._key_factory.conversation_lock(conversation_id)
        token = secrets.token_hex(16)
        acquired = self._client.set(key, token, ex=max(1, int(ttl_seconds)), nx=True)
        if not acquired:
            self.last_error = "conversation lock already held"
            return None
        self.last_error = ""
        return LockHandle(key=key, token=token, ttl_seconds=max(1, int(ttl_seconds)))

    def release(self, key: str, token: str) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        compare_delete = getattr(self._client, "compare_delete", None)
        if not callable(compare_delete):
            self.last_error = "atomic compare_delete helper unavailable"
            return False
        try:
            released = bool(compare_delete(key, token))
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_error = "" if released else "lock release rejected"
        return released

    def renew(self, key: str, token: str, *, ttl_seconds: int) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        compare_expire = getattr(self._client, "compare_expire", None)
        if not callable(compare_expire):
            self.last_error = "atomic compare_expire helper unavailable"
            return False
        try:
            renewed = bool(compare_expire(key, token, max(1, int(ttl_seconds))))
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_error = "" if renewed else "lock renew rejected"
        return renewed
