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


def test_patent_rerank_dashscope_uses_fastqa_compatible_payload():
    requests = _Requests({"output": {"results": [{"index": 1, "relevance_score": 0.95}]}})

    result = rerank_patent_stage2_documents(
        query="thermal",
        documents=["doc-a", "doc-b"],
        metadatas=[{"patent_id": "A"}, {"patent_id": "B"}],
        top_n=1,
        provider="dashscope",
        api_key="key-1",
        model="gte-rerank-v2",
        base_url="https://dashscope.example",
        timeout_seconds=12.5,
        requests_module=requests,
    )

    assert result["documents"] == ["doc-b"]
    assert result["metadatas"] == [{"patent_id": "B"}]
    assert result["rerank_scores"] == [0.95]
    assert result["fallback"] is False
    assert requests.calls[0]["endpoint"] == "https://dashscope.example/api/v1/services/rerank/text-rerank/text-rerank"
    assert requests.calls[0]["headers"]["Authorization"] == "Bearer key-1"
    assert requests.calls[0]["json"]["model"] == "gte-rerank-v2"
    assert requests.calls[0]["timeout"] == 12.5


def test_patent_rerank_fn_reads_patent_env_and_does_not_require_runtime_injection(monkeypatch):
    requests = _Requests({"output": {"results": [{"index": 0, "relevance_score": 0.8}]}})
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_API_KEY", "patent-key")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_BASE_URL", "https://dashscope.example")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_MODEL", "gte-rerank-v2")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TIMEOUT_SECONDS", "9")

    rerank_fn = build_patent_stage2_rerank_fn(requests_module=requests)

    assert rerank_fn is not None
    result = rerank_fn(query="q", documents=["doc"], metadatas=[{"patent_id": "CN"}], top_n=1)
    assert result["fallback"] is False
    assert requests.calls[0]["headers"]["Authorization"] == "Bearer patent-key"
    assert requests.calls[0]["timeout"] == 9.0


def test_patent_rerank_fn_uses_dashscope_key_fallback(monkeypatch):
    requests = _Requests({"output": {"results": [{"index": 0, "relevance_score": 0.8}]}})
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "dashscope")
    monkeypatch.delenv("PATENT_STAGE2_RERANK_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    rerank_fn = build_patent_stage2_rerank_fn(requests_module=requests)

    assert rerank_fn is not None
    rerank_fn(query="q", documents=["doc"], metadatas=[{}], top_n=1)
    assert requests.calls[0]["headers"]["Authorization"] == "Bearer dashscope-key"


def test_patent_rerank_provider_none_returns_no_runtime_callable(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "none")

    assert build_patent_stage2_rerank_fn() is None
