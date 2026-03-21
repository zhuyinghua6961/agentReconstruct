from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings

try:  # pragma: no cover
    import redis as redis_module  # type: ignore
except Exception:  # pragma: no cover
    redis_module = None


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


def build_redis_bindings(
    *,
    settings: Settings,
    redis_lib: Any | None = None,
) -> RedisBindings:
    resolved_url = settings.resolved_redis_url
    safe_url = redact_redis_url(resolved_url)
    key_prefix = str(settings.redis_key_prefix or "").strip()

    if not settings.redis_enabled:
        return RedisBindings(
            enabled=False,
            available=False,
            client=None,
            library_available=redis_module is not None,
            detail="redis disabled by config",
            url=safe_url,
            key_prefix=key_prefix,
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
            key_prefix=key_prefix,
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
            key_prefix=key_prefix,
        )

    try:
        client = redis_cls.from_url(
            resolved_url,
            decode_responses=False,
            socket_connect_timeout=settings.redis_socket_connect_timeout_sec,
            socket_timeout=settings.redis_socket_timeout_sec,
        )
    except Exception as exc:
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis client construction failed",
            error=str(exc),
            url=safe_url,
            key_prefix=key_prefix,
        )

    ping = getattr(client, "ping", None)
    if not callable(ping):
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis client missing ping support",
            error="redis client has no ping method",
            url=safe_url,
            key_prefix=key_prefix,
        )

    try:
        ok = bool(ping())
    except Exception as exc:
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis unavailable",
            error=str(exc),
            url=safe_url,
            key_prefix=key_prefix,
        )

    return RedisBindings(
        enabled=True,
        available=ok,
        client=client if ok else None,
        library_available=True,
        detail="redis connected" if ok else "redis ping returned false",
        url=safe_url,
        key_prefix=key_prefix,
    )
