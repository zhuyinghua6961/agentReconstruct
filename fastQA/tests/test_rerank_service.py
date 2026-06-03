from __future__ import annotations

from app.modules.generation_pipeline.rerank_service import rerank_documents


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> dict:
        return self._payload


class _Requests:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def post(self, endpoint, headers, json, timeout):
        self.calls.append({"endpoint": endpoint, "headers": headers, "json": json, "timeout": timeout})
        return _Response(self.payload)


def test_rerank_posts_openai_compatible_payload_without_auth():
    req = _Requests({"results": [{"index": 1, "relevance_score": 0.92}, {"index": 0, "relevance_score": 0.51}]})

    result = rerank_documents(
        query="lfp query",
        documents=["doc-a", "doc-b"],
        metadatas=[{"id": "a"}, {"id": "b"}],
        top_n=2,
        api_key="",
        model="qwen3-vl-rerank",
        base_url="http://localhost:8084/v1",
        timeout_seconds=7.0,
        requests_module=req,
    )

    assert req.calls == [
        {
            "endpoint": "http://localhost:8084/v1/rerank",
            "headers": {"Content-Type": "application/json"},
            "json": {
                "model": "qwen3-vl-rerank",
                "query": "lfp query",
                "documents": ["doc-a", "doc-b"],
                "top_n": 2,
            },
            "timeout": 7.0,
        }
    ]
    assert result == {
        "documents": ["doc-b", "doc-a"],
        "metadatas": [{"id": "b"}, {"id": "a"}],
        "rerank_scores": [0.92, 0.51],
        "fallback": False,
        "fallback_reason": "",
        "provider": "openai_compatible",
    }


def test_rerank_is_disabled_when_base_url_is_omitted():
    req = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})

    result = rerank_documents(
        query="q",
        documents=["doc"],
        model="m",
        requests_module=req,
    )

    assert req.calls == []
    assert result["fallback"] is True
    assert result["fallback_reason"] == "provider_disabled"


def test_rerank_is_disabled_when_model_is_omitted():
    req = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})

    result = rerank_documents(
        query="q",
        documents=["doc"],
        base_url="http://reranker/v1",
        requests_module=req,
    )

    assert req.calls == []
    assert result["fallback"] is True
    assert result["fallback_reason"] == "provider_disabled"


def test_rerank_adds_auth_only_when_api_key_is_present():
    req = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})

    rerank_documents(
        query="q",
        documents=["doc"],
        api_key="local-key",
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert req.calls[0]["headers"]["Authorization"] == "Bearer local-key"


def test_rerank_normalizes_bearer_api_key():
    req = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})

    rerank_documents(
        query="q",
        documents=["doc"],
        api_key="Bearer local-key",
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert req.calls[0]["headers"]["Authorization"] == "Bearer local-key"


def test_rerank_supports_authorization_auth_mode(monkeypatch):
    req = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})
    monkeypatch.setenv("RERANK_AUTH_MODE", "authorization")

    rerank_documents(
        query="q",
        documents=["doc"],
        api_key="Bearer local-key",
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert req.calls[0]["headers"]["Authorization"] == "local-key"


def test_rerank_caps_returned_rows_to_top_n_and_skips_invalid_indexes():
    req = _Requests(
        {
            "results": [
                {"index": 99, "relevance_score": 1.0},
                {"index": 2, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
                {"index": 0, "relevance_score": 0.7},
            ]
        }
    )

    result = rerank_documents(
        query="q",
        documents=["doc-a", "doc-b", "doc-c"],
        top_n=2,
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert result["documents"] == ["doc-c", "doc-b"]
    assert result["rerank_scores"] == [0.9, 0.8]


def test_rerank_skips_malformed_indexes_and_keeps_valid_rows():
    req = _Requests(
        {
            "results": [
                {"index": None, "relevance_score": 1.0},
                {"index": "bad", "relevance_score": 0.95},
                {"index": 1, "relevance_score": 0.9},
            ]
        }
    )

    result = rerank_documents(
        query="q",
        documents=["doc-a", "doc-b"],
        top_n=1,
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert result["documents"] == ["doc-b"]
    assert result["rerank_scores"] == [0.9]
    assert result["fallback"] is False


def test_rerank_falls_back_when_request_fails():
    class _FailingRequests:
        def post(self, endpoint, headers, json, timeout):
            raise RuntimeError("boom")

    result = rerank_documents(
        query="q",
        documents=["doc-a", "doc-b"],
        top_n=1,
        model="m",
        base_url="http://reranker",
        requests_module=_FailingRequests(),
    )

    assert result["documents"] == ["doc-a"]
    assert result["fallback"] is True
    assert result["fallback_reason"] == "request_failed"
    assert result["provider"] == "openai_compatible"


def test_rerank_falls_back_when_response_has_no_valid_rows():
    req = _Requests({"results": [{"index": 99, "relevance_score": 1.0}]})

    result = rerank_documents(
        query="q",
        documents=["doc-a"],
        top_n=1,
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert result["documents"] == ["doc-a"]
    assert result["fallback"] is True
    assert result["fallback_reason"] == "empty_rerank_result"


def test_rerank_falls_back_when_json_parsing_fails():
    class _BadResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            raise ValueError("bad json")

    class _RequestsWithBadJson:
        def post(self, endpoint, headers, json, timeout):
            return _BadResponse()

    result = rerank_documents(
        query="q",
        documents=["doc-a"],
        top_n=1,
        model="m",
        base_url="http://reranker",
        requests_module=_RequestsWithBadJson(),
    )

    assert result["fallback"] is True
    assert result["fallback_reason"] == "request_failed"


def test_rerank_tolerates_final_endpoint_url():
    req = _Requests({"results": [{"index": 0, "relevance_score": 0.77}]})

    result = rerank_documents(
        query="q",
        documents=["doc"],
        top_n=1,
        api_key="dash-key",
        model="dash-model",
        base_url="https://rerank.example/v1/rerank",
        requests_module=req,
    )

    assert req.calls[0]["endpoint"] == "https://rerank.example/v1/rerank"
    assert req.calls[0]["headers"]["Authorization"] == "Bearer dash-key"
    assert req.calls[0]["json"] == {
        "model": "dash-model",
        "query": "q",
        "documents": ["doc"],
        "top_n": 1,
    }
    assert result["fallback"] is False


def test_retired_provider_argument_is_ignored():
    req = _Requests({"results": []})

    result = rerank_documents(
        query="q",
        documents=["doc"],
        provider="disabled",
        model="m",
        base_url="http://reranker/v1",
        requests_module=req,
    )

    assert req.calls[0]["endpoint"] == "http://reranker/v1/rerank"
    assert result["fallback"] is True
    assert result["fallback_reason"] == "empty_rerank_result"
    assert result["provider"] == "openai_compatible"
