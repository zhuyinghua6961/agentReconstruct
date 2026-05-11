from __future__ import annotations

from app.modules.qa_kb.comparison_validation import validate_comparison_answer


def test_validate_comparison_answer_appends_missing_object_and_evidence_notes():
    retrieval_results = {
        "comparison_groups": [
            {"label": "草酸亚铁", "evidence_status": "sufficient", "doi_candidates": ["10.1/a"]},
            {
                "label": "铁红",
                "evidence_status": "insufficient",
                "missing_evidence_reason": "abstract_hits_below_threshold",
                "doi_candidates": [],
            },
        ]
    }

    result = validate_comparison_answer("草酸亚铁的优势是容易形成还原气氛。", retrieval_results=retrieval_results)

    assert result["changed"] is True
    assert "铁红" in result["answer"]
    assert "证据不足" in result["answer"]
    assert "abstract_hits_below_threshold" in result["answer"]
    assert result["missing_objects"] == ["铁红"]
