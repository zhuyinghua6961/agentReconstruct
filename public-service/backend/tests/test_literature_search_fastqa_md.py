from __future__ import annotations

from unittest.mock import patch

from app.modules.literature_search.doi_search import search_by_doi
from app.modules.literature_search.service import LiteratureSearchService


class _FakeMdCollection:
    def get(self, *, where, include=None, limit=None):
        _ = include, limit
        document_name = where.get("document_name")
        if document_name != "10.1000_test":
            return {"ids": [], "metadatas": []}
        return {"ids": ["md-1"], "metadatas": [{}]}


def test_search_by_doi_md_uses_document_name_lookup():
    hits = search_by_doi(
        query="10.1000/test",
        limit=5,
        fastqa_collection=None,
        fastqa_md_collection=_FakeMdCollection(),
        highthinking_collection=None,
        sources={"fastqa_md"},
        graph=None,
        logger=None,
    )
    assert len(hits) == 1
    assert hits[0]["doi"] == "10.1000/test"
    assert hits[0]["match_source"] == "fastqa_md_chroma"


def test_literature_search_resolve_sources_both_includes_md():
    service = LiteratureSearchService()
    assert service._resolve_sources("both") == {"fastqa", "fastqa_md", "highthinking"}


@patch("app.modules.literature_search.title_search.embed_fastqa_query")
def test_search_by_title_semantic_md_parses_document_name(mock_embed):
    from app.modules.literature_search.title_search import search_by_title

    mock_embed.return_value = [0.1, 0.2, 0.3]

    class _FakeMdCollection:
        def query(self, *, query_embeddings, n_results, include):
            _ = query_embeddings, n_results, include
            return {
                "metadatas": [[{"document_name": "10.1016_j.apenergy.2016.01.096"}]],
                "distances": [[0.3]],
            }

    hits, error = search_by_title(
        query="磷酸铁锂",
        match_mode="semantic",
        limit=5,
        fastqa_collection=None,
        fastqa_md_collection=_FakeMdCollection(),
        highthinking_collection=None,
        fastqa_db_path="/tmp/fastqa",
        fastqa_collection_name="lfp_papers",
        fastqa_md_db_path="/tmp/fastqa_md",
        fastqa_md_collection_name="md_papers",
        highthinking_db_path="/tmp/ht",
        highthinking_collection_name="lfp_markdown_qwen3_4096",
        sources={"fastqa_md"},
        graph=None,
        logger=None,
    )
    assert error is None
    assert hits[0]["doi"] == "10.1016/j.apenergy.2016.01.096"
    assert hits[0]["match_source"] == "fastqa_md_chroma"
