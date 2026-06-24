from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

from server.patent.browse_query import resolve_query_type

_LOGGER = logging.getLogger("patent.browse_search_cache")

_NAMESPACE = "patent_search"
_DEFAULT_TTL_SECONDS = 259200
_REDIS_CLIENT: Any | None = None
_REDIS_RESOLVED = False


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def patent_search_cache_enabled() -> bool:
    return _env_bool("PATENT_SEARCH_REDIS_CACHE_ENABLED", True)


def patent_search_cache_epoch() -> str:
    return str(os.getenv("PATENT_SEARCH_CACHE_EPOCH", "0") or "0").strip() or "0"


def patent_search_cache_ttl_seconds() -> int:
    raw = str(os.getenv("PATENT_SEARCH_CACHE_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS)) or str(_DEFAULT_TTL_SECONDS)).strip()
    try:
        return max(60, int(raw))
    except Exception:
        return _DEFAULT_TTL_SECONDS


def patent_search_cache_lock_enabled() -> bool:
    return _env_bool("PATENT_SEARCH_CACHE_LOCK_ENABLED", True)


def _cache_lock_ttl_seconds() -> int:
    raw = str(os.getenv("PATENT_SEARCH_CACHE_LOCK_TTL_SECONDS", "30") or "30").strip()
    try:
        return max(1, min(int(raw), 600))
    except Exception:
        return 30


def _cache_wait_ms() -> int:
    raw = str(os.getenv("PATENT_SEARCH_CACHE_WAIT_MS", "400") or "400").strip()
    try:
        return max(0, min(int(raw), 5000))
    except Exception:
        return 400


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def normalize_patent_search_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip()).casefold()


def hash_patent_search_query(query: str) -> str:
    normalized = normalize_patent_search_query(query)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _models_hash() -> str:
    from server.patent.browse_rerank import patent_browse_rerank_enabled

    payload = "|".join(
        [
            _first_env("EMBEDDING_API_MODEL", "EMBEDDING_MODEL_NAME"),
            _first_env("EMBEDDING_MODEL_TYPE", default="remote"),
            _first_env("RERANK_MODEL", "PATENT_STAGE2_RERANK_MODEL"),
            "1" if patent_browse_rerank_enabled() else "0",
        ]
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _normalize_sources(sources: str) -> str:
    value = str(sources or "both").strip().lower()
    if value in {"abstract", "chunk", "both"}:
        return value
    return "both"


def build_patent_search_cache_key(
    *,
    query: str,
    query_type: str,
    sources: str,
    limit: int,
) -> str:
    resolved_type = resolve_query_type(query=query, query_type=query_type)
    parts = [
        _NAMESPACE,
        patent_search_cache_epoch(),
        resolved_type,
        _normalize_sources(sources),
        str(max(1, min(int(limit or 20), 50))),
        _models_hash(),
        hash_patent_search_query(query),
    ]
    return ":".join(parts)


def build_patent_search_lock_key(
    *,
    query: str,
    query_type: str,
    sources: str,
    limit: int,
) -> str:
    resolved_type = resolve_query_type(query=query, query_type=query_type)
    parts = [
        f"{_NAMESPACE}:lock",
        patent_search_cache_epoch(),
        resolved_type,
        _normalize_sources(sources),
        str(max(1, min(int(limit or 20), 50))),
        hash_patent_search_query(query),
    ]
    return ":".join(parts)


def _get_redis_client() -> Any | None:
    global _REDIS_CLIENT, _REDIS_RESOLVED
    if _REDIS_RESOLVED:
        return _REDIS_CLIENT
    _REDIS_RESOLVED = True
    if not patent_search_cache_enabled():
        _LOGGER.info("patent_search cache disabled reason=PATENT_SEARCH_REDIS_CACHE_ENABLED")
        _REDIS_CLIENT = None
        return None
    try:
        from server.services.redis_client import bootstrap_redis_state
        from types import SimpleNamespace

        state = SimpleNamespace()
        bootstrap_redis_state(state)
        bindings = getattr(state, "redis_bindings", None)
        client = getattr(bindings, "client", None) if bindings is not None else None
        _REDIS_CLIENT = client
        if client is None:
            _LOGGER.warning("patent_search cache unavailable reason=redis_client_missing")
        else:
            _LOGGER.info("patent_search cache redis_ready key_prefix=%s", os.getenv("PATENT_REDIS_KEY_PREFIX", "patent"))
    except Exception as exc:
        _LOGGER.warning(
            "patent_search cache unavailable reason=bootstrap_failed error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


def _cache_payload_key(cache_key: str) -> str:
    prefix = str(os.getenv("PATENT_REDIS_KEY_PREFIX", os.getenv("REDIS_KEY_PREFIX", "patent")) or "patent").strip() or "patent"
    return f"{prefix}:{cache_key}"


def get_patent_search_cache(cache_key: str) -> dict[str, Any] | None:
    client = _get_redis_client()
    if client is None:
        return None
    redis_key = _cache_payload_key(cache_key)
    try:
        raw = client.get(redis_key)
    except Exception as exc:
        _LOGGER.warning(
            "patent_search cache_get failed redis_key=%s error_type=%s error=%s",
            redis_key,
            type(exc).__name__,
            exc,
        )
        return None
    if not raw:
        _LOGGER.debug("patent_search cache_get miss redis_key=%s", redis_key)
        return None
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception as exc:
        _LOGGER.warning(
            "patent_search cache_get decode_failed redis_key=%s error_type=%s error=%s",
            redis_key,
            type(exc).__name__,
            exc,
        )
        return None
    if not isinstance(payload, dict):
        _LOGGER.warning("patent_search cache_get invalid_payload redis_key=%s payload_type=%s", redis_key, type(payload).__name__)
        return None
    payload = dict(payload)
    cache_meta = dict(payload.get("cache_meta") or {})
    cache_meta["hit"] = True
    payload["cache_meta"] = cache_meta
    _LOGGER.info(
        "patent_search cache_get hit redis_key=%s count=%s cached_at=%s",
        redis_key,
        payload.get("count", 0),
        cache_meta.get("cached_at"),
    )
    return payload


def set_patent_search_cache(cache_key: str, payload: dict[str, Any]) -> None:
    client = _get_redis_client()
    if client is None:
        return
    if not isinstance(payload, dict):
        _LOGGER.warning("patent_search cache_set skipped reason=invalid_payload_type payload_type=%s", type(payload).__name__)
        return
    if payload.get("code") in {"EMBEDDING_UNAVAILABLE", "RETRIEVAL_RUNTIME_UNAVAILABLE"}:
        _LOGGER.info(
            "patent_search cache_set skipped reason=error_code code=%s cache_key=%s",
            payload.get("code"),
            cache_key,
        )
        return
    if payload.get("error") and not list(payload.get("items") or []):
        _LOGGER.info(
            "patent_search cache_set skipped reason=empty_error_result cache_key=%s error=%s",
            cache_key,
            payload.get("error"),
        )
        return
    to_store = dict(payload)
    to_store["cache_meta"] = {"hit": False, "cached_at": int(time.time())}
    redis_key = _cache_payload_key(cache_key)
    try:
        client.set(
            redis_key,
            json.dumps(to_store, ensure_ascii=False),
            ex=patent_search_cache_ttl_seconds(),
        )
        _LOGGER.info(
            "patent_search cache_set ok redis_key=%s count=%s ttl_seconds=%s backend=%s rerank_applied=%s",
            redis_key,
            to_store.get("count", 0),
            patent_search_cache_ttl_seconds(),
            to_store.get("retrieval_backend", ""),
            dict(to_store.get("rerank") or {}).get("applied"),
        )
    except Exception as exc:
        _LOGGER.warning(
            "patent_search cache_set failed redis_key=%s error_type=%s error=%s",
            redis_key,
            type(exc).__name__,
            exc,
        )
        return


def run_patent_search_cache_singleflight(
    *,
    query: str,
    query_type: str,
    sources: str,
    limit: int,
    compute: Any,
) -> dict[str, Any]:
    cache_key = build_patent_search_cache_key(
        query=query,
        query_type=query_type,
        sources=sources,
        limit=limit,
    )
    cached = get_patent_search_cache(cache_key)
    if isinstance(cached, dict):
        return cached

    client = _get_redis_client()
    lock_key = build_patent_search_lock_key(
        query=query,
        query_type=query_type,
        sources=sources,
        limit=limit,
    )
    acquired = False
    if client is not None and patent_search_cache_lock_enabled():
        try:
            acquired = bool(client.set(_cache_payload_key(lock_key), "1", ex=_cache_lock_ttl_seconds(), nx=True))
        except Exception:
            acquired = False
        if not acquired:
            deadline = time.time() + (_cache_wait_ms() / 1000.0)
            while time.time() < deadline:
                cached = get_patent_search_cache(cache_key)
                if isinstance(cached, dict):
                    return cached
                time.sleep(0.02)

    payload, _status = compute()
    if isinstance(payload, dict):
        set_patent_search_cache(cache_key, payload)
    if acquired and client is not None:
        try:
            client.delete(_cache_payload_key(lock_key))
        except Exception:
            pass
    return dict(payload) if isinstance(payload, dict) else {"items": [], "count": 0}
