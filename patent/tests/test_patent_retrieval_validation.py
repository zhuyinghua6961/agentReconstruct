from __future__ import annotations

from server.patent.retrieval_validation import validate_patent_stage2_candidates


def test_validation_keeps_metric_candidate_and_filters_generic_candidate():
    candidates = [
        {
            "document": "LiFePO4 LFP 放电容量 156 mAh/g，实施例1。",
            "metadata": {"patent_id": "CN1", "section_type": "description"},
            "score": 0.8,
        },
        {
            "document": "A generic cathode material has good performance.",
            "metadata": {"patent_id": "CN2", "section_type": "abstract"},
            "score": 0.9,
        },
    ]

    result = validate_patent_stage2_candidates(
        candidates=candidates,
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        claim_text="LFP 放电容量超过 150 mAh/g",
        min_results=1,
    )

    assert [item["metadata"]["patent_id"] for item in result.selected] == ["CN1"]
    assert result.diagnostics["filtered_count"] == 1


def test_validation_keeps_no_vector_candidate_when_needed():
    candidates = [
        {
            "document": "Claim 1: exact archive fallback evidence.",
            "metadata": {"patent_id": "CN123456789A", "section_type": "claim", "exact_id_match": True},
            "score": None,
        }
    ]

    result = validate_patent_stage2_candidates(
        candidates=candidates,
        user_question="CN123456789A",
        claim_text="CN123456789A",
        min_results=1,
    )

    assert result.selected
    assert result.diagnostics["validation_fallback"] is False


def test_validation_keeps_density_metric_candidate():
    result = validate_patent_stage2_candidates(
        candidates=[
            {
                "document": "LFP 正极材料压实密度 2.45 g/cm3。",
                "metadata": {"patent_id": "CN1", "section_type": "description"},
                "score": 0.5,
            },
            {
                "document": "A generic cathode material.",
                "metadata": {"patent_id": "CN2", "section_type": "abstract"},
                "score": 0.9,
            },
        ],
        user_question="找 LFP 压实密度超过 2.4 g/cm3 的专利",
        claim_text="LFP 压实密度超过 2.4 g/cm3",
        min_results=1,
    )

    assert [item["metadata"]["patent_id"] for item in result.selected] == ["CN1"]
