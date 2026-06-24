from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from server_fastapi.app import create_app


@pytest.fixture()
def client(monkeypatch):
    fake_service = MagicMock()
    fake_service.search.return_value = (
        {
            "items": [
                {
                    "canonical_patent_id": "CN123456789A",
                    "title": "Battery system",
                    "match_source": "patent_abstracts",
                    "match_score": 0.91,
                    "has_pdf": True,
                }
            ],
            "count": 1,
            "query_type_detected": "topic",
            "query": "battery",
            "sources": ["patent_abstracts"],
            "retrieval_backend": "vector_hybrid",
        },
        200,
    )
    monkeypatch.setattr(
        "server_fastapi.routers.patent_search._search_service",
        lambda request: fake_service,
    )
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client, fake_service


def test_patent_search_get_route_contract(client):
    test_client, _service = client
    response = test_client.get(
        "/api/v1/patent_search",
        params={"query": "battery", "query_type": "topic", "sources": "abstract"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["canonical_patent_id"] == "CN123456789A"


def test_patent_search_post_accepts_json_body(client):
    test_client, service = client
    response = test_client.post(
        "/api/v1/patent_search",
        json={
            "query": "CN123456789A",
            "query_type": "patent_id",
            "sources": "both",
            "limit": 10,
        },
    )
    assert response.status_code == 200
    service.search.assert_called()
    kwargs = service.search.call_args.kwargs
    assert kwargs["query"] == "CN123456789A"
    assert kwargs["query_type"] == "patent_id"


def test_patent_search_requires_query(client, monkeypatch):
    fake_service = MagicMock()
    fake_service.search.return_value = ({"items": [], "count": 0, "error": "缺少查询参数"}, 200)
    monkeypatch.setattr(
        "server_fastapi.routers.patent_search._search_service",
        lambda request: fake_service,
    )
    app = create_app()
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/patent_search")
    assert response.status_code == 200
    assert response.json()["error"] == "缺少查询参数"
