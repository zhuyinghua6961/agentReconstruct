from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from server.patent.cache_keys import PatentKeyFactory
from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.retrieval_models import (
    PatentCatalogRecord,
    PatentClaim,
    PatentDescriptionSnippet,
)
from server.patent.retrieval_service import PatentRetrievalService
from server.patent.runtime import PatentEmbeddingClient, PatentRuntime
from server.patent.stages.retrieval import run_stage2_targeted_retrieval
from server.services.execution_cache import ExecutionCache


@pytest.fixture(autouse=True)
def _disable_external_query_expander(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.expiry: dict[str, int | None] = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expiry[key] = ex
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        removed = self.store.pop(key, None)
        self.expiry.pop(key, None)
        return 1 if removed is not None else 0


def _catalog() -> list[PatentCatalogRecord]:
    return [
        PatentCatalogRecord(
            canonical_patent_id="CN123456789A",
            publication_number="CN123456789A",
            application_number="CN202410001234X",
            title="Battery thermal management system for electric vehicles",
            abstract_text="A thermal control system for electric vehicle battery packs.",
            applicant_names=["Example Battery Co"],
            inventor_names=["Alice Inventor"],
            ipc_codes=["H01M10/613"],
            cpc_codes=["H01M10/613"],
            claims=[
                PatentClaim(claim_number=1, text="A battery thermal management system configured for electric vehicles."),
            ],
            description_snippets=[
                PatentDescriptionSnippet(paragraph_id="p-001", text="The system balances battery temperature in electric vehicles."),
            ],
            country="CN",
            kind_code="A",
            publication_date="2024-01-01",
            provider="patent_source_x",
            original_available=True,
        ),
        PatentCatalogRecord(
            canonical_patent_id="US20240001234A1",
            publication_number="US20240001234A1",
            application_number="US18/000,123",
            title="Electrode manufacturing method",
            abstract_text="A process for manufacturing electrodes.",
            applicant_names=["Example Electrodes Inc"],
            inventor_names=["Bob Builder"],
            ipc_codes=["H01M4/13"],
            cpc_codes=["H01M4/13"],
            claims=[
                PatentClaim(claim_number=1, text="An electrode manufacturing method."),
            ],
            description_snippets=[
                PatentDescriptionSnippet(paragraph_id="p-010", text="A basic electrode process."),
                PatentDescriptionSnippet(paragraph_id="p-011", text="Anode porosity control reduces concentration polarization at high C-rate."),
            ],
            country="US",
            kind_code="A1",
            publication_date="2024-02-01",
            provider="patent_source_x",
            original_available=True,
        ),
    ]


def _service(*, redis: _FakeRedis | None = None, identity_registry: dict[str, str | None] | None = None) -> PatentRetrievalService:
    cache = ExecutionCache(redis, PatentKeyFactory(env="test")) if redis is not None else None
    return PatentRetrievalService(
        execution_cache=cache,
        identity_registry=identity_registry or {"CN123456789A": "CN123456789A"},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
    )


def test_exact_identifier_retrieval_returns_patent_evidence_and_original_links():
    outcome = _service().retrieve(question="Please summarize CN123456789A")

    assert outcome.retrieval_backend == "exact_id"
    assert outcome.retrieval_version == "retrieval-v1"
    assert outcome.catalog_index_version == "catalog-v1"
    assert outcome.references == ["CN123456789A"]
    assert outcome.reference_objects[0]["canonical_patent_id"] == "CN123456789A"
    assert outcome.reference_links[0]["viewer_uri"] == "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html"
    assert outcome.original_links[0]["section"] == "claim"


def test_exact_identifier_retrieval_supports_formatted_application_number():
    outcome = _service(
        identity_registry={
            "CN123456789A": "CN123456789A",
            "US18/000,123": "US20240001234A1",
        }
    ).retrieve(question="Please summarize US18/000,123")

    assert outcome.retrieval_backend == "exact_id"
    assert outcome.references == ["US20240001234A1"]
    assert outcome.reference_objects[0]["application_number"] == "US18/000,123"


def test_exact_identifier_retrieval_supports_catalog_identifier_index_fallback():
    outcome = _service(identity_registry={}).retrieve(question="Please summarize US18/000,123")

    assert outcome.retrieval_backend == "exact_id"
    assert outcome.references == ["US20240001234A1"]
    assert outcome.reference_objects[0]["application_number"] == "US18/000,123"


def test_metadata_lexical_retrieval_selects_best_metadata_match():
    outcome = _service(identity_registry={}).retrieve(question="Which patent covers battery thermal management for electric vehicles?")

    assert outcome.retrieval_backend == "metadata_lexical"
    assert outcome.references == ["CN123456789A"]
    assert outcome.evidences[0].title == "Battery thermal management system for electric vehicles"


def test_fulltext_lexical_retrieval_selects_claim_or_description_match():
    outcome = _service(identity_registry={}).retrieve(question="Which patent mentions anode porosity control at high C-rate?")

    assert outcome.retrieval_backend == "fulltext_lexical"
    assert outcome.references == ["US20240001234A1"]
    assert outcome.reference_objects[0]["section_type"] == "description"
    assert outcome.reference_objects[0]["anchor"] == {"claim_number": None, "paragraph_id": "p-011"}
    assert outcome.reference_links[0]["viewer_uri"] == "/api/patent/original/US20240001234A1?section=description&paragraph_id=p-011&format=html"
    assert outcome.original_links[0]["paragraph_id"] == "p-011"


def test_retrieval_cache_reports_hit_on_second_identical_query():
    redis = _FakeRedis()
    service = _service(redis=redis, identity_registry={})

    first = service.retrieve(question="Which patent covers battery thermal management for electric vehicles?")
    second = service.retrieve(question="Which patent covers battery thermal management for electric vehicles?")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.retrieval_backend == "metadata_lexical"


def test_retrieval_cache_key_changes_when_fulltext_top_k_changes():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    first_service = PatentRetrievalService(
        execution_cache=cache,
        identity_registry={},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
        top_k_fulltext=30,
    )
    second_service = PatentRetrievalService(
        execution_cache=cache,
        identity_registry={},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
        top_k_fulltext=5,
    )

    first = first_service.retrieve(question="Which patent mentions anode porosity control at high C-rate?")
    second = second_service.retrieve(question="Which patent mentions anode porosity control at high C-rate?")

    assert first.cache_hit is False
    assert second.cache_hit is False
    assert first.references == ["US20240001234A1"]
    assert second.references == ["US20240001234A1"]


def test_cached_fulltext_retrieval_preserves_matched_anchor_and_original_link():
    redis = _FakeRedis()
    service = _service(redis=redis, identity_registry={})

    first = service.retrieve(question="Which patent mentions anode porosity control at high C-rate?")
    second = service.retrieve(question="Which patent mentions anode porosity control at high C-rate?")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.reference_objects[0]["anchor"] == {"claim_number": None, "paragraph_id": "p-011"}
    assert second.original_links[0]["paragraph_id"] == "p-011"
    assert second.reference_links[0]["viewer_uri"] == "/api/patent/original/US20240001234A1?section=description&paragraph_id=p-011&format=html"


def test_negative_cache_is_used_for_missing_identifier_and_query_miss():
    redis = _FakeRedis()
    service = _service(redis=redis, identity_registry={"CN000000000A": None})

    first_id = service.retrieve(question="Please summarize CN000000000A")
    second_id = service.retrieve(question="Please summarize CN000000000A")
    first_query = service.retrieve(question="utterly unmatched patent query")
    second_query = service.retrieve(question="utterly unmatched patent query")

    assert first_id.not_found is True
    assert first_id.negative_cache_hit is False
    assert second_id.not_found is True
    assert second_id.negative_cache_hit is True
    assert first_query.not_found is True
    assert first_query.negative_cache_hit is False
    assert second_query.not_found is True
    assert second_query.negative_cache_hit is True


def test_retrieval_suppresses_original_links_when_original_is_unavailable():
    catalog = _catalog()
    unavailable = PatentCatalogRecord(
        canonical_patent_id="EP20240009999A1",
        publication_number="EP20240009999A1",
        application_number="EP24123456.7",
        title="Solid electrolyte interface stabilizer",
        abstract_text="A stabilizer for solid electrolyte interfaces.",
        applicant_names=["Example Electrolyte GmbH"],
        inventor_names=["Clara Chemist"],
        ipc_codes=["H01M10/0562"],
        cpc_codes=["H01M10/0562"],
        claims=[PatentClaim(claim_number=1, text="A stabilizer composition for a solid electrolyte interface.")],
        description_snippets=[PatentDescriptionSnippet(paragraph_id="p-020", text="The stabilizer suppresses interfacial side reactions.")],
        country="EP",
        kind_code="A1",
        publication_date="2024-03-01",
        provider="patent_source_x",
        original_available=False,
    )
    service = PatentRetrievalService(
        identity_registry={},
        catalog_records=[unavailable, *catalog],
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
    )

    outcome = service.retrieve(question="Which patent covers solid electrolyte interface stabilizer?")

    assert outcome.references == ["EP20240009999A1"]
    assert outcome.reference_objects[0]["original_available"] is False
    assert outcome.reference_links == []
    assert outcome.original_links == []


def test_metadata_retrieval_does_not_return_success_without_packageable_evidence():
    record = PatentCatalogRecord(
        canonical_patent_id="JP20240007777A",
        publication_number="JP20240007777A",
        application_number="JP2024-77777",
        title="Separator coating process",
        abstract_text="A process for coating a battery separator.",
        applicant_names=["Example Separator KK"],
        inventor_names=["Daisuke Inventor"],
        ipc_codes=["H01M50/449"],
        cpc_codes=["H01M50/449"],
        claims=[],
        description_snippets=[],
        country="JP",
        kind_code="A",
        publication_date="2024-04-01",
        provider="patent_source_x",
        original_available=True,
    )
    service = PatentRetrievalService(
        identity_registry={},
        catalog_records=[record],
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
    )

    outcome = service.retrieve(question="Which patent covers separator coating process?")

    assert outcome.not_found is True
    assert outcome.references == []
    assert outcome.reference_objects == []
    assert outcome.reference_links == []
    assert outcome.original_links == []


def test_vector_hybrid_retrieval_forces_table_supplements_for_matched_patent():
    abstract_hits = [
        {
            "patent_id": "CN115132975B",
            "abstract_score": 0.93,
            "kind": "abstract",
            "source_json": "CN115132975B_embedding.json",
        }
    ]
    chunk_hits = [
        {
            "patent_id": "CN115132975B",
            "chunk_score": 0.91,
            "source_file": "说明书.json",
            "json_stem": "CN115132975B",
            "chunk_index": 7,
            "document": "实施例表明 LMFP/LFP/三元复配能够同时改善高 SOC 充电安全性与低 SOC 放电功率。",
        }
    ]
    table_rows = [
        {
            "table_title": "表1 各实施例性能对比",
            "columns": ["实验序号", "α", "β", "γ"],
            "rows": [
                {"实验序号": "实施例1", "α": "1.08", "β": "0.91", "γ": "0.78"},
                {"实验序号": "实施例2", "α": "1.13", "β": "0.56", "γ": "1.45"},
            ],
            "patent_id": "CN115132975B",
        }
    ]

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                country="CN",
                kind_code="B",
                publication_date="2024-09-10",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: abstract_hits,
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_hits,
        table_loader=lambda canonical_patent_id: table_rows if canonical_patent_id == "CN115132975B" else [],
    )

    outcome = service.retrieve(question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？")

    assert outcome.not_found is False
    assert outcome.retrieval_backend == "vector_hybrid"
    assert outcome.references == ["CN115132975B"]
    assert outcome.reference_objects[0]["table_supplement_count"] == 1
    assert outcome.reference_objects[0]["table_supplements"][0]["table_title"] == "表1 各实施例性能对比"
    assert outcome.evidences[0].table_supplements[0].rows[0]["实验序号"] == "实施例1"


def test_targeted_retrieval_constrains_chunk_localization_to_abstract_recalled_candidate_ids():
    chunk_calls: list[tuple[str, list[str] | None]] = []
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                claims=[PatentClaim(claim_number=1, text="一种锂离子电池，其正极活性材料包括 LMFP。")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "CN115132975B", "abstract_score": 0.93}],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append((question, list(candidate_patent_ids or []))) or [
            {
                "patent_id": "CN115132975B",
                "chunk_score": 0.91,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 7,
                "document": "实施例表明 LMFP/LFP/三元复配能够同时改善高 SOC 充电安全性与低 SOC 放电功率。",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(
            candidate_recall_queries=["battery safety"],
            evidence_localization_queries=["high c-rate risk"],
        ),
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
    )

    assert chunk_calls == [("high c-rate risk", ["CN115132975B"])]
    assert payload["references"] == ["CN115132975B"]
    assert payload["source_ids"] == ["CN115132975B"]


def test_targeted_retrieval_merges_and_dedups_multi_query_results():
    def _chunk_search(question, candidate_patent_ids, top_k):
        if question == "query-a":
            return [
                {
                    "patent_id": "CN115132975B",
                    "chunk_score": 0.50,
                    "source_file": "说明书.json",
                    "json_stem": "CN115132975B",
                    "chunk_index": 1,
                    "document": "query-a evidence",
                }
            ]
        return [
            {
                "patent_id": "CN115132975B",
                "chunk_score": 0.95,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 2,
                "document": "query-b evidence",
            }
        ]

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                claims=[PatentClaim(claim_number=1, text="一种锂离子电池，其正极活性材料包括 LMFP。")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "CN115132975B", "abstract_score": 0.93}],
        chunk_vector_search=_chunk_search,
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(
            candidate_recall_queries=["query-a"],
            evidence_localization_queries=["query-a", "query-b"],
        ),
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
    )

    assert payload["references"] == ["CN115132975B"]
    assert len(payload["reference_objects"]) == 2
    assert payload["reference_objects"][0]["snippet"] == "query-b evidence"
    assert payload["reference_objects"][1]["snippet"] == "query-a evidence"


