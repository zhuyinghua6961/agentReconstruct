from __future__ import annotations

from app.modules.generation_pipeline.evidence_rerank import rerank_evidence_chunks


class _EmbeddingModel:
    def encode(self, texts):
        vectors = []
        for text in texts:
            lower = str(text).lower()
            vectors.append(
                [
                    1.0 if "fepo4" in lower or "磷酸铁" in lower else 0.0,
                    1.0 if "fec2o4" in lower or "草酸亚铁" in lower else 0.0,
                    1.0 if "advantage" in lower or "优势" in lower else 0.0,
                    1.0 if "wastewater" in lower or "废水" in lower else 0.0,
                ]
            )
        return vectors


def test_rerank_evidence_chunks_orders_by_question_similarity():
    result = rerank_evidence_chunks(
        pdf_chunks={
            "10.1/a": [
                {"text": "wastewater treatment and separation process", "chunk_id": "noise"},
                {"text": "FePO4 precursor advantage for LiFePO4 synthesis", "chunk_id": "hit"},
            ]
        },
        user_question="磷酸铁 FePO4 原料优势",
        retrieval_results={},
        embedding_model=_EmbeddingModel(),
    )

    chunks = result["pdf_chunks"]["10.1/a"]
    assert [chunk["chunk_id"] for chunk in chunks] == ["hit", "noise"]
    assert chunks[0]["evidence_score"] > chunks[1].get("evidence_score", 0.0)
    assert result["stats"]["before_chunk_count"] == 2
    assert result["stats"]["after_chunk_count"] == 2


def test_rerank_evidence_chunks_preserves_each_comparison_object():
    result = rerank_evidence_chunks(
        pdf_chunks={
            "10.1/fepo4": [{"text": "FePO4 route advantage direct precursor", "chunk_id": "fepo4"}],
            "10.1/fec2o4": [{"text": "FeC2O4 route advantage carbon coating", "chunk_id": "fec2o4"}],
            "10.1/noise": [{"text": "wastewater separation unrelated", "chunk_id": "noise"}],
        },
        user_question="磷酸铁和草酸亚铁作为原料各有什么优劣势？",
        retrieval_results={
            "comparison_groups": [
                {"label": "磷酸铁", "aliases": ["FePO4"], "doi_candidates": ["10.1/fepo4"]},
                {"label": "草酸亚铁", "aliases": ["FeC2O4"], "doi_candidates": ["10.1/fec2o4"]},
            ]
        },
        embedding_model=_EmbeddingModel(),
        topk_total=2,
        topk_per_comparison_object=1,
    )

    kept_ids = {chunk["chunk_id"] for chunks in result["pdf_chunks"].values() for chunk in chunks}
    assert kept_ids == {"fepo4", "fec2o4"}
    assert result["stats"]["comparison_object_count"] == 2
