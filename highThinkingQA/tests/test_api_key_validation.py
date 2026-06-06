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


def test_embed_texts_raises_on_embedding_parameter_error(monkeypatch):
    class _FailingEmbeddings:
        def create(self, **kwargs):
            raise RuntimeError("400 InvalidParameter: unsupported dimensions")

    class _FailingClient:
        embeddings = _FailingEmbeddings()

    monkeypatch.setattr(embedder.config, "EMBEDDING_DIMENSIONS", 4096, raising=False)
    monkeypatch.setattr(embedder.config, "HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES", 1, raising=False)

    with pytest.raises(RuntimeError, match="unsupported dimensions"):
        embedder.embed_texts(["non-empty query"], client=_FailingClient())


def test_embed_texts_omits_dimensions_parameter_for_remote_api(monkeypatch):
    calls = []

    class _EmbeddingItem:
        embedding = [0.1, 0.2, 0.3]

    class _Response:
        data = [_EmbeddingItem()]

    class _Embeddings:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _Response()

    class _Client:
        embeddings = _Embeddings()

    monkeypatch.setattr(embedder.config, "EMBEDDING_DIMENSIONS", 3, raising=False)

    result = embedder.embed_texts(["query"], client=_Client())

    assert result == [[0.1, 0.2, 0.3]]
    assert calls
    assert "dimensions" not in calls[0]
    assert calls[0]["model"] == embedder.config.EMBEDDING_MODEL
    assert calls[0]["encoding_format"] == "float"


def test_embed_texts_rejects_response_dimension_mismatch(monkeypatch):
    class _EmbeddingItem:
        embedding = [0.1, 0.2]

    class _Response:
        data = [_EmbeddingItem()]

    class _Embeddings:
        def create(self, **kwargs):
            return _Response()

    class _Client:
        embeddings = _Embeddings()

    monkeypatch.setattr(embedder.config, "EMBEDDING_DIMENSIONS", 4096, raising=False)

    with pytest.raises(RuntimeError, match="dimension mismatch"):
        embedder.embed_texts(["query"], client=_Client())