def test_targeted_retrieval_preserves_explicit_id_resolution_without_vector_search():
    service = _service(identity_registry={"US18/000,123": "US20240001234A1"})

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(explicit_patent_ids=["US18/000,123"]),
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
    )

    assert payload["references"] == ["US20240001234A1"]
    assert payload["source_ids"] == ["US20240001234A1"]


def test_targeted_retrieval_applies_graph_candidate_patent_filter():
    chunk_calls: list[list[str] | None] = []
    service = PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A", "US20240001234A1": "US20240001234A1"},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "US20240001234A1", "abstract_score": 0.9}],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append(list(candidate_patent_ids or [])) or [
            {
                "patent_id": "CN123456789A",
                "chunk_score": 0.9,
                "source_file": "说明书.json",
                "json_stem": "CN123456789A",
                "chunk_index": 1,
                "document": "graph filtered evidence",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(evidence_localization_queries=["graph query"]),
        user_question="graph question",
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert chunk_calls == [["CN123456789A"]]
    assert payload["metadata"]["graph_stage2_behavior"] == "filter_applied"
    assert payload["metadata"]["graph_candidate_patent_ids"] == ["CN123456789A"]


def test_targeted_retrieval_records_graph_no_hit_fallback():
    service = PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A"},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "US20240001234A1", "abstract_score": 0.9}],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(evidence_localization_queries=["graph query"]),
        user_question="battery thermal management",
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert payload["metadata"]["graph_stage2_behavior"] == "fallback_no_vector_hits"
    assert payload["metadata"]["graph_candidate_patent_ids"] == ["CN123456789A"]


def test_targeted_retrieval_claims_path_applies_graph_candidate_filter(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    chunk_calls: list[list[str] | None] = []
    service = PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A", "US20240001234A1": "US20240001234A1"},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "US20240001234A1", "abstract_score": 0.9, "document": "wrong abstract"}
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append(list(candidate_patent_ids or [])) or [
            {
                "patent_id": "CN123456789A",
                "chunk_score": 0.9,
                "source_file": "说明书.json",
                "json_stem": "CN123456789A",
                "chunk_index": 1,
                "document": "graph constrained claim evidence",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="graph claim", keywords=["graph"])],
        user_question="graph question",
        query_generation_fn=lambda *, user_question, retrieval_claim: ["graph claim query"],
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert chunk_calls == [["CN123456789A"]]
    assert payload["references"] == ["CN123456789A"]
    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["candidate_patent_ids"] == ["CN123456789A"]
    assert payload["metadata"]["graph_stage2_behavior"] == "filter_applied"
    assert payload["metadata"]["graph_candidate_patent_ids"] == ["CN123456789A"]


def test_targeted_retrieval_drops_off_graph_chunk_hits_when_backend_ignores_candidates():
    chunk_calls: list[list[str] | None] = []
    service = PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A", "US20240001234A1": "US20240001234A1"},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append(list(candidate_patent_ids or [])) or [
            {
                "patent_id": "US20240001234A1",
                "chunk_score": 0.9,
                "source_file": "说明书.json",
                "json_stem": "US20240001234A1",
                "chunk_index": 1,
                "document": "off graph evidence",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(evidence_localization_queries=["graph query"]),
        user_question="graph question",
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert chunk_calls == [["CN123456789A"]]
    assert payload["references"] == ["CN123456789A"]
    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["graph_stage2_behavior"] == "fallback_no_vector_hits"
    assert payload["metadata"]["graph_candidate_patent_ids"] == ["CN123456789A"]


def test_targeted_retrieval_returns_not_found_when_graph_candidates_have_no_anchor():
    service = PatentRetrievalService(
        identity_registry={"CN999999999A": "CN999999999A", "US20240001234A1": "US20240001234A1"},
        catalog_records=[_catalog()[1]],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [
            {
                "patent_id": "US20240001234A1",
                "chunk_score": 0.9,
                "source_file": "说明书.json",
                "json_stem": "US20240001234A1",
                "chunk_index": 1,
                "document": "off graph evidence",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(evidence_localization_queries=["graph query"]),
        user_question="US20240001234A1 graph question",
        context={"graph_kb": {"stage2_patent_candidates": ["CN999999999A"]}},
    )

    assert payload["references"] == []
    assert payload["source_ids"] == []
    assert payload["not_found"] is True
    assert payload["metadata"]["graph_stage2_behavior"] == "fallback_no_vector_hits"
    assert payload["metadata"]["graph_candidate_patent_ids"] == ["CN999999999A"]
    assert payload["metadata"]["candidate_patent_ids"] == ["CN999999999A"]


def test_targeted_retrieval_falls_back_to_archive_default_anchor_when_candidate_recall_is_confident_but_chunk_localization_is_empty():
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                claims=[PatentClaim(claim_number=1, text="一种用于动力车辆的锂离子电池。")],
                description_snippets=[PatentDescriptionSnippet(paragraph_id="p-001", text="该电池能够改善高 SOC 充电安全性。")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "CN115132975B", "abstract_score": 0.93}],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(
            candidate_recall_queries=["battery safety"],
            evidence_localization_queries=["high c-rate risk"],
        ),
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
    )

    assert payload["references"] == ["CN115132975B"]
    assert payload["metadata"]["localization_fallback"] == "archive_default_anchor"
    assert payload["reference_objects"][0]["section_type"] == "claim"


def test_targeted_retrieval_preserves_multiple_localized_snippets_for_same_patent():
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                claims=[PatentClaim(claim_number=1, text="一种用于动力车辆的锂离子电池。")],
                description_snippets=[
                    PatentDescriptionSnippet(paragraph_id="p-001", text="该电池能够改善高 SOC 充电安全性。"),
                    PatentDescriptionSnippet(paragraph_id="p-002", text="该电池能够提升低 SOC 放电功率。"),
                ],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "CN115132975B", "abstract_score": 0.93}],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [
            {
                "patent_id": "CN115132975B",
                "chunk_score": 0.91,
                "source_file": "权利要求.json",
                "json_stem": "CN115132975B",
                "chunk_index": 0,
                "document": "一种用于动力车辆的锂离子电池。",
            },
            {
                "patent_id": "CN115132975B",
                "chunk_score": 0.82,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 1,
                "document": "该电池能够提升低 SOC 放电功率。",
            },
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(
            candidate_recall_queries=["battery safety"],
            evidence_localization_queries=["battery safety", "low soc power"],
        ),
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
    )

    assert payload["references"] == ["CN115132975B"]
    assert len(payload["reference_objects"]) == 2
    assert {item["section_type"] for item in payload["reference_objects"]} == {"claim", "description"}


def test_targeted_retrieval_generates_query_per_claim_and_returns_unified_documents_metadata_distances(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    abstract_queries: list[str] = []
    chunk_calls: list[tuple[str, list[str] | None]] = []
    generated_calls: list[tuple[str, str]] = []

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                claims=[PatentClaim(claim_number=1, text="一种用于动力车辆的锂离子电池。")],
                description_snippets=[PatentDescriptionSnippet(paragraph_id="p-001", text="该电池能够改善高 SOC 充电安全性。")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: abstract_queries.append(question) or [
            {
                "patent_id": "CN115132975B",
                "distance": 0.08,
                "document": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            }
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append((question, list(candidate_patent_ids or []))) or [
            {
                "patent_id": "CN115132975B",
                "distance": 0.03,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 7,
                "document": "实施例表明 LMFP/LFP/三元复配能够同时改善高 SOC 充电安全性与低 SOC 放电功率。",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[
            PatentRetrievalClaim(
                claim="评估 LMFP 对 LFP 的替代窗口",
                keywords=["LMFP", "LFP", "替代窗口"],
                preferred_sections=["description", "tables"],
            )
        ],
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
        query_generation_fn=lambda *, user_question, retrieval_claim: generated_calls.append((user_question, retrieval_claim.claim)) or [
            "LMFP LFP 替代窗口 高 SOC 充电安全 低 SOC 放电功率"
        ],
    )

    assert generated_calls == [("从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？", "评估 LMFP 对 LFP 的替代窗口")]
    assert abstract_queries == ["lmfp LFP 替代窗口 高 soc 充电安全 低 放电功率 LiFePO4 磷酸铁锂"]
    assert chunk_calls == [("lmfp LFP 替代窗口 高 soc 充电安全 低 放电功率 LiFePO4 磷酸铁锂", ["CN115132975B"])]
    assert payload["source_ids"] == ["CN115132975B"]
    assert payload["documents"] == [
        "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
        "实施例表明 LMFP/LFP/三元复配能够同时改善高 SOC 充电安全性与低 SOC 放电功率。",
    ]
    assert payload["metadatas"][0]["patent_id"] == "CN115132975B"
    assert payload["metadatas"][0]["stage2_source"] == "abstract"
    assert payload["metadatas"][0]["generated_query"] == "lmfp LFP 替代窗口 高 soc 充电安全 低 放电功率 LiFePO4 磷酸铁锂"
    assert payload["metadatas"][1]["stage2_source"] == "chunk"
    assert payload["distances"] == [0.08, 0.03]


def test_targeted_retrieval_uses_oldcode_patent_dual_search_shape(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    abstract_calls: list[tuple[str, int]] = []
    chunk_calls: list[tuple[str, list[str] | None, int]] = []
    expander_inputs: list[str] = []

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LiFePO4 与 PEG 碳源改善倍率性能。",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        query_expander=lambda query: expander_inputs.append(query) or f"{query} overcharge",
        abstract_vector_search=lambda question, top_k: abstract_calls.append((question, top_k)) or [
            {
                "patent_id": "CN115132975B",
                "distance": 0.08,
                "document": "通过 LiFePO4 与 PEG 碳源改善倍率性能。",
            }
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append(
            (question, list(candidate_patent_ids) if candidate_patent_ids is not None else None, top_k)
        )
        or [
            {
                "patent_id": "CN115132975B",
                "distance": 0.03,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 7,
                "document": "实施例显示 PEG 聚乙二醇辅助碳包覆提升 LiFePO4 倍率性能。",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="PEG 碳源改善 LiFePO4 倍率性能", keywords=["PEG", "LiFePO4"])],
        user_question="PEG 对 LiFePO4 倍率性能有什么影响？",
        frozen_claim_queries=[["LiFePO4 PEG 倍率性能"]],
    )

    assert abstract_calls[0][1] == 25
    assert chunk_calls[0][1:] == (["CN115132975B"], 10)
    assert abstract_calls[0][0] == chunk_calls[0][0]
    assert expander_inputs == ["LiFePO4 peg 倍率性能 聚乙二醇 磷酸铁锂 LFP"]
    assert abstract_calls[0][0].endswith("overcharge")
    assert payload["documents"] == [
        "通过 LiFePO4 与 PEG 碳源改善倍率性能。",
        "实施例显示 PEG 聚乙二醇辅助碳包覆提升 LiFePO4 倍率性能。",
    ]


def test_targeted_retrieval_logs_detailed_stage2_diagnostics(monkeypatch, caplog):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_DIAGNOSTIC_LOG", "1")
    monkeypatch.setenv("QA_STAGE2_LOG_QUERY_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE2_LOG_HIT_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE2_LOG_HIT_MAX", "3")

    service = PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A"},
        catalog_records=_catalog(),
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {
                "patent_id": "CN123456789A",
                "distance": 0.08,
                "document": "一种电池热管理系统摘要。",
            }
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [
            {
                "patent_id": "CN123456789A",
                "distance": 0.03,
                "source_file": "说明书.json",
                "json_stem": "CN123456789A",
                "chunk_index": 7,
                "document": "实施例显示该系统能够平衡电池温度。",
            }
        ],
        query_expander=lambda query: query,
    )

    with caplog.at_level(logging.INFO, logger="patent.retrieval"):
        payload = service.targeted_retrieve(
            retrieval_claims=[PatentRetrievalClaim(claim="电池热管理 温度平衡", keywords=["电池", "热管理"])],
            user_question="如何评估电池热管理系统的温度平衡效果？",
            frozen_claim_queries=[["电池热管理 温度平衡"]],
        )

    assert payload["source_ids"] == ["CN123456789A"]
    messages = [record.message for record in caplog.records if record.name == "patent.retrieval"]
    assert any(
        "Patent Stage2 query encoding diagnostic" in message
        and "query_chars=" in message
        and "utf8_bytes=" in message
        and "chinese_chars=" in message
        and "has_replacement_char=false" in message
        and "has_mojibake_pattern=false" in message
        for message in messages
    )
    assert any(
        "Patent Stage2 vector search request" in message
        and "channel=abstract" in message
        and "top_k=" in message
        for message in messages
    )
    assert any(
        "Patent Stage2 raw hit detail" in message
        and "channel=abstract" in message
        and "rank=1" in message
        and "patent_id=CN123456789A" in message
        and "distance=0.08" in message
        for message in messages
    )
    assert any(
        "Patent Stage2 raw hit detail" in message
        and "channel=chunk" in message
        and "rank=1" in message
        and "patent_id=CN123456789A" in message
        and "distance=0.03" in message
        for message in messages
    )
    assert any(
        "Patent Stage2 diagnostic summary" in message
        and "generated_queries=1" in message
        and "selected_sources=1" in message
        and "source_ids=['CN123456789A']" in message
        for message in messages
    )


def test_patent_embedding_client_logs_embedding_diagnostics(monkeypatch, caplog):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    class _HttpClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def post(self, *args, **kwargs):
            return _Response()

        def close(self):
            return None

    monkeypatch.setenv("EMBEDDING_MODEL_TYPE", "remote")
    monkeypatch.setenv("EMBEDDING_API_URL", "http://embedding.example/v1/embeddings")
    monkeypatch.setenv("EMBEDDING_API_MODEL", "bge-test")
    monkeypatch.setattr("server.patent.runtime.httpx.Client", _HttpClient)

    with caplog.at_level(logging.INFO, logger="patent.runtime"):
        embeddings = PatentEmbeddingClient().encode(["电池热管理"])

    assert embeddings == [[0.1, 0.2, 0.3]]
    messages = [record.message for record in caplog.records if record.name == "patent.runtime"]
    assert any(
        "patent embedding diagnostic" in message
        and "mode=remote" in message
        and "model=bge-test" in message
        and "input_count=1" in message
        and "embedding_dim=3" in message
        and "empty_embedding=false" in message
        for message in messages
    )


def test_targeted_retrieval_global_chunk_when_abstract_has_no_patent_ids(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    chunk_calls: list[tuple[list[str] | None, int]] = []

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LiFePO4 与 PEG 碳源改善倍率性能。",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append(
            (list(candidate_patent_ids) if candidate_patent_ids is not None else None, top_k)
        )
        or [
            {
                "patent_id": "CN115132975B",
                "distance": 0.03,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 7,
                "document": "全局 chunk 检索命中 PEG 碳源证据。",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="PEG 碳源改善 LiFePO4 倍率性能", keywords=["PEG", "LiFePO4"])],
        user_question="PEG 对 LiFePO4 倍率性能有什么影响？",
        frozen_claim_queries=[["LiFePO4 PEG 倍率性能"]],
    )

    assert chunk_calls == [(None, 10)]
    assert payload["documents"] == ["全局 chunk 检索命中 PEG 碳源证据。"]


def test_targeted_retrieval_applies_rerank_inside_each_dual_vector_search(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    rerank_calls: list[dict[str, object]] = []

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B", "US20240001234A1": "US20240001234A1"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="abstract A",
            ),
            PatentCatalogRecord(
                canonical_patent_id="US20240001234A1",
                publication_number="US20240001234A1",
                application_number="US18/000,123",
                title="Electrode manufacturing method",
                abstract_text="abstract B",
            ),
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN115132975B", "distance": 0.40, "document": "abstract A"},
            {"patent_id": "US20240001234A1", "distance": 0.10, "document": "abstract B"},
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [
            {
                "patent_id": "CN115132975B",
                "distance": 0.30,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 1,
                "document": "chunk A",
            },
            {
                "patent_id": "US20240001234A1",
                "distance": 0.05,
                "source_file": "说明书.json",
                "json_stem": "US20240001234A1",
                "chunk_index": 2,
                "document": "chunk B",
            },
        ],
    )

    def _rerank(**kwargs):
        rerank_calls.append(kwargs)
        docs = list(kwargs.get("documents") or [])
        metas = list(kwargs.get("metadatas") or [])
        return {
            "documents": list(reversed(docs)),
            "metadatas": list(reversed(metas)),
            "rerank_scores": [0.99, 0.88],
            "fallback": False,
            "provider": "local",
        }

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="rerank dual search", keywords=[])],
        user_question="rerank dual search",
        frozen_claim_queries=[["rerank dual search"]],
        rerank_fn=_rerank,
    )

    assert len(rerank_calls) == 2
    assert [call["top_n"] for call in rerank_calls] == [2, 2]
    assert payload["documents"] == ["abstract B", "abstract A", "chunk B", "chunk A"]


