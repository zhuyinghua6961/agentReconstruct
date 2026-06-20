from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.modules.literature_search.doi_search import search_by_doi
from app.modules.literature_search.service import literature_search_service


class _FakeCollection:
    def __init__(self, mapping: dict[str, dict]):
        self.mapping = mapping

    def get(self, *, where, include=None):
        _ = include
        doi = where.get("doi")
        metadata = self.mapping.get(doi)
        if not metadata:
            return {"ids": [], "metadatas": []}
        return {"ids": [doi], "metadatas": [metadata]}


def test_search_by_doi_exact_uses_chroma_hit():
    collection = _FakeCollection(
        {
            "10.1000/test": {"title": "Exact Paper", "doi": "10.1000/test"},
        }
    )
    hits = search_by_doi(
        query="10.1000/test",
        limit=5,
        fastqa_collection=collection,
        fastqa_md_collection=None,
        highthinking_collection=None,
        sources={"fastqa"},
        graph=None,
        logger=None,
    )
    assert len(hits) == 1
    assert hits[0]["doi"] == "10.1000/test"
    assert hits[0]["title"] == "Exact Paper"


def test_search_by_doi_exact_returns_empty_when_missing():
    hits = search_by_doi(
        query="10.1002/",
        limit=5,
        fastqa_collection=None,
        fastqa_md_collection=None,
        highthinking_collection=None,
        sources={"fastqa"},
        graph=None,
        logger=None,
    )
    assert hits == []


@patch("app.modules.literature_search.service.build_reference_preview_entry")
@patch("app.modules.literature_search.service.search_by_doi")
def test_literature_search_service_enriches_doi_hits(mock_search, mock_preview):
    mock_search.return_value = [
        {"doi": "10.1000/test", "title": "Paper", "match_source": "fastqa_chroma", "match_score": 1.0}
    ]
    mock_preview.return_value = {
        "doi": "10.1000/test",
        "title": "Paper",
        "journal": "J",
        "publication_date": "2024",
        "source": "chromadb",
        "pdf_exists": True,
        "pdf_url": "/api/v1/view_pdf/10.1000%2Ftest",
    }
    payload, status_code = literature_search_service.search(
        query="10.1000/test",
        query_type="doi",
        match_mode="semantic",
        sources="fastqa",
        limit=5,
        agent=SimpleNamespace(graph=None, semantic_expert=None),
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
        runtime=SimpleNamespace(
            agent=SimpleNamespace(graph=None, semantic_expert=None),
            settings=SimpleNamespace(data_root="/tmp"),
            highthinking_chroma=None,
        ),
    )
    assert status_code == 200
    assert payload["query_type_detected"] == "doi"
    assert payload["count"] == 1
    assert payload["items"][0]["pdf_exists"] is True
    assert payload["rerank"]["applied"] is False
