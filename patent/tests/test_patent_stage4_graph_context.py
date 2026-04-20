from __future__ import annotations

from server.patent.retrieval_models import PatentRetrievalOutcome
from server.patent.stages.synthesis import run_stage4_synthesis_with_patent_evidence


def _retrieval_results() -> dict[str, object]:
    return {
        "references": ["CN115132975B"],
        "reference_objects": [
            {
                "canonical_patent_id": "CN115132975B",
                "publication_number": "CN115132975B",
                "title": "一种锂离子电池及动力车辆",
                "provider": "patent_archive",
                "original_available": True,
            }
        ],
        "reference_links": [],
        "original_links": [],
        "metadata": {"retrieval_backend": "vector_hybrid"},
    }


def _evidence_bundle() -> dict[str, object]:
    return {
        "source_ids": ["CN115132975B"],
        "evidences": [
            {
                "canonical_patent_id": "CN115132975B",
                "title": "一种锂离子电池及动力车辆",
                "abstract_text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                "matched_evidence": [
                    {
                        "section_type": "claim",
                        "section_label": "Claim 1",
                        "text": "一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                        "anchor": {"claim_number": 1, "paragraph_id": None},
                        "scores": {"chunk_score": 0.91},
                    }
                ],
                "reference_object": dict(_retrieval_results()["reference_objects"][0]),
                "reference_link": {},
                "original_links": [],
                "scores": {"chunk_score": 0.91},
                "metadata": {"publication_number": "CN115132975B"},
            }
        ],
    }


def test_stage4_synthesis_passes_graph_context_without_widening_citation_whitelist():
    captured: dict[str, object] = {}

    def _answer_builder(*, question, retrieval_outcome: PatentRetrievalOutcome, context):
        captured["context"] = context
        return "基于检索证据可确认该方案聚焦倍率性能优化。(patent_id=CN115132975B)"

    result = run_stage4_synthesis_with_patent_evidence(
        user_question="总结该方案的核心改进点",
        deep_answer="先关注倍率性能和安全性。",
        patent_evidence_bundle=_evidence_bundle(),
        retrieval_results=_retrieval_results(),
        answer_builder=_answer_builder,
        conversation_context={
            "graph_kb": {
                "mode": "graph_for_rag",
                "cache_fingerprint": "patent-graph:test",
                "stage4_fact_block": "- graph fact",
                "stage4_graph_candidate_patent_ids": ["CN999999999A"],
            }
        },
    )

    assert captured["context"]["graph_kb"]["stage4_graph_candidate_patent_ids"] == ["CN999999999A"]
    assert captured["context"]["graph_kb_mode"] == "graph_for_rag"
    assert captured["context"]["graph_kb_fingerprint"] == "patent-graph:test"
    assert captured["context"]["allowed_patent_ids"] == ["CN115132975B"]
    assert result["metadata"]["allowed_patent_ids"] == ["CN115132975B"]
    assert result["metadata"]["graph_kb_mode"] == "graph_for_rag"
    assert result["metadata"]["graph_kb_fingerprint"] == "patent-graph:test"