def test_targeted_retrieval_fetches_50_candidates_before_internal_rerank(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    abstract_top_k_calls: list[int] = []
    chunk_calls: list[tuple[list[str] | None, int]] = []
    rerank_calls: list[dict[str, int]] = []

    def _abstract_hits(top_k: int) -> list[dict[str, object]]:
        return [
            {
                "patent_id": "CN115132975B",
                "distance": 0.5 - (index * 0.001),
                "document": f"abstract candidate {index}",
            }
            for index in range(top_k)
        ]

    def _chunk_hits(top_k: int) -> list[dict[str, object]]:
        return [
            {
                "patent_id": "CN115132975B",
                "distance": 0.4 - (index * 0.001),
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": index,
                "document": f"chunk candidate {index}",
            }
            for index in range(top_k)
        ]

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="abstract candidate 0",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: abstract_top_k_calls.append(top_k) or _abstract_hits(top_k),
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append(
            (list(candidate_patent_ids) if candidate_patent_ids is not None else None, top_k)
        )
        or _chunk_hits(top_k),
    )

    def _rerank(**kwargs):
        docs = list(kwargs.get("documents") or [])
        metas = list(kwargs.get("metadatas") or [])
        top_n = int(kwargs.get("top_n") or 0)
        rerank_calls.append({"candidate_count": len(docs), "top_n": top_n})
        return {
            "documents": docs[:top_n],
            "metadatas": metas[:top_n],
            "rerank_scores": [1.0 - (index * 0.01) for index in range(min(top_n, len(docs)))],
            "fallback": False,
            "provider": "local",
        }

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="候选池重排", keywords=[])],
        user_question="候选池重排",
        frozen_claim_queries=[["候选池重排"]],
        rerank_fn=_rerank,
    )

    assert abstract_top_k_calls == [50]
    assert chunk_calls == [(["CN115132975B"], 50)]
    assert rerank_calls == [
        {"candidate_count": 50, "top_n": 25},
        {"candidate_count": 50, "top_n": 10},
    ]
    assert len(payload["documents"]) == 35


