from __future__ import annotations

from app.modules.microscopic_runtime.embedding_client import RemoteEmbeddingClient


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"embedding": [0.1, 0.2]}]}


class _Requests:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers or {}})
        return _Response()


def test_remote_embedding_client_uses_configurable_timeout(monkeypatch):
    requests_module = _Requests()
    monkeypatch.setenv("EMBEDDING_API_TIMEOUT_SECONDS", "150")
    monkeypatch.setenv("EMBEDDING_API_MODEL", "bge-local")

    client = RemoteEmbeddingClient("http://127.0.0.1:8001/v1", requests_module)
    output = client.encode(["hello"])

    assert output.shape == (1, 2)
    assert requests_module.calls[0]["url"] == "http://127.0.0.1:8001/v1/embeddings"
    assert requests_module.calls[0]["timeout"] == 150.0
    assert requests_module.calls[0]["json"]["model"] == "bge-local"


def test_remote_embedding_client_uses_embedding_api_model_over_legacy_model_name(monkeypatch):
    requests_module = _Requests()
    monkeypatch.setenv("EMBEDDING_API_MODEL", "target-model")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "legacy-model")

    client = RemoteEmbeddingClient("http://127.0.0.1:8001/v1/embeddings", requests_module)
    client.encode(["hello"])

    assert requests_module.calls[0]["json"]["model"] == "target-model"


def test_remote_embedding_client_tolerates_embedding_endpoint_url(monkeypatch):
    requests_module = _Requests()
    monkeypatch.setenv("EMBEDDING_API_MODEL", "target-model")

    client = RemoteEmbeddingClient("http://127.0.0.1:8001/v1/embeddings", requests_module)
    client.encode(["hello"])

    assert requests_module.calls[0]["url"] == "http://127.0.0.1:8001/v1/embeddings"


def test_remote_embedding_client_adds_authorization_header_when_embedding_api_key_is_set(monkeypatch):
    requests_module = _Requests()
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")

    client = RemoteEmbeddingClient("https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings", requests_module)
    client.encode(["hello"])

    assert requests_module.calls[0]["headers"]["Authorization"] == "Bearer embedding-key"


def test_remote_embedding_client_normalizes_bearer_embedding_api_key(monkeypatch):
    requests_module = _Requests()
    monkeypatch.setenv("EMBEDDING_API_KEY", "Bearer embedding-key")

    client = RemoteEmbeddingClient("https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings", requests_module)
    client.encode(["hello"])

    assert requests_module.calls[0]["headers"]["Authorization"] == "Bearer embedding-key"


def test_remote_embedding_client_supports_embedding_auth_mode(monkeypatch):
    requests_module = _Requests()
    monkeypatch.setenv("EMBEDDING_API_KEY", "Bearer embedding-key")
    monkeypatch.setenv("EMBEDDING_AUTH_MODE", "x-api-key")

    client = RemoteEmbeddingClient("https://embedding.example/v1/embeddings", requests_module)
    client.encode(["hello"])

    assert requests_module.calls[0]["headers"]["X-API-Key"] == "embedding-key"
    assert "Authorization" not in requests_module.calls[0]["headers"]


def test_remote_embedding_client_omits_authorization_header_without_embedding_api_key(monkeypatch):
    requests_module = _Requests()
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    client = RemoteEmbeddingClient("http://127.0.0.1:8001/v1/embeddings", requests_module)
    client.encode(["hello"])

    assert "Authorization" not in requests_module.calls[0]["headers"]
