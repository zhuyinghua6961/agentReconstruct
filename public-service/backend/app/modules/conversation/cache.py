from __future__ import annotations

import os
import time
from typing import Any

from app.integrations.redis import RedisService
from app.modules.qa_cache.metrics import increment_cache_metric


def _list_cache_ttl_seconds() -> int:
    raw = str(os.getenv("CONVERSATION_LIST_CACHE_TTL_SECONDS", "60") or "60").strip()
    try:
        return max(10, int(raw))
    except Exception:
        return 60


def _detail_cache_ttl_seconds() -> int:
    raw = str(os.getenv("CONVERSATION_DETAIL_CACHE_TTL_SECONDS", "30") or "30").strip()
    try:
        return max(10, int(raw))
    except Exception:
        return 30


def _detail_cache_touch_on_hit_enabled() -> bool:
    raw = str(os.getenv("CONVERSATION_DETAIL_CACHE_TOUCH_ON_HIT", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _detail_cache_freshness_grace_seconds() -> int:
    raw = str(os.getenv("CONVERSATION_DETAIL_CACHE_FRESHNESS_GRACE_SECONDS", "1") or "1").strip()
    try:
        return max(0, int(raw))
    except Exception:
        return 1


def _recent_pages_ttl_seconds() -> int:
    raw = str(os.getenv("CONVERSATION_LIST_RECENT_PAGES_TTL_SECONDS", "900") or "900").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 900


def _recent_pages_limit() -> int:
    raw = str(os.getenv("CONVERSATION_LIST_RECENT_PAGES_LIMIT", "8") or "8").strip()
    try:
        return max(1, min(20, int(raw)))
    except Exception:
        return 8


def _list_cache_version_key(*, redis_service: RedisService, user_id: int) -> str:
    return redis_service.key_factory.cache("conversation", "list", "version", int(user_id))


def _list_cache_version(*, redis_service: RedisService | None, user_id: int) -> str:
    if redis_service is None or not redis_service.available:
        return "0"
    payload = redis_service.get_json(_list_cache_version_key(redis_service=redis_service, user_id=user_id), default=None)
    if not isinstance(payload, dict):
        return "0"
    version = str(payload.get("version") or "").strip()
    return version or "0"


def _list_recent_pages_key(*, redis_service: RedisService, user_id: int) -> str:
    return redis_service.key_factory.cache("conversation", "list", "recent-pages", int(user_id))


def build_conversation_list_recent_pages_key(*, redis_service: RedisService, user_id: int) -> str:
    return _list_recent_pages_key(redis_service=redis_service, user_id=user_id)


def _detail_cache_version_key(*, redis_service: RedisService, user_id: int, conversation_id: int) -> str:
    return redis_service.key_factory.cache("conversation", "detail", "version", int(user_id), int(conversation_id))


def _detail_cache_version(*, redis_service: RedisService | None, user_id: int, conversation_id: int) -> str:
    if redis_service is None or not redis_service.available:
        return "0"
    payload = redis_service.get_json(
        _detail_cache_version_key(
            redis_service=redis_service,
            user_id=user_id,
            conversation_id=conversation_id,
        ),
        default=None,
    )
    if not isinstance(payload, dict):
        return "0"
    version = str(payload.get("version") or "").strip()
    return version or "0"


def build_conversation_list_cache_key(
    *,
    redis_service: RedisService,
    user_id: int,
    page: int,
    page_size: int,
) -> str:
    return redis_service.key_factory.cache(
        "conversation",
        "list",
        int(user_id),
        _list_cache_version(redis_service=redis_service, user_id=user_id),
        int(page),
        int(page_size),
    )


def build_conversation_detail_cache_key(
    *,
    redis_service: RedisService,
    user_id: int,
    conversation_id: int,
) -> str:
    return redis_service.key_factory.cache(
        "conversation",
        "detail",
        int(user_id),
        int(conversation_id),
        _detail_cache_version(
            redis_service=redis_service,
            user_id=user_id,
            conversation_id=conversation_id,
        ),
    )


def get_conversation_list_cache_version(
    *,
    redis_service: RedisService | None,
    user_id: int,
) -> str:
    return _list_cache_version(redis_service=redis_service, user_id=user_id)


def get_conversation_detail_cache_version(
    *,
    redis_service: RedisService | None,
    user_id: int,
    conversation_id: int,
) -> str:
    return _detail_cache_version(
        redis_service=redis_service,
        user_id=user_id,
        conversation_id=conversation_id,
    )


def get_conversation_detail_freshness_grace_seconds() -> int:
    return _detail_cache_freshness_grace_seconds()


def get_cached_conversation_list(
    *,
    redis_service: RedisService | None,
    user_id: int,
    page: int,
    page_size: int,
) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(
        build_conversation_list_cache_key(
            redis_service=redis_service,
            user_id=user_id,
            page=page,
            page_size=page_size,
        ),
        default=None,
    )
    if not isinstance(payload, dict):
        return None
    if payload.get("success") is not True:
        return None
    increment_cache_metric("conversation_list", "cache_hit")
    return payload


def cache_conversation_list(
    *,
    redis_service: RedisService | None,
    user_id: int,
    page: int,
    page_size: int,
    payload: dict[str, Any],
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return False
    ok = redis_service.set_json(
        build_conversation_list_cache_key(
            redis_service=redis_service,
            user_id=user_id,
            page=page,
            page_size=page_size,
        ),
        payload,
        ttl_seconds=_list_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("conversation_list", "cache_write")
    return ok


def note_conversation_list_miss() -> None:
    increment_cache_metric("conversation_list", "cache_miss")


def note_conversation_list_access(
    *,
    redis_service: RedisService | None,
    user_id: int,
    page: int,
    page_size: int,
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    key = _list_recent_pages_key(redis_service=redis_service, user_id=user_id)
    payload = redis_service.get_json(key, default=None)
    existing = payload.get("pages") if isinstance(payload, dict) else []
    normalized: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()

    current = (int(page), int(page_size))
    normalized.append({"page": current[0], "page_size": current[1]})
    seen.add(current)

    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            try:
                candidate = (int(item.get("page") or 0), int(item.get("page_size") or 0))
            except Exception:
                continue
            if candidate[0] <= 0 or candidate[1] <= 0 or candidate in seen:
                continue
            normalized.append({"page": candidate[0], "page_size": candidate[1]})
            seen.add(candidate)
            if len(normalized) >= _recent_pages_limit():
                break

    return bool(
        redis_service.set_json(
            key,
            {"pages": normalized},
            ttl_seconds=_recent_pages_ttl_seconds(),
        )
    )


def get_recent_conversation_list_pages(
    *,
    redis_service: RedisService | None,
    user_id: int,
) -> list[dict[str, int]]:
    if redis_service is None or not redis_service.available:
        return []
    payload = redis_service.get_json(
        _list_recent_pages_key(redis_service=redis_service, user_id=user_id),
        default=None,
    )
    pages = payload.get("pages") if isinstance(payload, dict) else []
    result: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    if isinstance(pages, list):
        for item in pages:
            if not isinstance(item, dict):
                continue
            try:
                page = int(item.get("page") or 0)
                page_size = int(item.get("page_size") or 0)
            except Exception:
                continue
            if page <= 0 or page_size <= 0:
                continue
            key = (page, page_size)
            if key in seen:
                continue
            seen.add(key)
            result.append({"page": page, "page_size": page_size})
    return result


def get_cached_conversation_detail(
    *,
    redis_service: RedisService | None,
    user_id: int,
    conversation_id: int,
) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    key = build_conversation_detail_cache_key(
        redis_service=redis_service,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    payload = redis_service.get_json(key, default=None)
    if not isinstance(payload, dict):
        return None
    if payload.get("success") is not True:
        return None
    if _detail_cache_touch_on_hit_enabled():
        if redis_service.expire(key, _detail_cache_ttl_seconds()):
            increment_cache_metric("conversation_detail", "cache_touch")
    increment_cache_metric("conversation_detail", "cache_hit")
    return payload


def cache_conversation_detail(
    *,
    redis_service: RedisService | None,
    user_id: int,
    conversation_id: int,
    payload: dict[str, Any],
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return False
    cache_meta = payload.get("cache_meta") if isinstance(payload.get("cache_meta"), dict) else {}
    payload_to_store = {
        **payload,
        "cache_meta": {
            **cache_meta,
            "cached_at": int(time.time()),
        },
    }
    ok = redis_service.set_json(
        build_conversation_detail_cache_key(
            redis_service=redis_service,
            user_id=user_id,
            conversation_id=conversation_id,
        ),
        payload_to_store,
        ttl_seconds=_detail_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("conversation_detail", "cache_write")
    return ok


def note_conversation_detail_miss() -> None:
    increment_cache_metric("conversation_detail", "cache_miss")


def invalidate_conversation_list_cache(
    *,
    redis_service: RedisService | None,
    user_id: int,
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    ok = redis_service.set_json(
        _list_cache_version_key(redis_service=redis_service, user_id=user_id),
        {"version": str(time.time_ns())},
    )
    if ok:
        increment_cache_metric("conversation_list", "cache_invalidate")
    return ok


def invalidate_conversation_detail_cache(
    *,
    redis_service: RedisService | None,
    user_id: int,
    conversation_id: int,
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    ok = redis_service.set_json(
        _detail_cache_version_key(
            redis_service=redis_service,
            user_id=user_id,
            conversation_id=conversation_id,
        ),
        {"version": str(time.time_ns())},
    )
    if ok:
        increment_cache_metric("conversation_detail", "cache_invalidate")
    return ok


__all__ = [
    "build_conversation_detail_cache_key",
    "build_conversation_list_cache_key",
    "build_conversation_list_recent_pages_key",
    "cache_conversation_detail",
    "cache_conversation_list",
    "get_conversation_detail_cache_version",
    "get_conversation_detail_freshness_grace_seconds",
    "get_conversation_list_cache_version",
    "get_recent_conversation_list_pages",
    "get_cached_conversation_detail",
    "get_cached_conversation_list",
    "invalidate_conversation_detail_cache",
    "invalidate_conversation_list_cache",
    "note_conversation_list_access",
    "note_conversation_detail_miss",
    "note_conversation_list_miss",
]