def test_targeted_retrieval_dedupes_by_first_200_chars_without_resorting(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    shared_prefix = "同一段专利证据。" + "A" * 210

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="同一段专利证据。",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {
                "patent_id": "CN115132975B",
                "distance": 0.40,
                "document": f"{shared_prefix} 摘要版本",
            }
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [
            {
                "patent_id": "CN115132975B",
                "distance": 0.01,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 7,
                "document": f"{shared_prefix} chunk 版本",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="重复证据去重", keywords=[])],
        user_question="重复证据如何去重？",
        frozen_claim_queries=[["重复证据"]],
    )

    assert payload["documents"] == [f"{shared_prefix} 摘要版本"]


def test_targeted_retrieval_merges_multi_claim_results_and_dedups_by_document_prefix(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    query_to_chunk = {
        "query-a": [
            {
                "patent_id": "CN115132975B",
                "distance": 0.04,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 1,
                "document": "重复证据段。后缀A",
            },
            {
                "patent_id": "CN115132975B",
                "distance": 0.06,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 2,
                "document": "独立证据A",
            },
        ],
        "query-b": [
            {
                "patent_id": "CN115132975B",
                "distance": 0.02,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 3,
                "document": "重复证据段。后缀B",
            },
            {
                "patent_id": "US20240001234A1",
                "distance": 0.05,
                "source_file": "说明书.json",
                "json_stem": "US20240001234A1",
                "chunk_index": 4,
                "document": "独立证据B",
            },
        ],
    }

    service = PatentRetrievalService(
        identity_registry={
            "CN115132975B": "CN115132975B",
            "US20240001234A1": "US20240001234A1",
        },
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            ),
            PatentCatalogRecord(
                canonical_patent_id="US20240001234A1",
                publication_number="US20240001234A1",
                application_number="US18/000,123",
                title="Electrode manufacturing method",
                abstract_text="An electrode manufacturing process.",
            ),
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN115132975B", "distance": 0.10, "document": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"},
            {"patent_id": "US20240001234A1", "distance": 0.12, "document": "An electrode manufacturing process."},
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: query_to_chunk[question],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[
            PatentRetrievalClaim(claim="claim-a", keywords=["A"]),
            PatentRetrievalClaim(claim="claim-b", keywords=["B"]),
        ],
        user_question="user question",
        query_generation_fn=lambda *, user_question, retrieval_claim: ["query-a" if retrieval_claim.claim == "claim-a" else "query-b"],
    )

    assert payload["source_ids"] == ["CN115132975B", "US20240001234A1"]
    assert payload["documents"][:4] == [
        "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
        "An electrode manufacturing process.",
        "重复证据段。后缀A",
        "独立证据A",
    ]
    assert "重复证据段。后缀B" in payload["documents"]
    assert payload["documents"].count("通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。") == 1
    assert any(item["patent_id"] == "US20240001234A1" for item in payload["metadatas"])


def test_targeted_retrieval_parallel_matches_serial_output_and_order():
    query_to_chunk = {
        "query-a": [
            {
                "patent_id": "CN115132975B",
                "distance": 0.04,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 1,
                "document": "重复证据段。后缀A",
            },
            {
                "patent_id": "CN115132975B",
                "distance": 0.06,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 2,
                "document": "独立证据A",
            },
        ],
        "query-b": [
            {
                "patent_id": "CN115132975B",
                "distance": 0.02,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 3,
                "document": "重复证据段。后缀B",
            },
            {
                "patent_id": "US20240001234A1",
                "distance": 0.05,
                "source_file": "说明书.json",
                "json_stem": "US20240001234A1",
                "chunk_index": 4,
                "document": "独立证据B",
            },
        ],
    }

    service = PatentRetrievalService(
        identity_registry={
            "CN115132975B": "CN115132975B",
            "US20240001234A1": "US20240001234A1",
        },
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            ),
            PatentCatalogRecord(
                canonical_patent_id="US20240001234A1",
                publication_number="US20240001234A1",
                application_number="US18/000,123",
                title="Electrode manufacturing method",
                abstract_text="An electrode manufacturing process.",
            ),
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN115132975B", "distance": 0.10, "document": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"},
            {"patent_id": "US20240001234A1", "distance": 0.12, "document": "An electrode manufacturing process."},
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: query_to_chunk[question],
    )

    serial_payload = service.targeted_retrieve(
        retrieval_claims=[
            PatentRetrievalClaim(claim="claim-a", keywords=["A"]),
            PatentRetrievalClaim(claim="claim-b", keywords=["B"]),
        ],
        user_question="user question",
        frozen_claim_queries=[["query-a"], ["query-b"]],
        parallel_workers=1,
    )
    parallel_payload = service.targeted_retrieve(
        retrieval_claims=[
            PatentRetrievalClaim(claim="claim-a", keywords=["A"]),
            PatentRetrievalClaim(claim="claim-b", keywords=["B"]),
        ],
        user_question="user question",
        frozen_claim_queries=[["query-a"], ["query-b"]],
        parallel_workers=2,
    )

    assert serial_payload["documents"] == parallel_payload["documents"]
    assert serial_payload["metadatas"] == parallel_payload["metadatas"]
    assert serial_payload["source_ids"] == parallel_payload["source_ids"]
    assert serial_payload["metadata"]["retrieval_plan_queries"] == parallel_payload["metadata"]["retrieval_plan_queries"]


def test_targeted_retrieval_parallel_worker_one_falls_back_to_serial():
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                claims=[PatentClaim(claim_number=1, text="一种锂离子电池，其正极活性材料包括 LMFP。")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN115132975B", "distance": 0.10, "document": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"},
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [],
        query_expander=lambda query: query,
    )

    serial_payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["A"])],
        user_question="user question",
        frozen_claim_queries=[["query-a"]],
        parallel_workers=1,
    )
    parallel_payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["A"])],
        user_question="user question",
        frozen_claim_queries=[["query-a"]],
        parallel_workers=4,
    )

    assert serial_payload == parallel_payload


def test_targeted_retrieval_parallel_honors_explicit_should_cancel():
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN115132975B", "distance": 0.10, "document": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"},
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["A"])],
        user_question="user question",
        frozen_claim_queries=[["query-a"]],
        parallel_workers=2,
        should_cancel=lambda: True,
    )

    assert payload["documents"] == []
    assert payload["metadata"]["cancelled"] is True


def test_targeted_retrieval_parallel_midflight_cancel_returns_without_waiting():
    release = threading.Event()
    started = threading.Event()

    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: started.set() or release.wait(timeout=0.5) or [],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [],
    )

    started_at = time.perf_counter()
    try:
        payload = service.targeted_retrieve(
            retrieval_claims=[
                PatentRetrievalClaim(claim="claim-a", keywords=["A"]),
                PatentRetrievalClaim(claim="claim-b", keywords=["B"]),
            ],
            user_question="user question",
            frozen_claim_queries=[["query-a"], ["query-b"]],
            parallel_workers=2,
            should_cancel=lambda: started.is_set(),
        )
    finally:
        release.set()

    elapsed = time.perf_counter() - started_at
    assert elapsed < 0.3
    assert payload["documents"] == []
    assert payload["metadata"]["cancelled"] is True


