from __future__ import annotations

import sys
from types import SimpleNamespace

from app.modules.qa_pdf import llm_factory


class _FakeLogger:
    def info(self, *_args, **_kwargs):
        return None


def _configure_langchain_branch(monkeypatch):
    for name in (
        "LLM_CONNECT_TIMEOUT_SECONDS",
        "LLM_READ_TIMEOUT_SECONDS",
        "LLM_STREAM_READ_TIMEOUT_SECONDS",
        "LLM_WRITE_TIMEOUT_SECONDS",
        "LLM_POOL_TIMEOUT_SECONDS",
        "LLM_KEEPALIVE_EXPIRY_SECONDS",
        "LLM_MAX_CONNECTIONS",
        "LLM_MAX_KEEPALIVE_CONNECTIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen-test")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", "601")
    monkeypatch.setenv("FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS", "181")
    monkeypatch.setenv("FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "90")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_CONNECTIONS", "160")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "64")
    monkeypatch.setenv("PDF_QA_TIMEOUT_SECONDS", "60")
    monkeypatch.setattr(llm_factory, "should_use_dashscope_native", lambda **_kwargs: False)


def test_init_llm_langchain_branch_reuses_injected_http_client(monkeypatch):
    _configure_langchain_branch(monkeypatch)
    calls: dict[str, object] = {}
    created_clients: list[dict[str, object]] = []
    shared_http_client = object()

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.update(kwargs)

    fake_httpx = SimpleNamespace(
        Timeout=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Limits=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Client=lambda **kwargs: created_clients.append(kwargs) or SimpleNamespace(kwargs=kwargs),
    )
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=_FakeChatOpenAI))
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    llm = llm_factory.init_llm(_FakeLogger(), http_client=shared_http_client)

    assert isinstance(llm, _FakeChatOpenAI)
    assert created_clients == []
    assert calls["http_client"] is shared_http_client
    assert calls["timeout"].kwargs == {
        "connect": 15.0,
        "read": 601.0,
        "write": 181.0,
        "pool": 30.0,
    }


def test_init_llm_langchain_branch_builds_private_transport_http_client(monkeypatch):
    _configure_langchain_branch(monkeypatch)
    calls: dict[str, object] = {}
    created_clients: list[dict[str, object]] = []

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.update(kwargs)

    fake_httpx = SimpleNamespace(
        Timeout=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Limits=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Client=lambda **kwargs: created_clients.append(kwargs) or SimpleNamespace(kwargs=kwargs),
    )
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=_FakeChatOpenAI))
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    llm = llm_factory.init_llm(_FakeLogger())

    assert isinstance(llm, _FakeChatOpenAI)
    assert len(created_clients) == 1
    client_kwargs = created_clients[0]
    assert client_kwargs["timeout"].kwargs == {
        "connect": 15.0,
        "read": 601.0,
        "write": 181.0,
        "pool": 30.0,
    }
    assert client_kwargs["limits"].kwargs == {
        "max_connections": 160,
        "max_keepalive_connections": 64,
        "keepalive_expiry": 90.0,
    }
    assert client_kwargs["http2"] is False
    assert calls["http_client"].kwargs == client_kwargs
    assert calls["timeout"].kwargs == {
        "connect": 15.0,
        "read": 601.0,
        "write": 181.0,
        "pool": 30.0,
    }


def test_init_llm_openai_compatible_path_propagates_http_client(monkeypatch):
    _configure_langchain_branch(monkeypatch)
    shared_http_client = object()
    calls: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(llm_factory, "should_use_dashscope_native", lambda **_kwargs: True)
    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: calls.update(kwargs) or sentinel)

    llm = llm_factory.init_llm(_FakeLogger(), http_client=shared_http_client)

    assert llm is sentinel
    assert calls["http_client"] is shared_http_client
    assert calls["stream_read_timeout_seconds"] == 601.0


