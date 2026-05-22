"""Tests QA pipeline Redis cache gate (env ``QA_PIPELINE_CACHE_ENABLED``)."""

from __future__ import annotations


def test_resolve_qa_pipeline_cache_redis_disabled_returns_none(monkeypatch):
    from app.modules.qa_cache.pipeline_cache_flags import resolve_qa_pipeline_cache_redis

    sentinel = object()

    monkeypatch.setenv("QA_PIPELINE_CACHE_ENABLED", "0")
    assert resolve_qa_pipeline_cache_redis(sentinel) is None

    monkeypatch.setenv("QA_PIPELINE_CACHE_ENABLED", "true")
    assert resolve_qa_pipeline_cache_redis(sentinel) is sentinel

    monkeypatch.delenv("QA_PIPELINE_CACHE_ENABLED", raising=False)
    assert resolve_qa_pipeline_cache_redis(sentinel) is sentinel