@pytest.mark.parametrize("parallel_workers", [1, 2])
def test_targeted_retrieval_claim_local_failure_is_logged_and_other_claims_survive(monkeypatch, caplog, parallel_workers):
    service = PatentRetrievalService(
        identity_registry={
            "CN115132975B": "CN115132975B",
            "US20240001234A1": "US20240001234A1",
        },
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            ),
            PatentCatalogRecord(
                canonical_patent_id="US20240001234A1",
                publication_number="US20240001234A1",
                application_number="US18/000,123",
                title="Electrode manufacturing method",
                abstract_text="An electrode manufacturing process.",
            ),
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "CN115132975B", "distance": 0.10, "document": "摘要证据"}],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [
            {
                "patent_id": "CN115132975B" if question == "query-a" else "US20240001234A1",
                "distance": 0.04,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B" if question == "query-a" else "US20240001234A1",
                "chunk_index": 1,
                "document": "证据A" if question == "query-a" else "证据B",
            }
        ],
    )

    original_match_from_chunk_hit = service._match_from_chunk_hit

    def _failing_match_from_chunk_hit(hit):
        if hit.get("document") == "证据B":
            raise RuntimeError("bad claim")
        return original_match_from_chunk_hit(hit)

    monkeypatch.setattr(service, "_match_from_chunk_hit", _failing_match_from_chunk_hit)

    with caplog.at_level("WARNING", logger="patent.retrieval"):
        payload = service.targeted_retrieve(
            retrieval_claims=[
                PatentRetrievalClaim(claim="claim-a", keywords=["A"]),
                PatentRetrievalClaim(claim="claim-b", keywords=["B"]),
            ],
            user_question="user question",
            frozen_claim_queries=[["query-a"], ["query-b"]],
            parallel_workers=parallel_workers,
        )

    assert payload["references"] == ["CN115132975B"]
    assert "证据A" in payload["documents"]
    assert all("证据B" != item for item in payload["documents"])
    assert any("claim retrieval failed" in record.message for record in caplog.records)


def test_extract_source_ids_prefers_metadata_patent_id_order_from_stage2_payload():
    service = _service(identity_registry={})

    source_ids = service.extract_source_ids(
        {
            "documents": ["doc-a", "doc-b", "doc-c"],
            "metadatas": [
                {"patent_id": "US20240001234A1"},
                {"patent_id": "CN115132975B"},
                {"patent_id": "US20240001234A1"},
            ],
            "references": ["CN115132975B"],
        }
    )

    assert source_ids == ["US20240001234A1", "CN115132975B"]


def test_metadata_retrieval_only_hydrates_archive_fulltext_for_top_candidate():
    loaded_claims: list[str] = []
    loaded_descriptions: list[str] = []

    class _ArchiveLoader:
        def load_claims(self, canonical_patent_id: str):
            loaded_claims.append(canonical_patent_id)
            return [PatentClaim(claim_number=1, text=f"{canonical_patent_id} claim")]

        def load_description_snippets(self, canonical_patent_id: str):
            loaded_descriptions.append(canonical_patent_id)
            return [PatentDescriptionSnippet(paragraph_id="p-001", text=f"{canonical_patent_id} desc")]

    service = PatentRetrievalService(
        identity_registry={},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="LMFP battery safety platform",
                abstract_text="Improves charge safety.",
            ),
            PatentCatalogRecord(
                canonical_patent_id="US20240001234A1",
                publication_number="US20240001234A1",
                application_number="US18/000,123",
                title="Electrode manufacturing method",
                abstract_text="Improves electrode coating.",
            ),
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        archive_loader=_ArchiveLoader(),
    )

    outcome = service.retrieve(question="LMFP battery safety platform")

    assert outcome.references == ["CN115132975B"]
    assert loaded_claims == ["CN115132975B"]
    assert loaded_descriptions == ["CN115132975B"]


def test_retrieval_degrades_to_no_vector_path_when_vector_search_fails_at_request_time():
    vector_calls = {"abstract": 0, "chunk": 0}
    service = PatentRetrievalService(
        identity_registry={},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN123456789A",
                publication_number="CN123456789A",
                application_number="CN202410001234X",
                title="Battery thermal management system for electric vehicles",
                abstract_text="A thermal control system for electric vehicle battery packs.",
                claims=[PatentClaim(claim_number=1, text="A battery thermal management system configured for electric vehicles.")],
                description_snippets=[PatentDescriptionSnippet(paragraph_id="p-001", text="Battery temperature control.")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: vector_calls.__setitem__("abstract", vector_calls["abstract"] + 1) or (_ for _ in ()).throw(RuntimeError("embedding endpoint unavailable")),
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: vector_calls.__setitem__("chunk", vector_calls["chunk"] + 1) or [],
    )

    first = service.retrieve(question="Which patent covers battery thermal management for electric vehicles?")
    second = service.retrieve(question="Which patent covers battery thermal management for electric vehicles?")

    assert first.retrieval_backend == "metadata_lexical"
    assert second.retrieval_backend == "metadata_lexical"
    assert first.references == ["CN123456789A"]
    assert second.references == ["CN123456789A"]
    assert vector_calls == {"abstract": 1, "chunk": 0}


def test_targeted_retrieval_parallel_keeps_vector_degrade_semantics():
    vector_calls = {"abstract": 0, "chunk": 0}
    service = PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN123456789A",
                publication_number="CN123456789A",
                application_number="CN202410001234X",
                title="Battery thermal management system for electric vehicles",
                abstract_text="A thermal control system for electric vehicle battery packs.",
                claims=[PatentClaim(claim_number=1, text="A battery thermal management system configured for electric vehicles.")],
                description_snippets=[PatentDescriptionSnippet(paragraph_id="p-001", text="Battery temperature control.")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: vector_calls.__setitem__("abstract", vector_calls["abstract"] + 1) or (_ for _ in ()).throw(RuntimeError("embedding endpoint unavailable")),
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: vector_calls.__setitem__("chunk", vector_calls["chunk"] + 1) or [],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["thermal management"])],
        user_question="Which patent covers battery thermal management for electric vehicles?",
        frozen_claim_queries=[["battery thermal management electric vehicles"]],
        parallel_workers=2,
    )

    assert service._vector_runtime_enabled is False
    assert payload["references"] == ["CN123456789A"]
    assert vector_calls == {"abstract": 1, "chunk": 0}


def test_targeted_retrieval_claim_path_does_not_call_answer_builder():
    answer_builder_calls: list[dict[str, object]] = []
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN115132975B", "distance": 0.10, "document": "摘要证据"}
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [],
        answer_builder=lambda **kwargs: answer_builder_calls.append(kwargs) or "stage2 should not build answer",
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["A"])],
        user_question="user question",
        frozen_claim_queries=[["query-a"]],
        parallel_workers=2,
    )

    assert payload["references"] == ["CN115132975B"]
    assert answer_builder_calls == []
    assert "answer_build_ms" not in payload["timings"]


def test_targeted_retrieval_plan_path_does_not_call_answer_builder():
    answer_builder_calls: list[dict[str, object]] = []
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN115132975B", "distance": 0.10, "document": "摘要证据"}
        ],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [
            {
                "patent_id": "CN115132975B",
                "distance": 0.04,
                "source_file": "说明书.json",
                "json_stem": "CN115132975B",
                "chunk_index": 1,
                "document": "说明书证据",
            }
        ],
        answer_builder=lambda **kwargs: answer_builder_calls.append(kwargs) or "stage2 should not build answer",
    )

    payload = service.targeted_retrieve(
        retrieval_plan={
            "candidate_recall_queries": ["battery thermal management"],
            "evidence_localization_queries": ["battery thermal management"],
            "explicit_patent_ids": [],
            "preferred_sections": [],
            "filters": {},
        },
        user_question="Which patent covers battery thermal management?",
    )

    assert payload["references"] == ["CN115132975B"]
    assert answer_builder_calls == []
    assert "answer_build_ms" not in payload["timings"]


def test_targeted_retrieval_claim_fallback_path_does_not_call_answer_builder():
    answer_builder_calls: list[dict[str, object]] = []
    service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="LMFP battery safety platform",
                abstract_text="Improves charge safety.",
                claims=[PatentClaim(claim_number=1, text="A battery safety platform for LMFP cells.")],
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: [],
        answer_builder=lambda **kwargs: answer_builder_calls.append(kwargs) or "stage2 should not build answer",
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[PatentRetrievalClaim(claim="unmatched claim", keywords=["unmatched"])],
        user_question="LMFP battery safety platform",
        frozen_claim_queries=[["unmatched query"]],
        parallel_workers=2,
    )

    assert payload["references"] == ["CN115132975B"]
    assert answer_builder_calls == []
    assert "answer_build_ms" not in payload["timings"]


