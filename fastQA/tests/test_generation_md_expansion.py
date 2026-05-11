from __future__ import annotations

import logging

from app.modules.generation_pipeline.md_expansion import run_stage25_md_expansion


class _EmbeddingModel:
    def encode(self, texts):
        assert texts == ["lfp voltage"]
        return [[0.1, 0.2, 0.3]]


class _Expert:
    embedding_model = _EmbeddingModel()


class _Collection:
    def query(self, **kwargs):
        where = kwargs.get("where")
        if where == {"doi": "10.1/a"}:
            return {
                "documents": [["md chunk a"]],
                "metadatas": [[{"doi": "10.1/a", "page": 1, "chunk_id": "c1"}]],
                "distances": [[0.1]],
            }
        return {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }


def test_run_stage25_md_expansion_returns_md_chunks(monkeypatch):
    monkeypatch.setenv("QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED", "0")

    result = run_stage25_md_expansion(
        retrieval_results={"documents": []},
        user_question="lfp voltage",
        dois=["10.1/a"],
        literature_expert=_Expert(),
        logger=logging.getLogger("test.md"),
        collection_override=_Collection(),
    )

    assert result["enabled"] is True
    assert result["applied"] is True
    assert list(result["md_chunks_by_doi"]) == ["10.1/a"]
    assert result["stats"]["hit_doi_count"] == 1
    assert result["stats"]["total_md_chunks"] == 1


class _RecordingEmbeddingModel:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def encode(self, texts):
        self.texts.extend(texts)
        if "FeC2O4" in texts[0]:
            return [[0.1, 0.2, 0.3]]
        if "Fe2O3" in texts[0]:
            return [[0.4, 0.5, 0.6]]
        return [[0.0, 0.0, 0.0]]


class _RecordingExpert:
    def __init__(self) -> None:
        self.embedding_model = _RecordingEmbeddingModel()


class _ComparisonCollection:
    def query(self, **kwargs):
        where = kwargs.get("where")
        embedding = kwargs["query_embeddings"][0]
        if where == {"doi": "10.1/fe-c2o4"} and embedding == [0.1, 0.2, 0.3]:
            return {
                "documents": [["ferrous oxalate md chunk"]],
                "metadatas": [[{"doi": "10.1/fe-c2o4", "chunk_id": "c1"}]],
                "distances": [[0.1]],
            }
        if where == {"doi": "10.2/fe2o3"} and embedding == [0.4, 0.5, 0.6]:
            return {
                "documents": [["hematite md chunk"]],
                "metadatas": [[{"doi": "10.2/fe2o3", "chunk_id": "c2"}]],
                "distances": [[0.2]],
            }
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


def test_run_stage25_md_expansion_uses_comparison_group_queries(monkeypatch):
    monkeypatch.setenv("QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED", "0")
    expert = _RecordingExpert()
    retrieval_results = {
        "comparison_groups": [
            {
                "label": "草酸亚铁",
                "queries": ["LFP FeC2O4 ferrous oxalate advantages"],
                "doi_candidates": ["10.1/fe-c2o4"],
            },
            {
                "label": "铁红",
                "queries": ["LFP Fe2O3 hematite red iron oxide advantages"],
                "doi_candidates": ["10.2/fe2o3"],
            },
        ]
    }

    result = run_stage25_md_expansion(
        retrieval_results=retrieval_results,
        user_question="generic comparison question",
        dois=["10.1/fe-c2o4", "10.2/fe2o3"],
        literature_expert=expert,
        logger=logging.getLogger("test.md"),
        collection_override=_ComparisonCollection(),
    )

    assert expert.embedding_model.texts == [
        "LFP FeC2O4 ferrous oxalate advantages",
        "LFP Fe2O3 hematite red iron oxide advantages",
    ]
    assert result["comparison_groups"][0]["md_hits"][0]["text"] == "ferrous oxalate md chunk"
    assert result["comparison_groups"][1]["md_hits"][0]["text"] == "hematite md chunk"
    assert result["stats"]["hit_doi_count"] == 2


class _NoiseFilteringCollection:
    def query(self, **kwargs):
        where = kwargs.get("where")
        if where == {"doi": "10.1/fe-po4"}:
            return {
                "documents": [[
                    "FePO4 is an iron source precursor for LiFePO4 synthesis with good phase purity.",
                    "Spent battery recycling recovers FePO4 from wastewater separation streams.",
                ]],
                "metadatas": [[
                    {"doi": "10.1/fe-po4", "chunk_id": "good"},
                    {"doi": "10.1/fe-po4", "chunk_id": "noise"},
                ]],
                "distances": [[0.1, 0.2]],
            }
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


def test_run_stage25_md_expansion_filters_comparison_noise_chunks(monkeypatch):
    monkeypatch.setenv("QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED", "0")
    expert = _RecordingExpert()
    retrieval_results = {
        "comparison_groups": [
            {
                "label": "磷酸铁",
                "queries": ["FePO4 as iron source precursor for LiFePO4 synthesis"],
                "doi_candidates": ["10.1/fe-po4"],
                "must_include_any": ["FePO4", "iron phosphate", "磷酸铁"],
                "positive_context_terms": ["LiFePO4 synthesis", "iron source", "precursor"],
                "negative_context_terms": ["recycling", "spent battery", "wastewater"],
            }
        ]
    }

    result = run_stage25_md_expansion(
        retrieval_results=retrieval_results,
        user_question="generic comparison question",
        dois=["10.1/fe-po4"],
        literature_expert=expert,
        logger=logging.getLogger("test.md"),
        collection_override=_NoiseFilteringCollection(),
    )

    assert result["md_chunks_by_doi"]["10.1/fe-po4"] == [
        {
            "doi": "10.1/fe-po4",
            "text": "FePO4 is an iron source precursor for LiFePO4 synthesis with good phase purity.",
            "page": 0,
            "chunk_id": "good",
            "distance": 0.1,
            "source": "md_expansion",
        }
    ]
    assert result["comparison_groups"][0]["md_hits"][0]["chunk_id"] == "good"
    assert result["stats"]["total_md_chunks"] == 1
