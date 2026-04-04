from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import get_settings
from server.patent.cache_keys import PatentKeyFactory

try:  # pragma: no cover
    import redis as redis_module  # type: ignore
except Exception:  # pragma: no cover
    redis_module = None


_COMPARE_DELETE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
""".strip()

_COMPARE_EXPIRE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
""".strip()

_COMPARE_SET_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
    if ARGV[1] ~= '' then
        return 0
    end
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
if current == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0
""".strip()



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



def _register_compare_helpers(client: Any) -> bool:
    compare_delete = getattr(client, "compare_delete", None)
    compare_expire = getattr(client, "compare_expire", None)
    compare_set = getattr(client, "compare_set", None)
    if callable(compare_delete) and callable(compare_expire) and callable(compare_set):
        return True

    register_script = getattr(client, "register_script", None)
    if callable(register_script):
        delete_script = register_script(_COMPARE_DELETE_SCRIPT)
        expire_script = register_script(_COMPARE_EXPIRE_SCRIPT)
        set_script = register_script(_COMPARE_SET_SCRIPT)

        def compare_delete_impl(key: str, token: str) -> int:
            return int(delete_script(keys=[str(key)], args=[str(token)]))

        def compare_expire_impl(key: str, token: str, ttl_seconds: int) -> int:
            ttl = max(1, int(ttl_seconds))
            return int(expire_script(keys=[str(key)], args=[str(token), ttl]))

        def compare_set_impl(key: str, expected: str, replacement: str, ttl_seconds: int) -> int:
            ttl = max(1, int(ttl_seconds))
            return int(set_script(keys=[str(key)], args=[str(expected or ""), str(replacement), ttl]))

        setattr(client, "compare_delete", compare_delete_impl)
        setattr(client, "compare_expire", compare_expire_impl)
        setattr(client, "compare_set", compare_set_impl)
        return True

    eval_fn = getattr(client, "eval", None)
    if callable(eval_fn):

        def compare_delete_impl(key: str, token: str) -> int:
            return int(eval_fn(_COMPARE_DELETE_SCRIPT, 1, str(key), str(token)))

        def compare_expire_impl(key: str, token: str, ttl_seconds: int) -> int:
            ttl = max(1, int(ttl_seconds))
            return int(eval_fn(_COMPARE_EXPIRE_SCRIPT, 1, str(key), str(token), ttl))

        def compare_set_impl(key: str, expected: str, replacement: str, ttl_seconds: int) -> int:
            ttl = max(1, int(ttl_seconds))
            return int(eval_fn(_COMPARE_SET_SCRIPT, 1, str(key), str(expected or ""), str(replacement), ttl))

        setattr(client, "compare_delete", compare_delete_impl)
        setattr(client, "compare_expire", compare_expire_impl)
        setattr(client, "compare_set", compare_set_impl)
        return True

    return False



def build_redis_bindings(*, redis_lib: Any | None = None) -> RedisBindings:
    settings = get_settings()
    safe_url = redact_redis_url(settings.redis.url)
    key_prefix = str(settings.redis.key_prefix or "").strip() or "patent"

    if not settings.redis.enabled:
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
            settings.redis.url,
            decode_responses=False,
            socket_connect_timeout=settings.redis.socket_connect_timeout_sec,
            socket_timeout=settings.redis.socket_timeout_sec,
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

    if not ok:
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis ping returned false",
            url=safe_url,
            key_prefix=key_prefix,
        )

    try:
        compare_ready = _register_compare_helpers(client)
    except Exception as exc:
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis atomic compare helper registration failed",
            error=str(exc),
            url=safe_url,
            key_prefix=key_prefix,
        )

    if not compare_ready:
        return RedisBindings(
            enabled=True,
            available=False,
            client=None,
            library_available=True,
            detail="redis client missing atomic compare support",
            error="redis client must support register_script or eval for compare helpers",
            url=safe_url,
            key_prefix=key_prefix,
        )

    return RedisBindings(
        enabled=True,
        available=True,
        client=client,
        library_available=True,
        detail="redis connected",
        url=safe_url,
        key_prefix=key_prefix,
    )



def bootstrap_redis_state(app_state: Any, *, redis_lib: Any | None = None) -> None:
    settings = get_settings()
    bindings = build_redis_bindings(redis_lib=redis_lib)
    app_state.redis_bindings = bindings
    app_state.redis_key_factory = PatentKeyFactory(env=settings.runtime_env, prefix=bindings.key_prefix)
    component_status = dict(getattr(app_state, "component_status", {}) or {})
    component_status["redis"] = {
        "ready": bool(bindings.available),
        "enabled": bool(bindings.enabled),
        "detail": bindings.detail,
        "error": bindings.error,
        "url": bindings.url,
        "key_prefix": bindings.key_prefix,
    }
    app_state.component_status = component_status