def test_build_default_patent_runtime_builds_no_vector_lexical_catalog_from_archive(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PATENT_ORIGINAL_MINIO_ONLY", "false")
    resource_root = tmp_path / "resource" / "patentQA"
    archive_dir = resource_root / "__archive__"
    patent_dir = archive_dir / "CN115132975B"
    patent_dir.mkdir(parents=True)
    (patent_dir / "著录项目.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "pn": "CN115132975B",
                        "bibliographic_data": {
                            "publication_reference": {"country": "CN", "kind": "B", "doc_number": "115132975", "date": "2022-10-01"},
                            "application_reference": {"doc_number": "CN202110320984.1"},
                            "invention_title": [{"text": "一种锂离子电池及动力车辆"}],
                            "abstracts": [{"text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (patent_dir / "权利要求.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "claims": [
                            {"claim_text": '<div num="1">一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。</div>'}
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (patent_dir / "说明书.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "description": [
                            {"text": '<b class="d_n">[0001]</b>该电池能够改善高 SOC 充电安全性。<b class="d_n">[0002]</b>该电池能够提升低 SOC 放电功率。'}
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    class _AnswerBuilder:
        def __call__(self, **kwargs):
            return ""

        def close(self):
            return None

    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_dir,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda: _AnswerBuilder())

    runtime = build_default_patent_runtime()

    outcome = runtime.retrieval_service.retrieve(question="哪篇专利提到低 SOC 放电功率？")

    assert runtime is not None
    assert outcome.not_found is False
    assert outcome.retrieval_backend == "fulltext_lexical"
    assert outcome.references == ["CN115132975B"]


def test_build_default_patent_runtime_wires_injected_execution_cache_into_real_retrieval_service(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PATENT_ORIGINAL_MINIO_ONLY", "false")
    resource_root = tmp_path / "resource" / "patentQA"
    archive_dir = resource_root / "__archive__"
    patent_dir = archive_dir / "CN115132975B"
    patent_dir.mkdir(parents=True)
    (patent_dir / "著录项目.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "pn": "CN115132975B",
                        "bibliographic_data": {
                            "publication_reference": {"country": "CN", "kind": "B", "doc_number": "115132975", "date": "2022-10-01"},
                            "application_reference": {"doc_number": "CN202110320984.1"},
                            "invention_title": [{"text": "一种锂离子电池及动力车辆"}],
                            "abstracts": [{"text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (patent_dir / "权利要求.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "claims": [
                            {"claim_text": '<div num="1">一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。</div>'}
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (patent_dir / "说明书.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "description": [
                            {"text": '<b class="d_n">[0001]</b>该电池能够改善高 SOC 充电安全性。<b class="d_n">[0002]</b>该电池能够提升低 SOC 放电功率。'}
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    class _AnswerBuilder:
        def __call__(self, **kwargs):
            return ""

        def close(self):
            return None

    cache = ExecutionCache(_FakeRedis(), PatentKeyFactory(env="test"))
    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_dir,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda: _AnswerBuilder())

    runtime = build_default_patent_runtime(execution_cache=cache)
    first = runtime.retrieval_service.retrieve(question="哪篇专利提到低 SOC 放电功率？")
    second = runtime.retrieval_service.retrieve(question="哪篇专利提到低 SOC 放电功率？")

    assert runtime is not None
    assert runtime.retrieval_service._execution_cache is cache
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.references == ["CN115132975B"]


def test_runtime_stage2_targeted_retrieval_delegates_to_patent_retrieval_stage_and_extracts_source_ids():
    runtime = PatentRuntime(
        retrieval_service=_service(identity_registry={"CN123456789A": "CN123456789A"}),
        resources=[],
    )

    payload = runtime.stage2_targeted_retrieval(
        PatentRetrievalPlan(explicit_patent_ids=["CN123456789A"]),
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
    )

    assert payload["references"] == ["CN123456789A"]
    assert runtime._extract_patent_ids_from_results(payload) == ["CN123456789A"]


def test_runtime_stage2_targeted_retrieval_passes_parallel_workers_and_should_cancel(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_stage2_targeted_retrieval(**kwargs):
        captured.update(kwargs)
        return {
            "references": ["CN123456789A"],
            "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
            "metadata": {},
        }

    monkeypatch.setattr("server.patent.runtime.run_stage2_targeted_retrieval", _fake_run_stage2_targeted_retrieval)

    runtime = PatentRuntime(
        retrieval_service=_service(identity_registry={"CN123456789A": "CN123456789A"}),
        resources=[],
        stage2_parallel_workers=6,
    )
    should_cancel = object()

    runtime.stage2_targeted_retrieval(
        [PatentRetrievalClaim(claim="claim-a", keywords=["A"])],
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
        should_cancel=should_cancel,
        active_stream_count=9,
    )

    assert captured["should_cancel"] is should_cancel
    assert captured["active_stream_count"] == 9
    assert captured["parallel_workers"] == 6


def test_patent_runtime_stage2_uses_planning_hot_pool_query_client(monkeypatch):
    captured: dict[str, object] = {}
    fallback_query_client = object()
    hot_query_client = object()

    class _PlanningHotPool:
        def __init__(self) -> None:
            self.proxy_calls: list[object] = []

        def proxy_client(self, *, fallback_client=None):
            self.proxy_calls.append(fallback_client)
            return hot_query_client

    def _fake_run_stage2_targeted_retrieval(**kwargs):
        captured.update(kwargs)
        return {
            "references": ["CN123456789A"],
            "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
            "metadata": {},
        }

    monkeypatch.setattr("server.patent.runtime.run_stage2_targeted_retrieval", _fake_run_stage2_targeted_retrieval)

    hot_pool = _PlanningHotPool()
    runtime = PatentRuntime(
        retrieval_service=_service(identity_registry={"CN123456789A": "CN123456789A"}),
        resources=[],
        planning_client=fallback_query_client,
        planning_hot_pool=hot_pool,
        planning_model="planner-model",
    )

    runtime.stage2_targeted_retrieval(
        [PatentRetrievalClaim(claim="claim-a", keywords=["a"])],
        user_question="user question",
    )

    assert captured["query_client"] is hot_query_client
    assert hot_pool.proxy_calls == [fallback_query_client]


def test_patent_runtime_stage2_without_hot_pool_uses_configured_planning_client(monkeypatch):
    captured: dict[str, object] = {}
    configured_query_client = object()

    def _fake_run_stage2_targeted_retrieval(**kwargs):
        captured.update(kwargs)
        return {
            "references": ["CN123456789A"],
            "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
            "metadata": {},
        }

    monkeypatch.setattr("server.patent.runtime.run_stage2_targeted_retrieval", _fake_run_stage2_targeted_retrieval)

    runtime = PatentRuntime(
        retrieval_service=_service(identity_registry={"CN123456789A": "CN123456789A"}),
        resources=[],
        planning_client=configured_query_client,
        planning_model="planner-model",
    )

    runtime.stage2_targeted_retrieval(
        [PatentRetrievalClaim(claim="claim-a", keywords=["a"])],
        user_question="user question",
    )

    assert captured["query_client"] is configured_query_client


def test_patent_runtime_stage2_enters_the_gate(monkeypatch):
    captured: dict[str, object] = {}
    configured_query_client = object()
    should_cancel = object()

    class _Gate:
        def __init__(self) -> None:
            self.proxy_calls: list[dict[str, object]] = []
            self.query_client = object()

        def proxy_client(self, *, base_client=None, trace_label="", should_cancel=None):
            self.proxy_calls.append(
                {
                    "base_client": base_client,
                    "trace_label": trace_label,
                    "should_cancel": should_cancel,
                }
            )
            return self.query_client

    def _fake_run_stage2_targeted_retrieval(**kwargs):
        captured.update(kwargs)
        return {
            "references": ["CN123456789A"],
            "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
            "metadata": {},
        }

    monkeypatch.setattr("server.patent.runtime.run_stage2_targeted_retrieval", _fake_run_stage2_targeted_retrieval)

    gate = _Gate()
    runtime = PatentRuntime(
        retrieval_service=_service(identity_registry={"CN123456789A": "CN123456789A"}),
        resources=[],
        planning_client=configured_query_client,
        planning_upstream_gate=gate,
        planning_model="planner-model",
    )

    runtime.stage2_targeted_retrieval(
        [PatentRetrievalClaim(claim="claim-a", keywords=["a"])],
        user_question="user question",
        should_cancel=should_cancel,
    )

    assert gate.proxy_calls == [
        {
            "base_client": configured_query_client,
            "trace_label": "stage2_query_generation",
            "should_cancel": should_cancel,
        }
    ]
    assert captured["query_client"] is gate.query_client


def test_patent_runtime_stage2_bypass_the_gate_when_disabled(monkeypatch):
    captured: dict[str, object] = {}
    configured_query_client = object()

    def _fake_run_stage2_targeted_retrieval(**kwargs):
        captured.update(kwargs)
        return {
            "references": ["CN123456789A"],
            "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
            "metadata": {},
        }

    monkeypatch.setattr("server.patent.runtime.run_stage2_targeted_retrieval", _fake_run_stage2_targeted_retrieval)

    runtime = PatentRuntime(
        retrieval_service=_service(identity_registry={"CN123456789A": "CN123456789A"}),
        resources=[],
        planning_client=configured_query_client,
        planning_model="planner-model",
    )

    runtime.stage2_targeted_retrieval(
        [PatentRetrievalClaim(claim="claim-a", keywords=["a"])],
        user_question="user question",
    )

    assert captured["query_client"] is configured_query_client


def test_run_stage2_targeted_retrieval_passes_active_stream_count_to_service():
    captured: dict[str, object] = {}

    class _RetrievalService:
        def targeted_retrieve(self, **kwargs):
            captured.update(kwargs)
            return {
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "metadata": {},
            }

    run_stage2_targeted_retrieval(
        retrieval_service=_RetrievalService(),
        retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["A"])],
        user_question="user question",
        query_client=None,
        query_model="",
        logger=None,
        should_cancel=None,
        active_stream_count=9,
        parallel_workers=2,
    )

    assert captured["active_stream_count"] == 9


def test_run_stage2_targeted_retrieval_logs_parallel_workers(caplog):
    class _RetrievalService:
        def targeted_retrieve(self, **kwargs):
            return {
                "source_ids": ["CN123456789A"],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "metadata": {"retrieval_plan_queries": ["query-a"]},
            }

    class _Logger:
        def info(self, message, *args):
            import logging

            logging.getLogger("patent.retrieval.test").info(message, *args)

        def warning(self, message, *args):
            import logging

            logging.getLogger("patent.retrieval.test").warning(message, *args)

    with caplog.at_level("INFO", logger="patent.retrieval.test"):
        run_stage2_targeted_retrieval(
            retrieval_service=_RetrievalService(),
            retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["a"])],
            user_question="user question",
            query_client=None,
            query_model="planner-model",
            logger=_Logger(),
            parallel_workers=3,
        )

    messages = [record.message for record in caplog.records if record.name == "patent.retrieval.test"]
    assert any(
        "patent stage2 targeted retrieval start" in message
        and "claim_count=1" in message
        and "query_model=planner-model" in message
        and "parallel_workers=3" in message
        for message in messages
    )


def test_patent_runtime_direct_construction_keeps_safe_parallel_worker_defaults():
    runtime = PatentRuntime(
        retrieval_service=_service(identity_registry={"CN123456789A": "CN123456789A"}),
        resources=[],
    )

    assert runtime.stage2_parallel_workers >= 1
    assert runtime.stage3_parallel_workers >= 1


def test_build_default_patent_runtime_reads_parallel_worker_envs(monkeypatch, tmp_path: Path):
    resource_root = tmp_path / "resource" / "patentQA"
    archive_dir = resource_root / "__archive__"
    archive_dir.mkdir(parents=True)

    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    class _AnswerBuilder:
        def __call__(self, **kwargs):
            return ""

        def close(self):
            return None

    monkeypatch.setenv("PATENT_STAGE2_PARALLEL_WORKERS", "4")
    monkeypatch.setenv("PATENT_STAGE3_PARALLEL_WORKERS", "3")
    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_dir,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda: _AnswerBuilder())

    runtime = build_default_patent_runtime()

    assert runtime.stage2_parallel_workers == 4
    assert runtime.stage3_parallel_workers == 3


def test_stage2_query_generation_is_frozen_serially_before_parallel_dispatch(monkeypatch):
    generated_claims: list[str] = []
    captured: dict[str, object] = {}

    class _RetrievalService:
        def targeted_retrieve(self, **kwargs):
            captured.update(kwargs)
            return {
                "source_ids": ["CN115132975B"],
                "references": ["CN115132975B"],
                "metadata": {"retrieval_plan_queries": ["query:claim-a", "query:claim-b", "query:claim-c"]},
            }

    class _Logger:
        def info(self, *args, **kwargs):
            return None

    def _fake_build_queries(*, user_question, retrieval_claim, client, model, logger):
        del user_question, client, model, logger
        generated_claims.append(retrieval_claim.claim)
        return [f"query:{retrieval_claim.claim}"]

    monkeypatch.setattr("server.patent.stages.retrieval.build_stage2_queries_for_claim", _fake_build_queries)

    run_stage2_targeted_retrieval(
        retrieval_service=_RetrievalService(),
        retrieval_claims=[
            PatentRetrievalClaim(claim="claim-a", keywords=["a"]),
            PatentRetrievalClaim(claim="claim-b", keywords=["b"]),
            PatentRetrievalClaim(claim="claim-c", keywords=["c"]),
        ],
        user_question="user question",
        query_client=object(),
        query_model="planner-model",
        logger=_Logger(),
    )

    assert generated_claims == ["claim-a", "claim-b", "claim-c"]
    assert captured["query_generation_fn"] is None
    assert captured["frozen_claim_queries"] == [
        ["query:claim-a"],
        ["query:claim-b"],
        ["query:claim-c"],
    ]


def test_stage2_query_generation_freeze_does_not_depend_on_logger(monkeypatch):
    captured: dict[str, object] = {}

    class _RetrievalService:
        def targeted_retrieve(self, **kwargs):
            captured.update(kwargs)
            return {
                "source_ids": ["CN115132975B"],
                "references": ["CN115132975B"],
                "metadata": {"retrieval_plan_queries": ["query:claim-a"]},
            }

    def _fake_build_queries(*, user_question, retrieval_claim, client, model, logger):
        del user_question, client, model, logger
        return [f"query:{retrieval_claim.claim}"]

    monkeypatch.setattr("server.patent.stages.retrieval.build_stage2_queries_for_claim", _fake_build_queries)

    run_stage2_targeted_retrieval(
        retrieval_service=_RetrievalService(),
        retrieval_claims=[PatentRetrievalClaim(claim="claim-a", keywords=["a"])],
        user_question="user question",
        query_client=object(),
        query_model="planner-model",
        logger=None,
    )

    assert captured["query_generation_fn"] is None
    assert captured["frozen_claim_queries"] == [["query:claim-a"]]


def test_targeted_retrieval_keeps_explicit_patent_ids_authoritative_even_when_vector_search_is_enabled():
    chunk_calls: list[list[str] | None] = []
    service = PatentRetrievalService(
        identity_registry={
            "CN123456789A": "CN123456789A",
            "US20240001234A1": "US20240001234A1",
        },
        catalog_records=_catalog(),
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=lambda question, top_k: [{"patent_id": "US20240001234A1", "abstract_score": 0.99}],
        chunk_vector_search=lambda question, candidate_patent_ids, top_k: chunk_calls.append(list(candidate_patent_ids or [])) or [
            {
                "patent_id": "CN123456789A",
                "chunk_score": 0.91,
                "source_file": "说明书.json",
                "json_stem": "CN123456789A",
                "chunk_index": 0,
                "document": "The system balances battery temperature in electric vehicles.",
            }
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_plan=PatentRetrievalPlan(
            explicit_patent_ids=["CN123456789A"],
            candidate_recall_queries=["battery safety"],
            evidence_localization_queries=["battery safety"],
        ),
        user_question="从专利角度如何评估 LMFP 对 LFP 的替代窗口和风险？",
    )

    assert chunk_calls == [["CN123456789A"]]
    assert payload["references"] == ["CN123456789A"]


def test_stage2_convergence_rerank_failure_falls_back_with_metadata(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "battery thermal abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "battery thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2},
        ],
    )

    def _broken_rerank(**kwargs):
        raise RuntimeError("rerank down")

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "battery thermal", "keywords": []}],
        user_question="battery thermal",
        frozen_claim_queries=[["battery thermal"]],
        rerank_fn=_broken_rerank,
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["stage2_rerank"]["fallback_reason"] == "request_failed"


def test_stage2_convergence_rerank_adapter_fallback_is_not_reported_as_applied(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "battery thermal abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "battery thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2},
        ],
    )

    def _fallback_rerank(**kwargs):
        return {
            "documents": list(kwargs.get("documents") or []),
            "metadatas": list(kwargs.get("metadatas") or []),
            "rerank_scores": [1.0],
            "fallback": True,
            "fallback_reason": "request_failed",
            "provider": "local",
        }

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "battery thermal", "keywords": []}],
        user_question="battery thermal",
        frozen_claim_queries=[["battery thermal"]],
        rerank_fn=_fallback_rerank,
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["stage2_rerank"]["applied"] is False
    assert payload["metadata"]["stage2_rerank"]["fallback"] is True
    assert payload["metadata"]["stage2_rerank"]["fallback_reason"] == "request_failed"
    assert payload["metadata"]["stage2_rerank"]["provider"] == "local"


def test_stage2_convergence_rerank_success_reorders_and_limits_patents(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TOP_PATENTS", "1")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "1")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
        ],
    )

    def _fake_rerank(*, query, documents, metadatas, top_n, **kwargs):
        del query, top_n, kwargs
        return {
            "documents": [documents[1]],
            "metadatas": [metadatas[1]],
            "rerank_scores": [0.99],
            "fallback": False,
            "provider": "fake",
        }

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "electrode", "keywords": []}],
        user_question="electrode",
        frozen_claim_queries=[["electrode"]],
        rerank_fn=_fake_rerank,
    )

    assert payload["source_ids"] == ["US20240001234A1"]
    assert payload["references"] == ["US20240001234A1"]
    assert payload["metadata"]["stage2_rerank"]["applied"] is True
    assert payload["metadata"]["stage2_rerank"]["provider"] == "fake"


