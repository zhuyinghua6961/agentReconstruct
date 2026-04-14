from __future__ import annotations

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
