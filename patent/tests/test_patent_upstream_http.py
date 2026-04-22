from __future__ import annotations

import config as patent_config

from server.patent.upstream_http import PatentSharedUpstreamHttpProvider


def test_shared_upstream_provider_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "false")

    provider = PatentSharedUpstreamHttpProvider.from_env()

    assert provider.enabled is False
    assert provider.client() is None
    provider.close()


def test_shared_upstream_provider_reuses_same_client_and_parses_limits(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")
    monkeypatch.setenv("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "123")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "11")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_CONNECTIONS", "22")

    provider = PatentSharedUpstreamHttpProvider.from_env()
    first = provider.client()
    second = provider.client()

    assert provider.enabled is True
    assert provider.keepalive_expiry_seconds == 123.0
    assert provider.max_keepalive_connections == 11
    assert provider.max_connections == 22
    assert first is not None
    assert first is second
    provider.close()


def test_shared_upstream_provider_can_be_built_from_settings(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")
    monkeypatch.setenv("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "123")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "11")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_CONNECTIONS", "22")

    settings = patent_config.get_settings()
    builder = getattr(PatentSharedUpstreamHttpProvider, "from_settings", None)

    assert callable(builder)
    provider = builder(settings)

    assert provider.enabled is True
    assert provider.keepalive_expiry_seconds == 123.0
    assert provider.max_keepalive_connections == 11
    assert provider.max_connections == 22
    provider.close()


def test_shared_upstream_provider_snapshot_exposes_runtime_metadata(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")
    monkeypatch.setenv("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "123")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "11")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_CONNECTIONS", "22")

    provider = PatentSharedUpstreamHttpProvider.from_env()
    snapshot_fn = getattr(provider, "snapshot", None)

    assert callable(snapshot_fn)
    snapshot = snapshot_fn()

    assert snapshot["pool_owner"] == "app"
    assert snapshot["client_owner"] == "shared"
    assert snapshot["shared_client_id"]
    assert snapshot["pid"] > 0
    assert snapshot["bootstrap_source"] == "startup"
    assert snapshot["max_connections"] == 22
    assert snapshot["max_keepalive_connections"] == 11
    assert snapshot["keepalive_expiry_seconds"] == 123.0
    provider.close()


def test_shared_upstream_provider_records_pool_wait_and_timeout(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")

    provider = PatentSharedUpstreamHttpProvider.from_env()
    record_pool_wait = getattr(provider, "record_pool_wait", None)
    record_pool_timeout = getattr(provider, "record_pool_timeout", None)
    snapshot_fn = getattr(provider, "snapshot", None)

    assert callable(record_pool_wait)
    assert callable(record_pool_timeout)
    assert callable(snapshot_fn)

    record_pool_wait(wait_ms=12.5)
    record_pool_timeout(wait_ms=21.0)
    snapshot = snapshot_fn()

    assert snapshot["pool_wait_ms"] == 21.0
    assert snapshot["pool_timeout_count"] == 1
    provider.close()


def test_shared_upstream_provider_close_is_idempotent(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")

    provider = PatentSharedUpstreamHttpProvider.from_env()

    assert provider.client() is not None

    provider.close()
    provider.close()

    assert provider.client() is None
