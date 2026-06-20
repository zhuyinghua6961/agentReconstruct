from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.modules.literature_search.title_search import search_by_title


class _FakeGraph:
    def __init__(self, rows):
        self.rows = rows

    def run(self, query, **kwargs):
        _ = query, kwargs
        return SimpleNamespace(data=lambda: self.rows)


@patch("app.modules.literature_search.title_search.scan_titles_from_chroma")
def test_search_by_title_fuzzy_uses_chroma_and_neo4j(mock_scan):
    mock_scan.return_value = [
        {"doi": "10.1000/a", "title": "LiFePO4 study", "match_source": "chroma_metadata", "match_score": 0.85}
    ]
    graph = _FakeGraph([{"doi": "10.1000/b", "title": "LiFePO4 battery"}])
    hits, error = search_by_title(
        query="LiFePO4",
        match_mode="fuzzy",
        limit=5,
        fastqa_collection=None,
        fastqa_md_collection=None,
        highthinking_collection=None,
        fastqa_db_path="/tmp/fastqa",
        fastqa_collection_name="lfp_papers",
        fastqa_md_db_path="/tmp/fastqa_md",
        fastqa_md_collection_name="md_papers",
        highthinking_db_path="/tmp/ht",
        highthinking_collection_name="lfp_markdown_qwen3_4096",
        sources={"fastqa"},
        graph=graph,
        logger=None,
    )
    assert error is None
    dois = {item["doi"] for item in hits}
    assert "10.1000/a" in dois
    assert "10.1000/b" in dois


@patch("app.modules.literature_search.title_search.embed_fastqa_query")
def test_search_by_title_semantic_dedupes_by_doi(mock_embed):
    mock_embed.return_value = [0.1, 0.2, 0.3]

    class _FakeCollection:
        def query(self, *, query_embeddings, n_results, include):
            _ = query_embeddings, n_results, include
            return {
                "metadatas": [[
                    {"doi": "10.1000/a", "title": "A1"},
                    {"doi": "10.1000/a", "title": "A2"},
                    {"doi": "10.1000/b", "title": "B"},
                ]],
                "distances": [[0.2, 0.4, 0.5]],
            }

    hits, error = search_by_title(
        query="battery materials",
        match_mode="semantic",
        limit=5,
        fastqa_collection=_FakeCollection(),
        fastqa_md_collection=None,
        highthinking_collection=None,
        fastqa_db_path="/tmp/fastqa",
        fastqa_collection_name="lfp_papers",
        fastqa_md_db_path="/tmp/fastqa_md",
        fastqa_md_collection_name="md_papers",
        highthinking_db_path="/tmp/ht",
        highthinking_collection_name="lfp_markdown_qwen3_4096",
        sources={"fastqa"},
        graph=None,
        logger=None,
    )
    assert error is None
    assert [item["doi"] for item in hits] == ["10.1000/a", "10.1000/b"]


@patch("app.modules.literature_search.title_search.embed_fastqa_query", side_effect=RuntimeError("down"))
def test_search_by_title_semantic_reports_embedding_unavailable(mock_embed):
    _ = mock_embed
    hits, error = search_by_title(
        query="battery",
        match_mode="semantic",
        limit=5,
        fastqa_collection=object(),
        fastqa_md_collection=None,
        highthinking_collection=None,
        fastqa_db_path="/tmp/fastqa",
        fastqa_collection_name="lfp_papers",
        fastqa_md_db_path="/tmp/fastqa_md",
        fastqa_md_collection_name="md_papers",
        highthinking_db_path="/tmp/ht",
        highthinking_collection_name="lfp_markdown_qwen3_4096",
        sources={"fastqa"},
        graph=None,
        logger=None,
    )
    assert hits == []
    assert error == "EMBEDDING_UNAVAILABLE"
