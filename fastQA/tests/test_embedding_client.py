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

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _Response()


def test_remote_embedding_client_uses_configurable_timeout(monkeypatch):
    requests_module = _Requests()
    monkeypatch.setenv("EMBEDDING_API_TIMEOUT_SECONDS", "150")
    monkeypatch.setenv("EMBEDDING_API_MODEL", "bge-local")

    client = RemoteEmbeddingClient("http://127.0.0.1:8001/v1/embeddings", requests_module)
    output = client.encode(["hello"])

    assert output.shape == (1, 2)
    assert requests_module.calls[0]["timeout"] == 150.0
    assert requests_module.calls[0]["json"]["model"] == "bge-local"
