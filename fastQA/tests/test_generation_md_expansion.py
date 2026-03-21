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
