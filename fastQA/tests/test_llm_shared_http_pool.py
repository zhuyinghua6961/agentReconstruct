from __future__ import annotations

import logging

from app.integrations.llm.shared_http_pool import FastQASharedUpstreamHttpPool, SharedHttpPoolConfig


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeHttpx:
    class Timeout:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Limits:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def __init__(self) -> None:
        self.client_kwargs: list[dict[str, object]] = []
        self.client_instance = _FakeClient()

    def Client(self, **kwargs):
        self.client_kwargs.append(kwargs)
        return self.client_instance


def test_shared_pool_from_env_reuses_one_httpx_client_per_worker_runtime_config(monkeypatch):
    fake_httpx = _FakeHttpx()

    pool = FastQASharedUpstreamHttpPool.from_env(httpx_module=fake_httpx)

    first = pool.client()
    second = pool.client()

    assert first is second
    assert first is fake_httpx.client_instance
    assert len(fake_httpx.client_kwargs) == 1


def test_shared_pool_prefers_unified_llm_transport_env(monkeypatch):
    fake_httpx = _FakeHttpx()
    monkeypatch.setenv("LLM_CONNECT_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("LLM_READ_TIMEOUT_SECONDS", "222")
    monkeypatch.setenv("LLM_STREAM_READ_TIMEOUT_SECONDS", "333")
    monkeypatch.setenv("LLM_WRITE_TIMEOUT_SECONDS", "44")
    monkeypatch.setenv("LLM_POOL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("LLM_MAX_CONNECTIONS", "77")
    monkeypatch.setenv("LLM_MAX_KEEPALIVE_CONNECTIONS", "8")
    monkeypatch.setenv("LLM_KEEPALIVE_EXPIRY_SECONDS", "66")
    monkeypatch.setenv("FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", "601")
    monkeypatch.setenv("FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS", "181")
    monkeypatch.setenv("FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_CONNECTIONS", "160")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "64")
    monkeypatch.setenv("FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "90")

    pool = FastQASharedUpstreamHttpPool.from_env(httpx_module=fake_httpx)
    client = pool.client()

    assert client is fake_httpx.client_instance
    kwargs = fake_httpx.client_kwargs[0]
    assert kwargs["timeout"].kwargs == {
        "connect": 11.0,
        "read": 222.0,
        "write": 44.0,
        "pool": 5.0,
    }
    assert kwargs["limits"].kwargs == {
        "max_connections": 77,
        "max_keepalive_connections": 8,
        "keepalive_expiry": 66.0,
    }
    assert kwargs["http2"] is False


def test_shared_pool_still_falls_back_to_fastqa_http_aliases_during_migration(monkeypatch):
    fake_httpx = _FakeHttpx()
    for name in (
        "LLM_CONNECT_TIMEOUT_SECONDS",
        "LLM_READ_TIMEOUT_SECONDS",
        "LLM_STREAM_READ_TIMEOUT_SECONDS",
        "LLM_WRITE_TIMEOUT_SECONDS",
        "LLM_POOL_TIMEOUT_SECONDS",
        "LLM_MAX_CONNECTIONS",
        "LLM_MAX_KEEPALIVE_CONNECTIONS",
        "LLM_KEEPALIVE_EXPIRY_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS", "181")
    monkeypatch.setenv("FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_CONNECTIONS", "160")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "64")
    monkeypatch.setenv("FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "90")

    pool = FastQASharedUpstreamHttpPool.from_env(httpx_module=fake_httpx)
    pool.client()

    kwargs = fake_httpx.client_kwargs[0]
    assert kwargs["timeout"].kwargs == {
        "connect": 15.0,
        "read": 180.0,
        "write": 181.0,
        "pool": 30.0,
    }
    assert kwargs["limits"].kwargs == {
        "max_connections": 160,
        "max_keepalive_connections": 64,
        "keepalive_expiry": 90.0,
    }


def test_shared_llm_pool_default_capacity_matches_streaming_budget(monkeypatch):
    for name in (
        "FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS",
        "FASTQA_LLM_HTTP_MAX_CONNECTIONS",
        "FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS",
        "OPENAI_CONNECT_TIMEOUT_SECONDS",
        "OPENAI_READ_TIMEOUT_SECONDS",
        "OPENAI_WRITE_TIMEOUT_SECONDS",
        "OPENAI_POOL_TIMEOUT_SECONDS",
        "DASHSCOPE_CONNECT_TIMEOUT_SECONDS",
        "DASHSCOPE_READ_TIMEOUT_SECONDS",
        "DASHSCOPE_WRITE_TIMEOUT_SECONDS",
        "DASHSCOPE_POOL_TIMEOUT_SECONDS",
        "LLM_CONNECT_TIMEOUT_SECONDS",
        "LLM_READ_TIMEOUT_SECONDS",
        "LLM_WRITE_TIMEOUT_SECONDS",
        "LLM_POOL_TIMEOUT_SECONDS",
        "LLM_KEEPALIVE_EXPIRY_SECONDS",
        "LLM_MAX_CONNECTIONS",
        "LLM_MAX_KEEPALIVE_CONNECTIONS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = SharedHttpPoolConfig.from_env()

    assert config.connect_timeout_seconds == 15.0
    assert config.read_timeout_seconds == 180.0
    assert config.write_timeout_seconds == 180.0
    assert config.pool_timeout_seconds == 30.0
    assert config.keepalive_expiry_seconds == 90.0
    assert config.max_connections == 160
    assert config.max_keepalive_connections == 64


def test_shared_pool_config_warns_when_env_values_are_invalid(monkeypatch, caplog):
    monkeypatch.setenv("LLM_POOL_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("LLM_MAX_CONNECTIONS", "bad-value")
    monkeypatch.setenv("FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_CONNECTIONS", "34")

    with caplog.at_level(logging.WARNING):
        config = SharedHttpPoolConfig.from_env()

    assert config.pool_timeout_seconds == 30.0
    assert config.max_connections == 160
    assert "LLM_POOL_TIMEOUT_SECONDS" in caplog.text
    assert "LLM_MAX_CONNECTIONS" in caplog.text
