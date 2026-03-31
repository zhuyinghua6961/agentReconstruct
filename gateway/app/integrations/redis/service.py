from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import RedisSettings
from app.integrations.redis.keys import RedisKeyFactory, build_key_factory


def _decode_if_bytes(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _import_redis_module():
    try:
        import redis  # type: ignore
    except Exception:
        return None
    return redis


@dataclass(frozen=True)
class GatewayRedisRuntimeStatus:
    enabled: bool
    available: bool
    dependency_available: bool
    client_source: str
    key_prefix: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    def probe(self) -> bool:
        if self.client is None:
            return False
        try:
            result = self.client.ping()
        except Exception:
            return False
        return bool(result)

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

    def set_json_if_absent(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:
        if self.client is None:
            return False
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        try:
            kwargs: dict[str, Any] = {"nx": True}
            if ttl_seconds is not None:
                kwargs["ex"] = max(1, int(ttl_seconds))
            result = self.client.set(key, payload, **kwargs)
        except Exception:
            return False
        return bool(result)

    def incr(self, key: str) -> int | None:
        if self.client is None or not str(key or "").strip():
            return None
        try:
            value = self.client.incr(str(key))
        except Exception:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def incrby(self, key: str, amount: int) -> int | None:
        if self.client is None or not str(key or "").strip():
            return None
        try:
            value = self.client.incrby(str(key), int(amount))
        except Exception:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def get_int(self, key: str, *, default: int = 0) -> int:
        if self.client is None or not str(key or "").strip():
            return int(default)
        try:
            raw = self.client.get(str(key))
        except Exception:
            return int(default)
        if raw in (None, ""):
            return int(default)
        try:
            return int(_decode_if_bytes(raw))
        except Exception:
            return int(default)

    def expire(self, key: str, ttl_seconds: int) -> bool:
        if self.client is None or not str(key or "").strip():
            return False
        try:
            result = self.client.expire(str(key), max(1, int(ttl_seconds)))
        except Exception:
            return False
        return bool(result)

    def rpush_json(self, key: str, value: Any) -> int | None:
        if self.client is None or not str(key or "").strip():
            return None
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        try:
            result = self.client.rpush(str(key), payload)
        except Exception:
            return None
        try:
            return int(result)
        except Exception:
            return None

    def lrange_json(self, key: str, *, start: int = 0, stop: int = -1) -> list[Any]:
        if self.client is None or not str(key or "").strip():
            return []
        try:
            values = self.client.lrange(str(key), int(start), int(stop))
        except Exception:
            return []
        output: list[Any] = []
        for item in values:
            text = _decode_if_bytes(item)
            try:
                output.append(json.loads(str(text)))
            except Exception:
                continue
        return output

    def compare_and_swap_json(
        self,
        key: str,
        *,
        expected_value: Any,
        new_value: Any,
        ttl_seconds: int | None = None,
    ) -> bool:
        if self.client is None or not str(key or "").strip():
            return False
        if not hasattr(self.client, "pipeline"):
            return self.set_json(str(key), new_value, ttl_seconds=ttl_seconds)

        expected_payload = json.dumps(expected_value, ensure_ascii=False, separators=(",", ":"))
        new_payload = json.dumps(new_value, ensure_ascii=False, separators=(",", ":"))
        try:
            with self.client.pipeline() as pipe:
                pipe.watch(str(key))
                current = pipe.get(str(key))
                current_payload = "" if current in (None, "") else str(_decode_if_bytes(current))
                if current_payload != expected_payload:
                    pipe.unwatch()
                    return False
                pipe.multi()
                if ttl_seconds is None:
                    pipe.set(str(key), new_payload)
                else:
                    pipe.set(str(key), new_payload, ex=max(1, int(ttl_seconds)))
                pipe.execute()
                return True
        except Exception:
            return False

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

    def ttl(self, key: str) -> int | None:
        if self.client is None or not str(key or "").strip():
            return None
        try:
            value = self.client.ttl(str(key))
        except Exception:
            return None
        try:
            ttl_value = int(value)
        except Exception:
            return None
        return ttl_value if ttl_value >= 0 else None

    def scan_keys(self, pattern: str) -> list[str]:
        if self.client is None or not str(pattern or "").strip():
            return []
        try:
            if hasattr(self.client, "scan_iter"):
                values = list(self.client.scan_iter(match=str(pattern)))
            elif hasattr(self.client, "keys"):
                values = list(self.client.keys(str(pattern)))
            else:
                return []
        except Exception:
            return []
        output: list[str] = []
        for value in values:
            normalized = _decode_if_bytes(value)
            text = str(normalized or "").strip()
            if text:
                output.append(text)
        return output

    def sadd(self, key: str, *values: object) -> int:
        if self.client is None or not str(key or "").strip():
            return 0
        normalized = [str(value or "").strip() for value in values if str(value or "").strip()]
        if not normalized:
            return 0
        try:
            return int(self.client.sadd(str(key), *normalized) or 0)
        except Exception:
            return 0

    def srem(self, key: str, *values: object) -> int:
        if self.client is None or not str(key or "").strip():
            return 0
        normalized = [str(value or "").strip() for value in values if str(value or "").strip()]
        if not normalized:
            return 0
        try:
            return int(self.client.srem(str(key), *normalized) or 0)
        except Exception:
            return 0

    def scard(self, key: str) -> int:
        if self.client is None or not str(key or "").strip():
            return 0
        try:
            return int(self.client.scard(str(key)) or 0)
        except Exception:
            return 0

    def smembers(self, key: str) -> list[str]:
        if self.client is None or not str(key or "").strip():
            return []
        try:
            values = self.client.smembers(str(key))
        except Exception:
            return []
        output: list[str] = []
        for value in values:
            normalized = _decode_if_bytes(value)
            text = str(normalized or "").strip()
            if text:
                output.append(text)
        return sorted(output)

    def zadd(self, key: str, mapping: dict[str, float | int]) -> int:
        if self.client is None or not str(key or "").strip():
            return 0
        normalized = {
            str(member or "").strip(): float(score)
            for member, score in mapping.items()
            if str(member or "").strip()
        }
        if not normalized:
            return 0
        try:
            return int(self.client.zadd(str(key), normalized) or 0)
        except Exception:
            return 0

    def zrem(self, key: str, *members: object) -> int:
        if self.client is None or not str(key or "").strip():
            return 0
        normalized = [str(member or "").strip() for member in members if str(member or "").strip()]
        if not normalized:
            return 0
        try:
            return int(self.client.zrem(str(key), *normalized) or 0)
        except Exception:
            return 0

    def zcard(self, key: str) -> int:
        if self.client is None or not str(key or "").strip():
            return 0
        try:
            return int(self.client.zcard(str(key)) or 0)
        except Exception:
            return 0

    def zrange(self, key: str, *, start: int = 0, stop: int = -1, withscores: bool = False) -> list[Any]:
        if self.client is None or not str(key or "").strip():
            return []
        try:
            values = self.client.zrange(str(key), int(start), int(stop), withscores=withscores)
        except Exception:
            return []
        output: list[Any] = []
        for value in values:
            if withscores and isinstance(value, tuple) and len(value) == 2:
                member = str(_decode_if_bytes(value[0]) or "").strip()
                if member:
                    output.append((member, float(value[1])))
                continue
            member = str(_decode_if_bytes(value) or "").strip()
            if member:
                output.append(member)
        return output

    def zrangebyscore(self, key: str, *, min_score: float, max_score: float) -> list[str]:
        if self.client is None or not str(key or "").strip():
            return []
        try:
            values = self.client.zrangebyscore(str(key), float(min_score), float(max_score))
        except Exception:
            return []
        output: list[str] = []
        for value in values:
            member = str(_decode_if_bytes(value) or "").strip()
            if member:
                output.append(member)
        return output


@dataclass(frozen=True)
class GatewayRedisRuntime:
    client: Any | None
    service: RedisService
    status: GatewayRedisRuntimeStatus


def _build_client(redis_settings: RedisSettings) -> tuple[Any | None, GatewayRedisRuntimeStatus]:
    if not redis_settings.enabled:
        return None, GatewayRedisRuntimeStatus(
            enabled=False,
            available=False,
            dependency_available=False,
            client_source="disabled",
            key_prefix=redis_settings.key_prefix,
        )

    redis_module = _import_redis_module()
    if redis_module is None:
        return None, GatewayRedisRuntimeStatus(
            enabled=True,
            available=False,
            dependency_available=False,
            client_source="missing_dependency",
            key_prefix=redis_settings.key_prefix,
            error="redis_dependency_missing",
        )

    try:
        if redis_settings.url:
            client = redis_module.Redis.from_url(
                redis_settings.url,
                db=redis_settings.db,
                username=redis_settings.username or None,
                password=redis_settings.password or None,
                socket_connect_timeout=redis_settings.socket_connect_timeout_seconds,
                socket_timeout=redis_settings.socket_timeout_seconds,
            )
            client_source = "url"
        else:
            client = redis_module.Redis(
                host=redis_settings.host,
                port=redis_settings.port,
                db=redis_settings.db,
                username=redis_settings.username or None,
                password=redis_settings.password or None,
                socket_connect_timeout=redis_settings.socket_connect_timeout_seconds,
                socket_timeout=redis_settings.socket_timeout_seconds,
            )
            client_source = "host_port"
        client.ping()
    except Exception as exc:
        return None, GatewayRedisRuntimeStatus(
            enabled=True,
            available=False,
            dependency_available=True,
            client_source="error",
            key_prefix=redis_settings.key_prefix,
            error=str(exc),
        )

    return client, GatewayRedisRuntimeStatus(
        enabled=True,
        available=True,
        dependency_available=True,
        client_source=client_source,
        key_prefix=redis_settings.key_prefix,
    )


def bootstrap_redis_runtime(redis_settings: RedisSettings) -> GatewayRedisRuntime:
    client, status = _build_client(redis_settings)
    return GatewayRedisRuntime(
        client=client,
        service=RedisService.from_prefix(client=client, key_prefix=redis_settings.key_prefix),
        status=status,
    )


__all__ = [
    "GatewayRedisRuntime",
    "GatewayRedisRuntimeStatus",
    "RedisService",
    "bootstrap_redis_runtime",
]
