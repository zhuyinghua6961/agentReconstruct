from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.deps import AuthContext
from app.main import create_app
from app.modules.auth.deps import get_optional_auth_context


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


def test_literature_search_records_usage_on_success(monkeypatch):
    app = create_app()
    recorded: list[dict] = []

    def _fake_search(**kwargs):
        _ = kwargs
        return (
            {
                "items": [{"doi": "10.1000/test", "title": "Paper"}],
                "count": 1,
                "query_type_detected": "doi",
            },
            200,
        )

    def _fake_record_event(**kwargs):
        recorded.append(dict(kwargs))
        return {"success": True}

    monkeypatch.setattr(
        "app.modules.literature_search.api.literature_search_service.search",
        _fake_search,
    )
    monkeypatch.setattr(
        "app.modules.literature_search.api.usage_stats_service_module.usage_stats_service.record_event",
        _fake_record_event,
    )
    monkeypatch.setattr(
        "app.modules.literature_search.api._precheck_authenticated_doc_assist",
        lambda auth: None,
    )

    with TestClient(app) as client:
        client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        response = client.get(
            "/api/v1/literature_search",
            params={"query": "10.1000/test"},
            headers={"Authorization": "Bearer demo"},
        )

    assert response.status_code == 200
    assert len(recorded) == 1
    assert recorded[0]["event_type"] == "literature_search"


def test_literature_search_skips_usage_without_auth(monkeypatch):
    app = create_app()
    recorded: list[dict] = []

    monkeypatch.setattr(
        "app.modules.literature_search.api.literature_search_service.search",
        lambda **kwargs: ({"items": [{"doi": "10.1000/test"}], "count": 1}, 200),
    )
    monkeypatch.setattr(
        "app.modules.literature_search.api.usage_stats_service_module.usage_stats_service.record_event",
        lambda **kwargs: recorded.append(dict(kwargs)) or {"success": True},
    )
    monkeypatch.setattr(
        "app.modules.literature_search.api._precheck_authenticated_doc_assist",
        lambda auth: None,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/literature_search", params={"query": "10.1000/test"})

    assert response.status_code == 200
    assert recorded == []
