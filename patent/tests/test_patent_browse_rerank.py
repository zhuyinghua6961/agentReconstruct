from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.patent.browse_rerank import (
    apply_patent_browse_rerank,
    item_rerank_text,
    patent_browse_rerank_candidates,
    patent_browse_rerank_configured,
    patent_browse_rerank_enabled,
)


def test_item_rerank_text_prefers_title_and_abstract():
    text = item_rerank_text(
        {
            "title": "Battery pack",
            "abstract": "A thermal management system for EV batteries.",
            "canonical_patent_id": "CN123456789A",
        }
    )
    assert "Battery pack" in text
    assert "thermal management" in text


def test_patent_browse_rerank_disabled_without_config(monkeypatch):
    monkeypatch.delenv("RERANK_BASE_URL", raising=False)
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    monkeypatch.delenv("PATENT_STAGE2_RERANK_BASE_URL", raising=False)
    monkeypatch.delenv("PATENT_STAGE2_RERANK_MODEL", raising=False)
    assert patent_browse_rerank_configured() is False
    assert patent_browse_rerank_enabled() is False


def test_patent_browse_rerank_candidates_respects_limit(monkeypatch):
    monkeypatch.delenv("PATENT_SEARCH_RERANK_CANDIDATES", raising=False)
    assert patent_browse_rerank_candidates(limit=10) == 30
    monkeypatch.setenv("PATENT_SEARCH_RERANK_CANDIDATES", "40")
    assert patent_browse_rerank_candidates(limit=10) == 40


def test_apply_patent_browse_rerank_reorders_items(monkeypatch):
    monkeypatch.setenv("RERANK_BASE_URL", "http://rerank.local")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")
    monkeypatch.setenv("PATENT_SEARCH_RERANK_ENABLED", "1")

    items = [
        {"canonical_patent_id": "CN111111111A", "title": "First", "match_score": 0.9},
        {"canonical_patent_id": "CN222222222A", "title": "Second", "match_score": 0.8},
    ]

    def fake_rerank(*, query, documents, metadatas, top_n):
        assert query == "battery thermal"
        assert len(documents) == 2
        return {
            "metadatas": [metadatas[1], metadatas[0]],
            "rerank_scores": [0.95, 0.72],
            "fallback": False,
        }

    ranked, meta = apply_patent_browse_rerank(
        query="battery thermal",
        items=items,
        limit=2,
        rerank_fn=fake_rerank,
    )
    assert meta["applied"] is True
    assert ranked[0]["canonical_patent_id"] == "CN222222222A"
    assert ranked[0]["match_source"] == "patent_rerank"
    assert ranked[0]["match_score"] == 0.95


def test_apply_patent_browse_rerank_falls_back_on_error(monkeypatch):
    monkeypatch.setenv("RERANK_BASE_URL", "http://rerank.local")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")

    items = [
        {"canonical_patent_id": "CN111111111A", "title": "First", "match_score": 0.9},
        {"canonical_patent_id": "CN222222222A", "title": "Second", "match_score": 0.8},
    ]

    def fake_rerank(*, query, documents, metadatas, top_n):
        return {
            "metadatas": metadatas,
            "rerank_scores": [],
            "fallback": True,
            "fallback_reason": "timeout",
        }

    ranked, meta = apply_patent_browse_rerank(
        query="battery",
        items=items,
        limit=2,
        rerank_fn=fake_rerank,
    )
    assert meta["fallback"] is True
    assert meta["fallback_reason"] == "timeout"
    assert ranked[0]["canonical_patent_id"] == "CN111111111A"