def test_runtime_stage2_targeted_retrieval_passes_rerank_fn_to_wrapper(monkeypatch):
    captured = {}

    def _fake_run_stage2_targeted_retrieval(**kwargs):
        captured.update(kwargs)
        return {"documents": [], "metadatas": [], "distances": [], "references": [], "source_ids": [], "metadata": {}}

    monkeypatch.setattr("server.patent.runtime.run_stage2_targeted_retrieval", _fake_run_stage2_targeted_retrieval)

    runtime = PatentRuntime(
        retrieval_service=_service(),
        resources=[],
        planning_client=None,
        planning_model="",
    )

    def _rerank(**kwargs):
        return {"documents": [], "metadatas": [], "rerank_scores": []}

    runtime.stage2_rerank_fn = _rerank

    runtime.stage2_targeted_retrieval(
        [PatentRetrievalClaim(claim="battery thermal", keywords=[])],
        user_question="battery thermal",
    )

    assert captured["rerank_fn"] is _rerank


def test_build_default_runtime_wires_stage2_rerank_fn_from_env(monkeypatch, tmp_path):
    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    class _ArchiveLoader:
        def build_identity_registry(self):
            return {}

        def build_catalog_records(self):
            return []

        def load_tables(self, patent_id):
            return []

    class _AnswerBuilder:
        def close(self):
            return None

    monkeypatch.setenv("RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RERANK_API_KEY", "rerank-key")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "gte-rerank-v2")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=tmp_path / "missing_abstract",
            chunk_db_path=tmp_path / "missing_chunk",
            archive_root=archive_root,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentArchiveLoader", lambda root: _ArchiveLoader())
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda: _AnswerBuilder())

    runtime = build_default_patent_runtime()

    assert callable(runtime.stage2_rerank_fn)


def test_build_default_runtime_strict_does_not_wire_archive_loader_for_retrieval_hydration(monkeypatch, tmp_path):
    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    class _ArchiveLoader:
        def build_identity_registry(self):
            return {}

        def build_catalog_records(self):
            return []

        def load_tables(self, patent_id):
            raise AssertionError(f"archive table loader should not be used for {patent_id}")

        def load_claims(self, patent_id):
            raise AssertionError(f"archive claims loader should not be used for {patent_id}")

        def load_description_snippets(self, patent_id):
            raise AssertionError(f"archive description loader should not be used for {patent_id}")

    class _AnswerBuilder:
        def close(self):
            return None

    monkeypatch.delenv("PATENT_ORIGINAL_MINIO_ONLY", raising=False)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=tmp_path / "missing_abstract",
            chunk_db_path=tmp_path / "missing_chunk",
            archive_root=archive_root,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentArchiveLoader", lambda root: _ArchiveLoader())
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda: _AnswerBuilder())

    runtime = build_default_patent_runtime()

    assert runtime.retrieval_service._archive_loader is None


