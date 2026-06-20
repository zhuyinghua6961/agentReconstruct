from __future__ import annotations

import pytest
from unittest.mock import patch

from app.integrations.redis import RedisService
from app.modules.literature_search import cache as literature_cache
from app.modules.literature_search.service import LiteratureSearchService


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

    def delete(self, *keys: str):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttl.pop(key, None)
                removed += 1
        return removed

    def eval(self, script, numkeys, *args):
        _ = script, numkeys
        key = args[0]
        token = args[1]
        if self.store.get(key) == token:
            return self.delete(key)
        return 0


@pytest.fixture
def redis_service() -> RedisService:
    return RedisService.from_prefix(client=_FakeRedisClient(), key_prefix="test_public_service")


def test_literature_search_cache_key_stable_for_same_query(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_SEARCH_CACHE_EPOCH", "0")
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-local")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_MODEL", "qwen3-embedding-8b")
    monkeypatch.delenv("RERANK_BASE_URL", raising=False)
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    key_a = literature_cache.build_literature_search_cache_key(
        redis_service=redis_service,
        query="LiFePO4",
        query_type="title",
        match_mode="semantic",
        sources="both",
        limit=20,
    )
    key_b = literature_cache.build_literature_search_cache_key(
        redis_service=redis_service,
        query="  lifePO4  ",
        query_type="title",
        match_mode="semantic",
        sources="both",
        limit=20,
    )
    assert key_a == key_b


def test_literature_search_cache_key_differs_by_sources_and_limit(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_SEARCH_CACHE_EPOCH", "0")
    base = dict(
        redis_service=redis_service,
        query="battery",
        query_type="title",
        match_mode="semantic",
        sources="both",
        limit=20,
    )
    key_both = literature_cache.build_literature_search_cache_key(**base)
    key_fastqa = literature_cache.build_literature_search_cache_key(**{**base, "sources": "fastqa"})
    key_limit = literature_cache.build_literature_search_cache_key(**{**base, "limit": 5})
    assert key_both != key_fastqa
    assert key_both != key_limit


def test_literature_search_cache_key_changes_when_model_changes(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_SEARCH_CACHE_EPOCH", "0")
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-local")
    key_a = literature_cache.build_literature_search_cache_key(
        redis_service=redis_service,
        query="battery",
        query_type="title",
        match_mode="semantic",
        sources="fastqa",
        limit=10,
    )
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-v2")
    key_b = literature_cache.build_literature_search_cache_key(
        redis_service=redis_service,
        query="battery",
        query_type="title",
        match_mode="semantic",
        sources="fastqa",
        limit=10,
    )
    assert key_a != key_b


def test_literature_search_cache_ttl_defaults_to_three_days(monkeypatch):
    monkeypatch.delenv("LITERATURE_SEARCH_CACHE_TTL_SECONDS", raising=False)
    assert literature_cache.literature_search_cache_ttl_seconds() == 259200


def test_should_not_cache_embedding_unavailable():
    assert literature_cache.should_cache_literature_search_payload(
        {"code": "EMBEDDING_UNAVAILABLE", "items": [], "error": "x"}
    ) is False


def test_cache_roundtrip_adds_cache_meta(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_SEARCH_CACHE_TTL_SECONDS", "259200")
    key = literature_cache.build_literature_search_cache_key(
        redis_service=redis_service,
        query="battery",
        query_type="title",
        match_mode="semantic",
        sources="fastqa",
        limit=5,
    )
    payload = {"items": [{"doi": "10.1000/a"}], "count": 1}
    assert literature_cache.cache_literature_search(redis_service=redis_service, cache_key=key, payload=payload)
    cached = literature_cache.get_cached_literature_search(redis_service=redis_service, cache_key=key)
    assert cached is not None
    assert cached["count"] == 1
    assert cached["cache_meta"]["hit"] is True


def test_search_uses_cache_without_recomputing():
    service = LiteratureSearchService()
    cached_payload = {
        "items": [{"doi": "10.1000/a"}],
        "count": 1,
        "query_type_detected": "title",
        "query": "battery",
        "sources": ["fastqa"],
        "rerank": {"enabled": False, "applied": False, "fallback": False},
    }
    fake_redis = type("FakeRedis", (), {"available": True})()

    with patch(
        "app.modules.literature_search.service.resolve_literature_search_redis_service",
        return_value=fake_redis,
    ), patch(
        "app.modules.literature_search.service.build_literature_search_cache_key",
        return_value="cache-key",
    ), patch(
        "app.modules.literature_search.service.get_cached_literature_search",
        return_value=cached_payload,
    ), patch.object(
        service,
        "_search_uncached",
    ) as mock_uncached:
        payload, status = service.search(
            query="battery",
            query_type="title",
            match_mode="semantic",
            sources="fastqa",
            limit=5,
            agent=None,
            logger=None,
            runtime=None,
        )
    assert status == 200
    assert payload["count"] == 1
    mock_uncached.assert_not_called()


def test_singleflight_lock_holder_computes_once(monkeypatch, redis_service):
    monkeypatch.setenv("LITERATURE_SEARCH_CACHE_LOCK_ENABLED", "1")
    calls = {"count": 0}

    def _compute() -> dict[str, object]:
        calls["count"] += 1
        return {"items": [], "count": 0}

    lock_key = literature_cache.build_literature_search_lock_key(
        redis_service=redis_service,
        query="battery",
        query_type="title",
        match_mode="semantic",
        sources="fastqa",
        limit=5,
    )
    result = literature_cache.run_literature_search_singleflight(
        redis_service=redis_service,
        lock_key=lock_key,
        read_cached_fn=lambda: None,
        compute_fn=_compute,
    )
    assert result == {"items": [], "count": 0}
    assert calls["count"] == 1
