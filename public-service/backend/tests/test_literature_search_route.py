from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_literature_search_route_contract(monkeypatch):
    app = create_app()

    def _fake_search(**kwargs):
        _ = kwargs
        return (
            {
                "items": [
                    {
                        "doi": "10.1000/test",
                        "title": "Paper",
                        "journal": "J",
                        "publication_date": "2024",
                        "pdf_exists": True,
                        "pdf_url": "/api/v1/view_pdf/10.1000%2Ftest",
                        "match_source": "fastqa_chroma",
                        "match_score": 1.0,
                        "match_mode": "exact",
                    }
                ],
                "count": 1,
                "query_type_detected": "doi",
                "query": "10.1000/test",
                "sources": ["fastqa"],
            },
            200,
        )

    monkeypatch.setattr(
        "app.modules.literature_search.api.literature_search_service.search",
        _fake_search,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/literature_search",
            params={"query": "10.1000/test", "query_type": "doi", "sources": "fastqa"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["doi"] == "10.1000/test"


def test_literature_search_post_accepts_json_body(monkeypatch):
    app = create_app()
    captured = {}

    def _fake_search(**kwargs):
        captured.update(kwargs)
        return ({"items": [], "count": 0, "query_type_detected": "title"}, 200)

    monkeypatch.setattr(
        "app.modules.literature_search.api.literature_search_service.search",
        _fake_search,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/literature_search",
            json={
                "query": "LiFePO4",
                "query_type": "title",
                "match_mode": "fuzzy",
                "sources": "both",
                "limit": 10,
            },
        )

    assert response.status_code == 200
    assert captured["query"] == "LiFePO4"
    assert captured["match_mode"] == "fuzzy"


def test_literature_search_requires_query(monkeypatch):
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/literature_search")
    assert response.status_code == 200
    assert response.json()["error"] == "缺少查询参数"
