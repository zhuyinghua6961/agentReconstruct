from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.patent.browse_query import resolve_query_type
from server.patent.browse_search import PatentBrowseSearchService
from server.patent.retrieval_models import PatentCatalogRecord


def _catalog() -> list[PatentCatalogRecord]:
    return [
        PatentCatalogRecord(
            canonical_patent_id="CN123456789A",
            publication_number="CN123456789A",
            application_number="CN202410001234X",
            title="Battery thermal management system",
            abstract_text="A thermal control system for electric vehicle battery packs.",
            applicant_names=["Example Battery Co"],
            ipc_codes=["H01M10/613"],
            country="CN",
            kind_code="A",
            publication_date="2024-01-01",
            original_available=True,
        )
    ]


def _build_service(
    *,
    abstract_hits: list[dict] | None = None,
    chunk_hits: list[dict] | None = None,
    vector_enabled: bool = True,
) -> PatentBrowseSearchService:
    retrieval = MagicMock()
    retrieval._vector_search_enabled.return_value = vector_enabled  # noqa: SLF001
    retrieval._resolve_identifier.side_effect = lambda raw: "CN123456789A" if "CN123456789A" in str(raw).upper() else ""  # noqa: SLF001
    retrieval._ensure_catalog_record.side_effect = lambda patent_id: next(  # noqa: SLF001
        (item for item in _catalog() if item.canonical_patent_id == str(patent_id).upper()),
        None,
    )
    retrieval._run_abstract_vector_search.return_value = list(abstract_hits or [])  # noqa: SLF001
    retrieval._run_chunk_vector_search.return_value = list(chunk_hits or [])  # noqa: SLF001
    retrieval._metadata_candidates.return_value = []  # noqa: SLF001
    retrieval._normalize_patent_id.side_effect = lambda value: str(value or "").strip().upper()  # noqa: SLF001

    runtime = MagicMock()
    runtime.retrieval_service = retrieval
    runtime.resources = []
    return PatentBrowseSearchService(runtime=runtime)


def test_resolve_query_type_auto_detects_patent_id():
    assert resolve_query_type(query="CN123456789A", query_type="auto") == "patent_id"
    assert resolve_query_type(query="磷酸铁锂", query_type="auto") == "topic"


def test_patent_search_requires_query():
    service = _build_service()
    payload, status = service.search(query="")
    assert status == 200
    assert payload["error"] == "缺少查询参数"


def test_patent_search_topic_includes_rerank_metadata(monkeypatch):
    monkeypatch.setattr(
        "server.patent.browse_search.apply_patent_browse_rerank",
        lambda **kwargs: (
            kwargs["items"],
            {"enabled": True, "applied": True, "fallback": False},
        ),
    )
    service = _build_service(
        abstract_hits=[
            {
                "canonical_patent_id": "CN123456789A",
                "document": "thermal management snippet",
                "distance": 0.2,
            }
        ]
    )
    payload, status = service.search(query="battery thermal", query_type="topic", sources="abstract")
    assert status == 200
    assert payload["rerank"]["applied"] is True


def test_patent_search_exact_id_skips_rerank():
    service = _build_service()
    payload, status = service.search(query="CN123456789A", query_type="patent_id")
    assert status == 200
    assert payload["count"] == 1
    assert payload["items"][0]["canonical_patent_id"] == "CN123456789A"
    assert payload["items"][0]["match_score"] == 1.0
    assert payload["rerank"] == {"enabled": False, "applied": False, "fallback": False}


def test_patent_search_exact_id_returns_catalog_item():
    service = _build_service()
    payload, status = service.search(query="CN123456789A", query_type="patent_id")
    assert status == 200
    assert payload["count"] == 1
    assert payload["items"][0]["canonical_patent_id"] == "CN123456789A"
    assert payload["items"][0]["match_score"] == 1.0


def test_patent_search_topic_aggregates_abstract_hits():
    service = _build_service(
        abstract_hits=[
            {
                "canonical_patent_id": "CN123456789A",
                "document": "thermal management snippet",
                "distance": 0.2,
            }
        ]
    )
    payload, status = service.search(query="battery thermal", query_type="topic", sources="abstract")
    assert status == 200
    assert payload["count"] == 1
    assert payload["items"][0]["match_source"] == "patent_abstracts"
    assert payload["items"][0]["snippet"] == "thermal management snippet"


def test_patent_search_topic_merges_chunk_hits():
    service = _build_service(
        abstract_hits=[{"canonical_patent_id": "CN123456789A", "distance": 0.5}],
        chunk_hits=[
            {
                "canonical_patent_id": "CN123456789A",
                "document": "claim snippet",
                "distance": 0.1,
            }
        ],
    )
    payload, status = service.search(query="battery", query_type="topic", sources="both")
    assert status == 200
    assert payload["items"][0]["match_source"] == "patent_chunks"


def test_patent_search_without_runtime_returns_unavailable():
    service = PatentBrowseSearchService(runtime=None)
    payload, status = service.search(query="battery")
    assert status == 200
    assert payload["code"] == "RETRIEVAL_RUNTIME_UNAVAILABLE"
