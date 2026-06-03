from __future__ import annotations

from server.patent.rerank_service import build_patent_stage2_rerank_fn, rerank_patent_stage2_documents


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Requests:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def post(self, endpoint, *, headers, json, timeout):
        self.calls.append({"endpoint": endpoint, "headers": headers, "json": json, "timeout": timeout})
        return _Response(self.payload)


def test_patent_rerank_uses_openai_compatible_payload():
    requests = _Requests({"results": [{"index": 1, "relevance_score": 0.95}]})

    result = rerank_patent_stage2_documents(
        query="thermal",
        documents=["doc-a", "doc-b"],
        metadatas=[{"patent_id": "A"}, {"patent_id": "B"}],
        top_n=1,
        provider="dashscope",
        api_key="key-1",
        model="gte-rerank-v2",
        base_url="https://dashscope.example/v1",
        timeout_seconds=12.5,
        requests_module=requests,
    )

    assert result["documents"] == ["doc-b"]
    assert result["metadatas"] == [{"patent_id": "B"}]
    assert result["rerank_scores"] == [0.95]
    assert result["fallback"] is False
    assert requests.calls[0]["endpoint"] == "https://dashscope.example/v1/rerank"
    assert requests.calls[0]["headers"]["Authorization"] == "Bearer key-1"
    assert requests.calls[0]["json"] == {
        "model": "gte-rerank-v2",
        "query": "thermal",
        "documents": ["doc-a", "doc-b"],
        "top_n": 1,
    }
    assert requests.calls[0]["timeout"] == 12.5


def test_patent_rerank_normalizes_bearer_api_key():
    requests = _Requests({"results": [{"index": 0, "relevance_score": 0.95}]})

    rerank_patent_stage2_documents(
        query="thermal",
        documents=["doc-a"],
        provider="local",
        api_key="Bearer key-1",
        model="qwen3-vl-rerank",
        base_url="http://localhost:8084",
        requests_module=requests,
    )

    assert requests.calls[0]["headers"]["Authorization"] == "Bearer key-1"


def test_patent_rerank_supports_x_api_key_auth_mode(monkeypatch):
    requests = _Requests({"results": [{"index": 0, "relevance_score": 0.95}]})
    monkeypatch.setenv("RERANK_AUTH_MODE", "x-api-key")

    rerank_patent_stage2_documents(
        query="thermal",
        documents=["doc-a"],
        provider="local",
        api_key="Bearer key-1",
        model="qwen3-vl-rerank",
        base_url="http://localhost:8084",
        requests_module=requests,
    )

    assert requests.calls[0]["headers"]["X-API-Key"] == "key-1"
    assert "Authorization" not in requests.calls[0]["headers"]


def test_patent_rerank_payload_does_not_send_legacy_return_documents():
    requests = _Requests({"results": [{"index": 0, "relevance_score": 0.95}]})

    result = rerank_patent_stage2_documents(
        query="thermal",
        documents=["doc-a"],
        metadatas=[{"patent_id": "CN123"}],
        top_n=1,
        provider="local",
        model="qwen3-vl-rerank",
        base_url="http://localhost:8084",
        timeout_seconds=12.5,
        requests_module=requests,
    )

    assert result["documents"] == ["doc-a"]
    assert requests.calls[0]["endpoint"] == "http://localhost:8084/v1/rerank"
    assert requests.calls[0]["json"] == {
        "model": "qwen3-vl-rerank",
        "query": "thermal",
        "documents": ["doc-a"],
        "top_n": 1,
    }


def test_patent_rerank_fn_reads_unified_env_and_does_not_require_runtime_injection(monkeypatch):
    requests = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})
    monkeypatch.setenv("RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RERANK_API_KEY", "rerank-key")
    monkeypatch.setenv("RERANK_BASE_URL", "https://dashscope.example")
    monkeypatch.setenv("RERANK_MODEL", "gte-rerank-v2")
    monkeypatch.setenv("RERANK_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "local")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_API_KEY", "patent-key")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_BASE_URL", "https://legacy.example")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_MODEL", "legacy-rerank")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TIMEOUT_SECONDS", "22")

    rerank_fn = build_patent_stage2_rerank_fn(requests_module=requests)

    assert rerank_fn is not None
    result = rerank_fn(query="q", documents=["doc"], metadatas=[{"patent_id": "CN"}], top_n=1)
    assert result["fallback"] is False
    assert requests.calls[0]["headers"]["Authorization"] == "Bearer rerank-key"
    assert requests.calls[0]["endpoint"] == "https://dashscope.example/v1/rerank"
    assert requests.calls[0]["timeout"] == 9.0


def test_patent_rerank_fn_is_disabled_when_base_url_is_missing(monkeypatch):
    requests = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})
    monkeypatch.setenv("RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RERANK_API_KEY", "rerank-key")
    monkeypatch.delenv("RERANK_BASE_URL", raising=False)
    monkeypatch.setenv("RERANK_MODEL", "gte-rerank-v2")
    monkeypatch.delenv("PATENT_STAGE2_RERANK_BASE_URL", raising=False)
    monkeypatch.delenv("PATENT_STAGE2_RERANK_MODEL", raising=False)

    assert build_patent_stage2_rerank_fn(requests_module=requests) is None
    assert requests.calls == []


def test_patent_rerank_fn_falls_back_to_legacy_endpoint_aliases_for_one_version(monkeypatch):
    requests = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})
    for name in ("RERANK_PROVIDER", "RERANK_API_KEY", "RERANK_BASE_URL", "RERANK_MODEL", "RERANK_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_API_KEY", "legacy-key")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_BASE_URL", "https://legacy.example")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_MODEL", "legacy-rerank")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TIMEOUT_SECONDS", "22")

    rerank_fn = build_patent_stage2_rerank_fn(requests_module=requests)

    assert rerank_fn is not None
    result = rerank_fn(query="q", documents=["doc"], metadatas=[{}], top_n=1)
    assert result["fallback"] is False
    assert requests.calls[0]["headers"]["Authorization"] == "Bearer legacy-key"
    assert requests.calls[0]["endpoint"] == "https://legacy.example/v1/rerank"
    assert requests.calls[0]["timeout"] == 22.0


def test_patent_rerank_is_disabled_when_model_is_missing(monkeypatch):
    monkeypatch.setenv("RERANK_PROVIDER", "none")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rerank.example/v1")
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    monkeypatch.delenv("PATENT_STAGE2_RERANK_BASE_URL", raising=False)
    monkeypatch.delenv("PATENT_STAGE2_RERANK_MODEL", raising=False)

    assert build_patent_stage2_rerank_fn() is None