def test_run_stage2_targeted_retrieval_passes_rerank_fn_to_service():
    class _Service:
        def targeted_retrieve(self, **kwargs):
            self.kwargs = kwargs
            return {"documents": [], "metadatas": [], "distances": [], "references": [], "source_ids": [], "metadata": {}}

    service = _Service()

    def _rerank(**kwargs):
        return {"documents": [], "metadatas": [], "rerank_scores": []}

    run_stage2_targeted_retrieval(
        retrieval_service=service,
        retrieval_claims=[PatentRetrievalClaim(claim="battery thermal", keywords=[])],
        user_question="battery thermal",
        rerank_fn=_rerank,
    )

    assert service.kwargs["rerank_fn"] is _rerank


def test_stage2_convergence_targeted_no_vector_fallback_keeps_stage3_payload(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_VALIDATION_ENABLED", "true")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        identity_registry={"CN123456789A": "CN123456789A"},
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[
            {
                "claim": "Summarize CN123456789A thermal management",
                "keywords": ["CN123456789A"],
                "preferred_sections": ["claims"],
            }
        ],
        user_question="Summarize CN123456789A",
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["references"] == ["CN123456789A"]
    assert isinstance(payload["documents"], list)
    assert isinstance(payload["metadatas"], list)
    assert isinstance(payload["distances"], list)
    assert isinstance(payload["reference_objects"], list)
    assert isinstance(payload["reference_links"], list)
    assert isinstance(payload["original_links"], list)
    assert payload["metadata"]["stage2_validation"]["validation_fallback"] in {False, True}


def test_stage2_convergence_contracts_payload_to_selected_patents(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "1")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "thermal electrode", "keywords": []}],
        user_question="thermal electrode",
        frozen_claim_queries=[["thermal electrode"]],
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["references"] == ["CN123456789A"]
    assert [item["canonical_patent_id"] for item in payload["reference_objects"]] == ["CN123456789A"]
    assert [item["patent_id"] for item in payload["metadatas"]] == ["CN123456789A"]
    assert payload["metadata"]["stage2_raw_candidate_count"] >= 2


def test_stage2_convergence_logs_controls_and_summary(monkeypatch, caplog):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "1")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
        ],
    )

    with caplog.at_level(logging.INFO, logger="patent.retrieval"):
        service.targeted_retrieve(
            retrieval_claims=[{"claim": "thermal electrode", "keywords": []}],
            user_question="thermal electrode",
            frozen_claim_queries=[["thermal electrode"]],
        )

    messages = [record.message for record in caplog.records if record.name == "patent.retrieval"]
    assert any("patent stage2 convergence controls" in message for message in messages)
    assert any("patent stage2 retrieval summary" in message for message in messages)


def test_stage2_b_keeps_graph_candidate_hard_filter_when_convergence_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "false")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "thermal", "keywords": []}],
        user_question="thermal",
        frozen_claim_queries=[["thermal"]],
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["graph_stage2_behavior"] == "filter_applied"


def test_stage2_convergence_disabled_preserves_existing_wide_output(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "1")
    monkeypatch.setenv("PATENT_STAGE2_VALIDATION_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {
                "patent_id": patent_id,
                "document": f"{patent_id} chunk",
                "source_file": "说明书.txt",
                "chunk_index": index,
                "distance": 0.1 + index,
            }
            for index, patent_id in enumerate(list(patent_ids or []))
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "thermal electrode", "keywords": []}],
        user_question="thermal electrode",
        frozen_claim_queries=[["thermal electrode"]],
    )

    assert payload["source_ids"] == ["CN123456789A", "US20240001234A1"]
    assert payload["references"] == ["CN123456789A", "US20240001234A1"]
    assert "stage2_validation" not in payload.get("metadata", {})
    assert "stage2_rerank" not in payload.get("metadata", {})


def test_stage2_convergence_disabled_keeps_b_graph_filter_even_if_c_toggles_are_set(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED", "true")

    chunk_calls: list[list[str] | None] = []
    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: (
            chunk_calls.append(list(patent_ids) if patent_ids is not None else None)
            or [
                {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
                {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
            ]
        ),
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "thermal electrode", "keywords": []}],
        user_question="thermal electrode",
        frozen_claim_queries=[["thermal electrode"]],
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert None not in chunk_calls
    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["graph_stage2_behavior"] == "filter_applied"


def test_stage2_c_global_chunk_recall_finds_better_evidence_outside_abstract_candidates(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED", "true")

    chunk_calls: list[list[str] | None] = []

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "generic thermal abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: (
            chunk_calls.append(list(patent_ids) if patent_ids is not None else None)
            or (
                [
                    {"patent_id": "CN123456789A", "document": "generic thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2},
                ]
                if patent_ids
                else [
                    {"patent_id": "US20240001234A1", "document": "Anode porosity control at high C-rate", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.05},
                ]
            )
        ),
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "anode porosity high C-rate", "keywords": []}],
        user_question="anode porosity high C-rate",
        frozen_claim_queries=[["anode porosity high C-rate"]],
    )

    assert None in chunk_calls
    assert payload["source_ids"][0] == "US20240001234A1"


def test_stage2_c_graph_candidates_do_not_hard_filter_strong_vector_candidates(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED", "true")

    chunk_calls: list[list[str] | None] = []

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "generic graph-seeded abstract", "distance": 0.4},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: (
            chunk_calls.append(list(patent_ids) if patent_ids is not None else None)
            or (
                [
                    {"patent_id": "CN123456789A", "document": "generic graph chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.4},
                ]
                if patent_ids
                else [
                    {"patent_id": "US20240001234A1", "document": "LiFePO4 放电容量 156 mAh/g 实施例", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.02},
                ]
            )
        ),
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "LFP 放电容量超过 150 mAh/g", "keywords": ["LFP"]}],
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        frozen_claim_queries=[["LFP discharge capacity 150 mAh/g"]],
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert None in chunk_calls
    assert set(payload["metadata"]["stage2_raw_candidate_patent_ids"]) >= {"CN123456789A", "US20240001234A1"}
    assert payload["source_ids"][0] == "US20240001234A1"
    assert any(
        "graph_candidate_boost" in item["reasons"]
        for item in payload["metadata"]["stage2_patent_scores"]
        if item["patent_id"] == "CN123456789A"
    )


def test_stage2_c_table_boost_loads_tables_only_for_candidate_pool(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_TABLE_METRIC_BOOST_ENABLED", "true")

    loaded_tables: list[str] = []

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "LFP capacity abstract", "distance": 0.2},
            {"patent_id": "US20240001234A1", "document": "generic electrode abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": patent_id, "document": f"{patent_id} chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2}
            for patent_id in list(patent_ids or [])
        ],
        table_loader=lambda patent_id: (
            loaded_tables.append(patent_id)
            or (
                [{"table_title": "表1 放电容量", "rows": [{"材料": "LFP", "放电容量": "156 mAh/g"}]}]
                if patent_id == "CN123456789A"
                else []
            )
        ),
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "LFP 放电容量超过 150 mAh/g", "keywords": ["LFP"]}],
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        frozen_claim_queries=[["LFP discharge capacity 150 mAh/g"]],
    )

    assert set(loaded_tables) <= set(payload["metadata"]["stage2_raw_candidate_patent_ids"])
    assert payload["source_ids"][0] == "CN123456789A"
    assert "table_metric_match" in payload["metadata"]["stage2_patent_scores"][0]["reasons"]


def test_stage2_c_explicit_id_hard_constraint_uses_exact_fallback_when_vectors_miss(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        identity_registry={"CN123456789A": "CN123456789A"},
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "US20240001234A1", "document": "unrelated electrode abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "US20240001234A1", "document": "unrelated electrode chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "请总结 CN123456789A", "keywords": []}],
        user_question="请总结 CN123456789A",
        frozen_claim_queries=[["CN123456789A"]],
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["references"] == ["CN123456789A"]
    assert payload["metadata"]["stage2_explicit_id_fallback"] is True


def test_stage2_b_explicit_id_hard_constraint_uses_exact_fallback_when_vectors_miss(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "false")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        identity_registry={"CN123456789A": "CN123456789A"},
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "US20240001234A1", "document": "unrelated electrode abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "US20240001234A1", "document": "unrelated electrode chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "请总结 CN123456789A", "keywords": []}],
        user_question="请总结 CN123456789A",
        frozen_claim_queries=[["CN123456789A"]],
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["references"] == ["CN123456789A"]
    assert payload["metadata"]["stage2_explicit_id_fallback"] is True


def test_stage2_b_explicit_id_fallback_survives_validation_with_zero_min_results(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "false")
    monkeypatch.setenv("PATENT_STAGE2_MIN_RESULTS_PER_CLAIM", "0")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        identity_registry={"CN123456789A": "CN123456789A"},
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "US20240001234A1", "document": "unrelated electrode abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "US20240001234A1", "document": "unrelated electrode chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "请总结 CN123456789A", "keywords": []}],
        user_question="请总结 CN123456789A",
        frozen_claim_queries=[["CN123456789A"]],
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadatas"][0]["exact_id_match"] is True


def test_stage2_c_respects_max_global_patents_instead_of_forcing_one(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "2")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "LFP capacity abstract", "distance": 0.2},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "LiFePO4 放电容量 156 mAh/g", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2},
            {"patent_id": "US20240001234A1", "document": "electrode process", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.1},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "LFP 放电容量超过 150 mAh/g", "keywords": ["LFP"]}],
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        frozen_claim_queries=[["LFP discharge capacity 150 mAh/g"]],
    )

    assert payload["source_ids"] == ["CN123456789A", "US20240001234A1"]
