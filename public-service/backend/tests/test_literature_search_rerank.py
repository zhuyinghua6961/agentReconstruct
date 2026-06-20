from __future__ import annotations

from unittest.mock import patch

from app.modules.literature_search.rerank_hits import apply_literature_rerank
from app.modules.literature_search.rerank_service import rerank_candidate_limit, rerank_configured


def test_rerank_candidate_limit_expands_when_configured(monkeypatch):
    monkeypatch.setenv("RERANK_BASE_URL", "http://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")
    assert rerank_configured() is True
    assert rerank_candidate_limit(10) == 30
    assert rerank_candidate_limit(20) == 50


def test_rerank_candidate_limit_matches_limit_when_disabled(monkeypatch):
    monkeypatch.delenv("RERANK_BASE_URL", raising=False)
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    assert rerank_candidate_limit(10) == 10


@patch("app.modules.literature_search.rerank_hits.rerank_documents")
def test_apply_literature_rerank_reorders_hits(mock_rerank, monkeypatch):
    monkeypatch.setenv("RERANK_BASE_URL", "http://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")
    hits = [
        {"doi": "10.1000/a", "title": "Alpha", "match_score": 0.7},
        {"doi": "10.1000/b", "title": "Beta", "match_score": 0.9},
    ]
    mock_rerank.return_value = {
        "fallback": False,
        "rerank_scores": [0.95, 0.4],
        "metadatas": [hits[1], hits[0]],
        "documents": ["Beta", "Alpha"],
    }
    ordered, meta = apply_literature_rerank(query="beta paper", hits=hits, limit=2, logger=None)
    assert meta["applied"] is True
    assert [item["doi"] for item in ordered] == ["10.1000/b", "10.1000/a"]
    assert ordered[0]["match_score"] == 0.95


@patch("app.modules.literature_search.rerank_hits.rerank_documents")
def test_apply_literature_rerank_falls_back_on_failure(mock_rerank, monkeypatch):
    monkeypatch.setenv("RERANK_BASE_URL", "http://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")
    hits = [
        {"doi": "10.1000/a", "title": "Alpha", "match_score": 0.7},
        {"doi": "10.1000/b", "title": "Beta", "match_score": 0.9},
    ]
    mock_rerank.return_value = {
        "fallback": True,
        "fallback_reason": "request_failed",
        "metadatas": [],
        "documents": [],
        "rerank_scores": [],
    }
    ordered, meta = apply_literature_rerank(query="beta paper", hits=hits, limit=1, logger=None)
    assert meta["fallback"] is True
    assert ordered[0]["doi"] == "10.1000/a"
