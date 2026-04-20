from __future__ import annotations

from server.patent.answering import PatentAnswerBuilder, build_fallback_patent_answer
from server.patent.retrieval_models import PatentEvidence, PatentRetrievalOutcome


def _retrieval_outcome() -> PatentRetrievalOutcome:
    return PatentRetrievalOutcome(
        retrieval_backend="vector_hybrid",
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        references=["CN115132975B"],
        reference_objects=[{"canonical_patent_id": "CN115132975B", "publication_number": "CN115132975B"}],
        reference_links=[],
        original_links=[],
        evidences=[
            PatentEvidence(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number=None,
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                matched_section_type="claim",
                matched_section_label="Claim 1",
                matched_snippet="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                provider="patent_archive",
                original_available=True,
            )
        ],
    )


def test_answer_builder_prompt_includes_graph_context_without_promoting_candidates_to_citations():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="总结该方案的核心改进点",
        retrieval_outcome=_retrieval_outcome(),
        context={
            "allowed_patent_ids": ["CN115132975B"],
            "graph_kb": {
                "mode": "graph_for_rag",
                "stage4_fact_block": "- graph fact",
                "stage4_graph_candidate_patent_ids": ["CN999999999A"],
            },
        },
    )

    assert "图谱结构化辅助线索" in prompt
    assert "CN999999999A" in prompt
    assert "graph fact" in prompt
    assert "只有白名单允许引用" in prompt


def test_fallback_answer_sees_graph_context_but_does_not_fabricate_graph_only_citations():
    answer = build_fallback_patent_answer(
        question="总结该方案的核心改进点",
        retrieval_outcome=_retrieval_outcome(),
        context={
            "allowed_patent_ids": ["CN115132975B"],
            "graph_kb": {
                "mode": "graph_for_rag",
                "stage4_fact_block": "- graph fact",
                "stage4_graph_candidate_patent_ids": ["CN999999999A"],
            },
        },
    )

    assert "图谱辅助线索" in answer
    assert "CN999999999A" in answer
    assert "(patent_id=CN999999999A)" not in answer
    assert "(patent_id=CN115132975B)" in answer
