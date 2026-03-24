from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

try:  # pragma: no cover - optional dependency in tests
    import redis as redis_module  # type: ignore
except Exception:  # pragma: no cover
    redis_module = None


@dataclass(frozen=True)
class RedisSettings:
    enabled: bool
    resolved_url: str
    key_prefix: str
    socket_connect_timeout_sec: int
    socket_timeout_sec: int


@dataclass(frozen=True)
class RedisBindings:
    enabled: bool
    available: bool
    client: Any | None
    library_available: bool
    detail: str
    error: str = ""
    url: str = ""
    key_prefix: str = ""


@dataclass(frozen=True)
class RedisKeyFactory:
    prefix: str

    def join(self, *segments: object) -> str:
        items = []
        base = str(self.prefix or "").strip().strip(":")
        if base:
            items.append(base)
        for segment in segments:
            normalized = str(segment or "").strip().strip(":")
            if normalized:
                items.append(normalized)
        return ":".join(items)

    def cache(self, *segments: object) -> str:
        return self.join("cache", *segments)

    def lock(self, *segments: object) -> str:
        return self.join("lock", *segments)


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

    def get_json(self, key: str, *, default: Any = None) -> Any:
        if self.client is None:
            return default
        try:
            raw = self.client.get(key)
        except Exception:
            return default
        if raw in (None, ""):
            return default
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(str(raw))
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
        try:
            return int(self.client.delete(*[str(key) for key in keys if str(key or "").strip()]) or 0)
        except Exception:
            return 0


def _get_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _get_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _resolved_redis_url() -> str:
    explicit = str(os.getenv("REDIS_URL", "") or "").strip()
    if explicit:
        return explicit
    host = str(os.getenv("REDIS_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port = _get_int("REDIS_PORT", 6379, minimum=1, maximum=65535)
    password = str(os.getenv("REDIS_PASSWORD", "123456") or "123456")
    db = _get_int("REDIS_DB", 0, minimum=0, maximum=63)
    return f"redis://:{password}@{host}:{port}/{db}"


def get_redis_settings() -> RedisSettings:
    return RedisSettings(
        enabled=_get_bool("REDIS_ENABLED", False),
        resolved_url=_resolved_redis_url(),
        key_prefix=str(os.getenv("REDIS_KEY_PREFIX", "highthinkingqa") or "highthinkingqa").strip() or "highthinkingqa",
        socket_connect_timeout_sec=_get_int("REDIS_SOCKET_CONNECT_TIMEOUT_SEC", 2, minimum=1, maximum=60),
        socket_timeout_sec=_get_int("REDIS_SOCKET_TIMEOUT_SEC", 2, minimum=1, maximum=60),
    )


def redact_redis_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw or "@" not in raw:
        return raw
    scheme, remainder = raw.split("://", 1) if "://" in raw else ("redis", raw)
    credentials, suffix = remainder.split("@", 1)
    if ":" not in credentials:
        return f"{scheme}://{credentials}@{suffix}"
    username, _password = credentials.split(":", 1)
    return f"{scheme}://{username}:***@{suffix}"


def build_key_factory(prefix: str) -> RedisKeyFactory:
    return RedisKeyFactory(prefix=str(prefix or "").strip().strip(":"))


def build_redis_bindings(*, redis_lib: Any | None = None) -> RedisBindings:
    settings = get_redis_settings()
    safe_url = redact_redis_url(settings.resolved_url)
    if not settings.enabled:
        return RedisBindings(
            enabled=False,
            available=False,
            client=None,
            library_available=redis_module is not None,
            detail="redis disabled by config",
            url=safe_url,
            key_prefix=settings.key_prefix,
        )

    library = redis_module if redis_lib is None else redis_lib
    if library is None:
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=False,
            detail="redis client library unavailable",
            error="python package 'redis' is not installed",
            url=safe_url,
            key_prefix=settings.key_prefix,
        )

    redis_cls = getattr(library, "Redis", None)
    if redis_cls is None or not hasattr(redis_cls, "from_url"):
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis client library unsupported",
            error="redis.Redis.from_url missing",
            url=safe_url,
            key_prefix=settings.key_prefix,
        )

    try:
        client = redis_cls.from_url(
            settings.resolved_url,
            decode_responses=False,
            socket_connect_timeout=settings.socket_connect_timeout_sec,
            socket_timeout=settings.socket_timeout_sec,
        )
        ok = bool(client.ping())
    except Exception as exc:
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis unavailable",
            error=str(exc),
            url=safe_url,
            key_prefix=settings.key_prefix,
        )

    return RedisBindings(
        enabled=True,
        available=ok,
        client=client if ok else None,
        library_available=True,
        detail="redis connected" if ok else "redis ping returned false",
        url=safe_url,
        key_prefix=settings.key_prefix,
    )


@lru_cache(maxsize=1)
def get_redis_service() -> RedisService:
    bindings = build_redis_bindings()
    return RedisService.from_prefix(client=bindings.client, key_prefix=bindings.key_prefix)


def reset_redis_runtime_cache() -> None:
    clear = getattr(get_redis_service, "cache_clear", None)
    if callable(clear):
        clear()


def bootstrap_redis_state(app_state: Any) -> None:
    bindings = build_redis_bindings()
    service = get_redis_service()
    app_state.redis_bindings = bindings
    app_state.redis_service = service
    status = "ok"
    if not bindings.enabled:
        status = "skipped"
    elif not bindings.available:
        status = "degraded"
    component_status = getattr(app_state, "component_status", None)
    if component_status is None:
        component_status = {}
        app_state.component_status = component_status
    component_status["redis"] = {
        "status": status,
        "detail": bindings.detail,
        "error": bindings.error,
        "enabled": bindings.enabled,
        "available": bindings.available,
        "library_available": bindings.library_available,
        "url": bindings.url,
        "key_prefix": bindings.key_prefix,
    }
