from __future__ import annotations

from types import SimpleNamespace

from agent_core.llm_client import chat_completion, get_llm_client


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
