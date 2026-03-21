from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.integrations.redis.keys import RedisKeyFactory, build_key_factory


def _decode_if_bytes(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


@dataclass
class RedisService:
    client: Any | None
    key_factory: RedisKeyFactory

    @classmethod
    def from_prefix(cls, *, client: Any | None, key_prefix: str) -> "RedisService":
        return cls(client=client, key_factory=build_key_factory(key_prefix))

    @property
    def available(self) -> bool:
        return self.client is not None

    def prefixed(self, *segments: object) -> str:
        return self.key_factory.join(*segments)

    def get_json(self, key: str, *, default: Any = None) -> Any:
        if self.client is None:
            return default
        try:
            raw = self.client.get(key)
        except Exception:
            return default
        if raw in (None, ""):
            return default
        text = _decode_if_bytes(raw)
        try:
            return json.loads(str(text))
        except Exception:
            return default

    def set_json(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:
        if self.client is None:
            return False
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        try:
            if ttl_seconds is None:
                result = self.client.set(key, payload)
            else:
                result = self.client.set(key, payload, ex=max(1, int(ttl_seconds)))
        except Exception:
            return False
        return bool(result)

    def delete(self, *keys: str) -> int:
        if self.client is None or not keys:
            return 0
        filtered = [str(key) for key in keys if str(key or "").strip()]
        if not filtered:
            return 0
        try:
            return int(self.client.delete(*filtered) or 0)
        except Exception:
            return 0

    def expire(self, key: str, ttl_seconds: int) -> bool:
        if self.client is None:
            return False
        try:
            return bool(self.client.expire(key, max(1, int(ttl_seconds))))
        except Exception:
            return False

    def ttl(self, key: str) -> int | None:
        if self.client is None:
            return None
        try:
            value = self.client.ttl(key)
        except Exception:
            return None
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None
