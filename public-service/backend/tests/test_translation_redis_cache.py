from __future__ import annotations

import pytest

from app.integrations.redis import RedisService
from app.integrations.redis.keys import build_key_factory
from app.modules.documents.translation_cache_impl import TranslationCache
from app.modules.documents import translation_redis_cache as redis_cache


class _FakeRedisClient:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        if nx:
            self.store[key] = value
            if ex is not None:
                self.ttl[key] = int(ex)
            return True
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = int(ex)
        return True

    def delete(self, *keys: str):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttl.pop(key, None)
                removed += 1
        return removed

    def expire(self, key: str, ttl_seconds: int):
        if key not in self.store:
            return False
        self.ttl[key] = int(ttl_seconds)
        return True

    def ttl(self, key: str):
        if key not in self.store:
            return -2
        return self.ttl.get(key, -1)

    def eval(self, script, numkeys, *args):
        _ = script
        key = args[0]
        token = args[1]
        if self.store.get(key) == token:
            return self.delete(key)
        return 0


@pytest.fixture
def redis_service() -> RedisService:
    return RedisService.from_prefix(client=_FakeRedisClient(), key_prefix="test_public_service")


def test_chunk_cache_key_includes_epoch_version_and_profile(monkeypatch, redis_service):
    monkeypatch.setenv("TRANSLATION_CACHE_EPOCH", "7")
    monkeypatch.setenv("TRANSLATION_PROMPT_VERSION", "2")
    text_hash = redis_cache.hash_translation_text("hello", profile="document")
    key = redis_cache.build_chunk_cache_key(
        redis_service=redis_service,
        text_hash=text_hash,
        profile="document",
    )
    assert "translation" in key
    assert "chunk" in key
    assert ":7:" in key
    assert ":2:" in key
    assert ":document:" in key
    assert key.endswith(text_hash)


def test_document_cache_key_changes_with_segment_fingerprint(monkeypatch, redis_service):
    monkeypatch.setenv("TRANSLATION_CACHE_EPOCH", "0")
    monkeypatch.setenv("TRANSLATION_PROMPT_VERSION", "2")
    fp_a = redis_cache.build_segment_fingerprint(["chunk-a", "chunk-b"])
    fp_b = redis_cache.build_segment_fingerprint(["chunk-a", "chunk-c"])
    key_a = redis_cache.build_document_cache_key(
        redis_service=redis_service,
        document_type="doi",
        document_id="10.1000/test",
        segment_fingerprint=fp_a,
    )
    key_b = redis_cache.build_document_cache_key(
        redis_service=redis_service,
        document_type="doi",
        document_id="10.1000/test",
        segment_fingerprint=fp_b,
    )
    assert key_a != key_b


def test_chunk_cache_roundtrip(monkeypatch, redis_service):
    monkeypatch.setenv("TRANSLATION_REDIS_CACHE_ENABLED", "1")
    monkeypatch.setenv("TRANSLATION_REDIS_CHUNK_TTL_SECONDS", "120")
    assert redis_cache.cache_chunk_translation(
        redis_service=redis_service,
        text="hello world",
        translation="你好世界",
        profile="document",
    )
    cached = redis_cache.get_cached_chunk_translation(
        redis_service=redis_service,
        text="hello world",
        profile="document",
    )
    assert cached == "你好世界"


def test_document_cache_roundtrip(monkeypatch, redis_service):
    monkeypatch.setenv("TRANSLATION_REDIS_DOCUMENT_TTL_SECONDS", "600")
    fingerprint = redis_cache.build_segment_fingerprint(["a", "b"])
    assert redis_cache.cache_document_translation(
        redis_service=redis_service,
        document_type="doi",
        document_id="10.1000/example",
        segment_fingerprint=fingerprint,
        translated_text="# 标题\n\n正文",
        segment_count=2,
        truncated=False,
        provider="openai-compatible",
    )
    cached = redis_cache.get_cached_document_translation(
        redis_service=redis_service,
        document_type="doi",
        document_id="10.1000/example",
        segment_fingerprint=fingerprint,
    )
    assert cached is not None
    assert cached["translated_text"] == "# 标题\n\n正文"
    assert cached["segment_count"] == 2


def test_translation_cache_prefers_redis_then_backfills(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSLATION_REDIS_CACHE_ENABLED", "1")
    monkeypatch.setenv("TRANSLATION_PROMPT_VERSION", "2")
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    fake_client = _FakeRedisClient()
    fake_service = RedisService.from_prefix(client=fake_client, key_prefix="test_public_service")

    def _fake_get_service():
        return fake_service

    monkeypatch.setattr(
        "app.modules.documents.translation_cache_impl.get_translation_redis_service",
        _fake_get_service,
    )
    cache = TranslationCache(cache_dir=str(tmp_path / "cache"))

    redis_cache.cache_chunk_translation(
        redis_service=fake_service,
        text="chunk-one",
        translation="片段一",
        profile="document",
    )

    assert cache.get("chunk-one", profile="document") == "片段一"


def test_translation_cache_read_through_from_local_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSLATION_REDIS_CACHE_ENABLED", "1")
    monkeypatch.setenv("TRANSLATION_PROMPT_VERSION", "2")
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    fake_client = _FakeRedisClient()
    fake_service = RedisService.from_prefix(client=fake_client, key_prefix="test_public_service")

    def _fake_get_service():
        return fake_service

    monkeypatch.setattr(
        "app.modules.documents.translation_cache_impl.get_translation_redis_service",
        _fake_get_service,
    )
    cache = TranslationCache(cache_dir=str(tmp_path / "cache"))
    cache.set("chunk-two", "片段二", profile="snippet")

    assert redis_cache.get_cached_chunk_translation(
        redis_service=fake_service,
        text="chunk-two",
        profile="snippet",
    ) == "片段二"


def test_document_lock_prevents_duplicate_acquire(monkeypatch, redis_service):
    monkeypatch.setenv("TRANSLATION_DOCUMENT_LOCK_TTL_SECONDS", "30")
    fingerprint = redis_cache.build_segment_fingerprint(["x"])
    first = redis_cache.try_acquire_document_translation_lock(
        redis_service=redis_service,
        document_type="doi",
        document_id="10.1/abc",
        segment_fingerprint=fingerprint,
    )
    second = redis_cache.try_acquire_document_translation_lock(
        redis_service=redis_service,
        document_type="doi",
        document_id="10.1/abc",
        segment_fingerprint=fingerprint,
    )
    assert first is not None
    assert second is None
    redis_cache.release_document_translation_lock(redis_service=redis_service, handle=first)
