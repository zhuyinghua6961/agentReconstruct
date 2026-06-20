from __future__ import annotations

import pytest

from app.integrations.redis import RedisService
from app.modules.documents import literature_content_cache as content_cache


class _FakeRedisClient:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = int(ex)
        return True


@pytest.fixture
def redis_service() -> RedisService:
    return RedisService.from_prefix(client=_FakeRedisClient(), key_prefix="test_public_service")


def test_literature_content_cache_key_uses_normalized_doi(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_CONTENT_CACHE_EPOCH", "0")
    key_a = content_cache.build_literature_content_cache_key(
        redis_service=redis_service,
        normalized_doi="10.1000/test",
    )
    key_b = content_cache.build_literature_content_cache_key(
        redis_service=redis_service,
        normalized_doi="10.1000/test",
    )
    assert key_a == key_b
    assert "literature-content" in key_a
    assert key_a.endswith("10.1000/test")


def test_literature_content_cache_ttl_defaults_to_three_days(monkeypatch):
    monkeypatch.delenv("LITERATURE_CONTENT_CACHE_TTL_SECONDS", raising=False)
    assert content_cache.literature_content_cache_ttl_seconds() == 259200


def test_literature_content_cache_roundtrip(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_CONTENT_CACHE_TTL_SECONDS", "259200")
    key = content_cache.build_literature_content_cache_key(
        redis_service=redis_service,
        normalized_doi="10.1000/test",
    )
    payload = {"doi": "10.1000/test", "title": "A paper", "content": "hello"}
    assert content_cache.cache_literature_content(redis_service=redis_service, cache_key=key, payload=payload)
    cached = content_cache.get_cached_literature_content(redis_service=redis_service, cache_key=key)
    assert cached is not None
    assert cached["title"] == "A paper"
    assert cached["cache_meta"]["hit"] is True


def test_literature_content_skips_large_payload(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_CONTENT_CACHE_MAX_BYTES", "64")
    key = content_cache.build_literature_content_cache_key(
        redis_service=redis_service,
        normalized_doi="10.1000/big",
    )
    payload = {"doi": "10.1000/big", "content": "x" * 200}
    assert content_cache.cache_literature_content(redis_service=redis_service, cache_key=key, payload=payload) is False
    assert content_cache.get_cached_literature_content(redis_service=redis_service, cache_key=key) is None


def test_literature_content_caches_not_found(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_CONTENT_CACHE_TTL_SECONDS", "259200")
    key = content_cache.build_literature_content_cache_key(
        redis_service=redis_service,
        normalized_doi="10.1000/missing",
    )
    payload = {"error": "未找到该文献"}
    assert content_cache.cache_literature_content(redis_service=redis_service, cache_key=key, payload=payload)
    cached = content_cache.get_cached_literature_content(redis_service=redis_service, cache_key=key)
    assert cached is not None
    assert cached["error"] == "未找到该文献"


def test_should_not_cache_runtime_unavailable():
    assert content_cache.should_cache_literature_content_payload(
        {"success": False, "code": "RETRIEVAL_RUNTIME_UNAVAILABLE", "error": "x"}
    ) is False
