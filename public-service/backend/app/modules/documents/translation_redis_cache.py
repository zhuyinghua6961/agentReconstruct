from __future__ import annotations

import hashlib
import os
import time
from typing import Any

from app.core.config import get_settings
from app.integrations.redis import RedisLockHandle, RedisLockManager, RedisService, build_redis_bindings
from app.modules.qa_cache.metrics import increment_cache_metric

_REDIS_SERVICE: RedisService | None = None
_REDIS_SERVICE_RESOLVED = False


def _translation_cache_epoch() -> str:
    return str(os.getenv("TRANSLATION_CACHE_EPOCH", "0") or "0").strip() or "0"


def translation_prompt_version() -> str:
    return str(os.getenv("TRANSLATION_PROMPT_VERSION", "2") or "2").strip() or "2"


def _normalize_profile(profile: str | None) -> str:
    normalized = str(profile or "snippet").strip().lower()
    return normalized if normalized in {"snippet", "document"} else "snippet"


def translation_redis_cache_enabled() -> bool:
    return str(os.getenv("TRANSLATION_REDIS_CACHE_ENABLED", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _chunk_ttl_seconds() -> int:
    raw = str(os.getenv("TRANSLATION_REDIS_CHUNK_TTL_SECONDS", "604800") or "604800").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 604800


def _document_ttl_seconds() -> int:
    raw = str(os.getenv("TRANSLATION_REDIS_DOCUMENT_TTL_SECONDS", "604800") or "604800").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 604800


def document_lock_ttl_seconds() -> int:
    raw = str(os.getenv("TRANSLATION_DOCUMENT_LOCK_TTL_SECONDS", "1800") or "1800").strip()
    try:
        return max(30, int(raw))
    except Exception:
        return 1800


def document_lock_wait_seconds() -> float:
    raw = str(os.getenv("TRANSLATION_DOCUMENT_LOCK_WAIT_SECONDS", "30") or "30").strip()
    try:
        return max(0.0, float(raw))
    except Exception:
        return 30.0


def hash_translation_text(text: str, *, profile: str = "snippet") -> str:
    normalized_profile = _normalize_profile(profile)
    payload = f"{translation_prompt_version()}:{normalized_profile}:{text}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def build_segment_fingerprint(segments: list[str]) -> str:
    joined = "\n---\n".join(str(item or "") for item in segments)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()


def get_translation_redis_service() -> RedisService | None:
    global _REDIS_SERVICE, _REDIS_SERVICE_RESOLVED
    if _REDIS_SERVICE_RESOLVED:
        return _REDIS_SERVICE
    _REDIS_SERVICE_RESOLVED = True
    if not translation_redis_cache_enabled():
        _REDIS_SERVICE = None
        return None
    try:
        settings = get_settings()
        bindings = build_redis_bindings(settings=settings)
        _REDIS_SERVICE = RedisService.from_prefix(
            client=bindings.client,
            key_prefix=str(settings.redis_key_prefix or "agentcode"),
        )
        if not _REDIS_SERVICE.available:
            _REDIS_SERVICE = None
    except Exception:
        _REDIS_SERVICE = None
    return _REDIS_SERVICE


def build_chunk_cache_key(
    *,
    redis_service: RedisService,
    text_hash: str,
    profile: str,
) -> str:
    return redis_service.key_factory.cache(
        "translation",
        "chunk",
        _translation_cache_epoch(),
        translation_prompt_version(),
        _normalize_profile(profile),
        str(text_hash or "").strip(),
    )


def build_document_cache_key(
    *,
    redis_service: RedisService,
    document_type: str,
    document_id: str,
    segment_fingerprint: str,
) -> str:
    return redis_service.key_factory.cache(
        "translation",
        "doc",
        _translation_cache_epoch(),
        translation_prompt_version(),
        str(document_type or "").strip().lower(),
        str(document_id or "").strip(),
        str(segment_fingerprint or "").strip(),
    )


def build_document_lock_key(
    *,
    redis_service: RedisService,
    document_type: str,
    document_id: str,
    segment_fingerprint: str,
) -> str:
    return redis_service.key_factory.lock(
        "translation",
        "doc",
        _translation_cache_epoch(),
        translation_prompt_version(),
        str(document_type or "").strip().lower(),
        str(document_id or "").strip(),
        str(segment_fingerprint or "").strip(),
    )


def get_cached_chunk_translation(
    *,
    redis_service: RedisService | None,
    text: str,
    profile: str = "snippet",
) -> str | None:
    if redis_service is None or not redis_service.available:
        return None
    text_hash = hash_translation_text(text, profile=profile)
    key = build_chunk_cache_key(redis_service=redis_service, text_hash=text_hash, profile=profile)
    payload = redis_service.get_json(key, default=None)
    if not isinstance(payload, dict):
        return None
    translation = payload.get("translation")
    if not isinstance(translation, str) or not translation:
        return None
    increment_cache_metric("translation", "cache_hit")
    return translation


def cache_chunk_translation(
    *,
    redis_service: RedisService | None,
    text: str,
    translation: str,
    profile: str = "snippet",
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    normalized = str(translation or "").strip()
    if not normalized:
        return False
    text_hash = hash_translation_text(text, profile=profile)
    key = build_chunk_cache_key(redis_service=redis_service, text_hash=text_hash, profile=profile)
    ok = redis_service.set_json(
        key,
        {
            "translation": normalized,
            "profile": _normalize_profile(profile),
            "version": translation_prompt_version(),
            "cache_epoch": _translation_cache_epoch(),
            "text_hash": text_hash,
        },
        ttl_seconds=_chunk_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("translation", "cache_write")
    return ok


def get_cached_document_translation(
    *,
    redis_service: RedisService | None,
    document_type: str,
    document_id: str,
    segment_fingerprint: str,
) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    key = build_document_cache_key(
        redis_service=redis_service,
        document_type=document_type,
        document_id=document_id,
        segment_fingerprint=segment_fingerprint,
    )
    payload = redis_service.get_json(key, default=None)
    if not isinstance(payload, dict):
        return None
    translated_text = payload.get("translated_text")
    if not isinstance(translated_text, str) or not translated_text.strip():
        return None
    increment_cache_metric("translation", "document_hit")
    return payload


def cache_document_translation(
    *,
    redis_service: RedisService | None,
    document_type: str,
    document_id: str,
    segment_fingerprint: str,
    translated_text: str,
    segment_count: int,
    truncated: bool,
    provider: str = "",
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    text = str(translated_text or "").strip()
    if not text:
        return False
    key = build_document_cache_key(
        redis_service=redis_service,
        document_type=document_type,
        document_id=document_id,
        segment_fingerprint=segment_fingerprint,
    )
    ok = redis_service.set_json(
        key,
        {
            "translated_text": text,
            "segment_count": int(segment_count),
            "truncated": bool(truncated),
            "provider": str(provider or ""),
            "version": translation_prompt_version(),
            "cache_epoch": _translation_cache_epoch(),
            "cached_at": time.time(),
        },
        ttl_seconds=_document_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("translation", "document_write")
    return ok


def try_acquire_document_translation_lock(
    *,
    redis_service: RedisService | None,
    document_type: str,
    document_id: str,
    segment_fingerprint: str,
) -> RedisLockHandle | None:
    if redis_service is None or not redis_service.available:
        return None
    lock_key = build_document_lock_key(
        redis_service=redis_service,
        document_type=document_type,
        document_id=document_id,
        segment_fingerprint=segment_fingerprint,
    )
    lock_manager = RedisLockManager(redis_service.client)
    return lock_manager.acquire(lock_key, ttl_seconds=document_lock_ttl_seconds())


def release_document_translation_lock(
    *,
    redis_service: RedisService | None,
    handle: RedisLockHandle | None,
) -> bool:
    if redis_service is None or not redis_service.available or handle is None:
        return False
    lock_manager = RedisLockManager(redis_service.client)
    return lock_manager.release(handle)


def wait_for_cached_document_translation(
    *,
    redis_service: RedisService | None,
    document_type: str,
    document_id: str,
    segment_fingerprint: str,
    wait_seconds: float | None = None,
) -> dict[str, Any] | None:
    deadline = time.time() + (document_lock_wait_seconds() if wait_seconds is None else max(0.0, float(wait_seconds)))
    while time.time() < deadline:
        cached = get_cached_document_translation(
            redis_service=redis_service,
            document_type=document_type,
            document_id=document_id,
            segment_fingerprint=segment_fingerprint,
        )
        if cached is not None:
            return cached
        time.sleep(0.2)
    return None


__all__ = [
    "build_chunk_cache_key",
    "build_document_cache_key",
    "build_document_lock_key",
    "build_segment_fingerprint",
    "cache_chunk_translation",
    "cache_document_translation",
    "document_lock_ttl_seconds",
    "document_lock_wait_seconds",
    "get_cached_chunk_translation",
    "get_cached_document_translation",
    "get_translation_redis_service",
    "hash_translation_text",
    "release_document_translation_lock",
    "translation_prompt_version",
    "translation_redis_cache_enabled",
    "try_acquire_document_translation_lock",
    "wait_for_cached_document_translation",
]
