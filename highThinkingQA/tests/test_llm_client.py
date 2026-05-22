from __future__ import annotations

from types import SimpleNamespace

from agent_core.llm_client import chat_completion, chat_completion_stream, get_llm_client
from server.services.documents_service import DocumentsService


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", reasoning_content=None))]
        )


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def test_chat_completion_forwards_timeout_to_sdk_call():
    completions = _FakeCompletions()
    client = _FakeClient(completions)

    result = chat_completion(
        prompt="demo",
        client=client,
        enable_thinking=False,
        timeout_seconds=12.5,
    )

    assert result == "ok"
    assert completions.calls[0]["timeout"] == 12.5


def test_chat_completion_omits_enable_thinking_for_non_stream_calls():
    completions = _FakeCompletions()
    client = _FakeClient(completions)

    result = chat_completion(
        prompt="demo",
        client=client,
        enable_thinking=True,
    )

    assert result == "ok"
    call = completions.calls[0]
    assert call.get("stream") in (None, False)
    assert "extra_body" not in call
    assert call["temperature"] == 0.7


def test_chat_completion_stream_sends_enable_thinking_for_stream_calls():
    class _FakeStreamCompletions(_FakeCompletions):
        def create(self, **kwargs):
            self.calls.append(dict(kwargs))
            delta = SimpleNamespace(content="ok", reasoning_content=None)
            chunk = SimpleNamespace(choices=[SimpleNamespace(delta=delta)])
            return iter([chunk])

    completions = _FakeStreamCompletions()
    client = _FakeClient(completions)

    result = "".join(
        chat_completion_stream(
            prompt="demo",
            client=client,
            enable_thinking=True,
        )
    )

    assert result == "ok"
    call = completions.calls[0]
    assert call["stream"] is True
    assert call["extra_body"] == {"enable_thinking": True}



def test_get_llm_client_forwards_max_retries_override(monkeypatch):
    captured = {}

    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "masked")
    monkeypatch.setattr("agent_core.llm_client.config.LLM_BASE_URL", "https://example.invalid/v1")

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("agent_core.llm_client.OpenAI", fake_openai)

    client = get_llm_client(max_retries=0)

    assert client is not None
    assert captured["max_retries"] == 0


def test_get_async_llm_client_requires_llm_api_key(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "")

    try:
        from agent_core.llm_client import get_async_llm_client

        get_async_llm_client()
    except RuntimeError as exc:
        assert str(exc) == "LLM_API_KEY is not configured"
    else:
        raise AssertionError("expected RuntimeError")


def test_documents_service_prefers_unified_llm_namespace(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "llm-model")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dash.example/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "dash-model")
    monkeypatch.setenv("DOCUMENTS_LLM_MODEL", "documents-model")

    assert DocumentsService._llm_api_key() == "llm-key"
    assert DocumentsService._llm_base_url() == "https://llm.example/v1"
    assert DocumentsService._llm_model() == "llm-model"
