from __future__ import annotations

from app.modules.qa_pdf import llm_factory


class _FakeLogger:
    def info(self, *_args, **_kwargs):
        return None


def _configure_unified_llm(monkeypatch):
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


def test_init_llm_openai_compatible_path_propagates_http_client(monkeypatch):
    _configure_unified_llm(monkeypatch)
    shared_http_client = object()
    calls: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: calls.update(kwargs) or sentinel)

    llm = llm_factory.init_llm(_FakeLogger(), http_client=shared_http_client)

    assert llm is sentinel
    assert calls["http_client"] is shared_http_client
    assert calls["stream_read_timeout_seconds"] == 601.0


def test_init_llm_prefers_unified_llm_aliases(monkeypatch):
    _configure_unified_llm(monkeypatch)
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

    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: calls.update(kwargs) or sentinel)

    assert llm_factory.init_llm(_FakeLogger()) is sentinel
    assert calls["api_key"] == "llm-key"
    assert calls["base_url"] == "https://llm.example/v1"
    assert calls["model"] == "llm-model"


def test_init_llm_allows_blank_local_llm_api_key(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "llm-model")
    calls: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(llm_factory, "build_chat_adapter", lambda **kwargs: calls.update(kwargs) or sentinel)

    assert llm_factory.init_llm(_FakeLogger()) is sentinel
    assert calls["api_key"] == ""
    assert calls["base_url"] == "https://llm.example/v1"
    assert calls["model"] == "llm-model"


def test_init_llm_prefers_unified_llm_timeouts_over_fastqa_http_aliases(monkeypatch):
    _configure_unified_llm(monkeypatch)
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