def test_init_llm_prefers_unified_llm_aliases(monkeypatch):
    _configure_langchain_branch(monkeypatch)
    monkeypatch.setenv("PDF_QA_MODEL", "pdf-model")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "llm-model")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dash.example/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "dash-model")
    calls: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(llm_factory, "should_use_dashscope_native", lambda **_kwargs: True)
    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: calls.update(kwargs) or sentinel)

    assert llm_factory.init_llm(_FakeLogger()) is sentinel
    assert calls["api_key"] == "llm-key"
    assert calls["base_url"] == "https://llm.example/v1"
    assert calls["model"] == "llm-model"


def test_init_llm_ignores_retired_llm_aliases(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PDF_QA_MODEL", "pdf-model")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dash.example/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "dash-model")

    try:
        llm_factory.init_llm(_FakeLogger())
    except ValueError as exc:
        assert str(exc) == "请设置LLM_API_KEY环境变量"
    else:  # pragma: no cover - enforced by failing test before fix
        raise AssertionError("expected retired aliases to be ignored")


def test_init_llm_prefers_unified_llm_timeouts_over_fastqa_http_aliases(monkeypatch):
    _configure_langchain_branch(monkeypatch)
    monkeypatch.setenv("LLM_CONNECT_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("LLM_READ_TIMEOUT_SECONDS", "222")
    monkeypatch.setenv("LLM_STREAM_READ_TIMEOUT_SECONDS", "333")
    monkeypatch.setenv("LLM_WRITE_TIMEOUT_SECONDS", "44")
    monkeypatch.setenv("LLM_POOL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("LLM_KEEPALIVE_EXPIRY_SECONDS", "66")
    monkeypatch.setenv("LLM_MAX_CONNECTIONS", "77")
    monkeypatch.setenv("LLM_MAX_KEEPALIVE_CONNECTIONS", "8")
    calls: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(llm_factory, "should_use_dashscope_native", lambda **_kwargs: True)
    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: calls.update(kwargs) or sentinel)

    assert llm_factory.init_llm(_FakeLogger()) is sentinel
    assert calls["connect_timeout_seconds"] == 11.0
    assert calls["read_timeout_seconds"] == 222.0
    assert calls["stream_read_timeout_seconds"] == 333.0
    assert calls["write_timeout_seconds"] == 44.0
    assert calls["pool_timeout_seconds"] == 5.0
    assert calls["keepalive_expiry_seconds"] == 66.0
    assert calls["max_connections"] == 77
    assert calls["max_keepalive_connections"] == 8


def test_init_llm_langchain_constructor_failure_closes_private_client_before_fallback(monkeypatch):
    _configure_langchain_branch(monkeypatch)
    built_clients: list[SimpleNamespace] = []
    fallback_calls: dict[str, object] = {}
    sentinel = object()

    class _FakeChatOpenAI:
        def __init__(self, **_kwargs):
            raise RuntimeError("constructor failed")

    def _fake_client(**kwargs):
        client = SimpleNamespace(kwargs=kwargs, close_calls=0)

        def _close():
            client.close_calls += 1

        client.close = _close
        built_clients.append(client)
        return client

    fake_httpx = SimpleNamespace(
        Timeout=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Limits=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Client=_fake_client,
    )
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=_FakeChatOpenAI))
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: fallback_calls.update(kwargs) or sentinel)

    llm = llm_factory.init_llm(_FakeLogger())

    assert llm is sentinel
    assert len(built_clients) == 1
    assert built_clients[0].close_calls == 1
    assert fallback_calls["http_client"] is None


def test_init_llm_fallback_openai_compatible_path_propagates_http_client(monkeypatch):
    _configure_langchain_branch(monkeypatch)
    shared_http_client = object()
    fallback_calls: dict[str, object] = {}
    sentinel = object()

    class _FakeChatOpenAI:
        def __init__(self, **_kwargs):
            raise RuntimeError("constructor failed")

    fake_httpx = SimpleNamespace(
        Timeout=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Limits=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        Client=lambda **kwargs: SimpleNamespace(kwargs=kwargs, close=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=_FakeChatOpenAI))
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: fallback_calls.update(kwargs) or sentinel)

    llm = llm_factory.init_llm(_FakeLogger(), http_client=shared_http_client)

    assert llm is sentinel
    assert fallback_calls["http_client"] is shared_http_client
    assert fallback_calls["stream_read_timeout_seconds"] == 601.0
