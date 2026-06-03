from __future__ import annotations

import pytest

from agent_core import llm_client
from ingest import embedder


def test_get_llm_client_omits_auth_for_blank_llm_api_key(monkeypatch):
    monkeypatch.setattr(llm_client.config, "LLM_API_KEY", "")
    monkeypatch.setattr(llm_client.config, "LLM_BASE_URL", "http://local-llm/v1")

    client = llm_client.get_llm_client()

    assert client is not None
    assert client.endpoint == "http://local-llm/v1/chat/completions"
    assert "Authorization" not in client._headers()


def test_get_embedding_client_requires_dashscope_api_key(monkeypatch):
    monkeypatch.setattr(embedder.config, "EMBEDDING_API_KEY", "")

    with pytest.raises(RuntimeError, match="HIGHTHINKINGQA_EMBEDDING_API_KEY is not configured"):
        embedder.get_embedding_client()


def test_get_embedding_client_allows_blank_key_when_auth_mode_none(monkeypatch):
    monkeypatch.setattr(embedder.config, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(embedder.config, "HIGHTHINKINGQA_EMBEDDING_AUTH_MODE", "none", raising=False)
    monkeypatch.setattr(embedder.config, "EMBEDDING_BASE_URL", "http://embedding.example/v1", raising=False)

    client = embedder.get_embedding_client()

    assert "Authorization" not in client._headers()
