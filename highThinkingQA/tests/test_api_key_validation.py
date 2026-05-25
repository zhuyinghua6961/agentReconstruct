from __future__ import annotations

import pytest

from agent_core import llm_client
from ingest import embedder


def test_get_llm_client_uses_local_placeholder_for_blank_llm_api_key(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm_client.config, "LLM_API_KEY", "")
    monkeypatch.setattr(llm_client.config, "LLM_BASE_URL", "http://local-llm/v1")

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm_client, "OpenAI", fake_openai)

    client = llm_client.get_llm_client()

    assert client is not None
    assert captured["api_key"] == "local-openai-compatible"
    assert captured["base_url"] == "http://local-llm/v1"


def test_get_embedding_client_requires_dashscope_api_key(monkeypatch):
    monkeypatch.setattr(embedder.config, "EMBEDDING_API_KEY", "")

    with pytest.raises(RuntimeError, match="HIGHTHINKINGQA_EMBEDDING_API_KEY is not configured"):
        embedder.get_embedding_client()
