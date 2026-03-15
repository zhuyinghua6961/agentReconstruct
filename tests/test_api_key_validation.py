from __future__ import annotations

import pytest

from agent_core import llm_client
from ingest import embedder


def test_get_llm_client_requires_dashscope_api_key(monkeypatch):
    monkeypatch.setattr(llm_client.config, "LLM_API_KEY", "")

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY is not configured"):
        llm_client.get_llm_client()


def test_get_embedding_client_requires_dashscope_api_key(monkeypatch):
    monkeypatch.setattr(embedder.config, "EMBEDDING_API_KEY", "")

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY is not configured"):
        embedder.get_embedding_client()
