from __future__ import annotations

from server.patent.models import PatentQaExecutionMetadata, PatentRetrievalPlan


def test_patent_staged_models_expose_minimal_runtime_contract():
    plan = PatentRetrievalPlan(
        question_type="comparison",
        analysis_axes=["risk", "timing"],
        explicit_patent_ids=["CN115132975B"],
        candidate_recall_queries=["LMFP LFP safety"],
        evidence_localization_queries=["high soc charging safety"],
        preferred_sections=["claim", "description", "table"],
        filters={"country": "CN"},
    )
    metadata = PatentQaExecutionMetadata(
        route="kb_qa",
        query_mode="patent staged qa",
        source_ids=["CN115132975B"],
        stage_timings_ms={"stage1": 12.5},
    )

    assert plan.explicit_patent_ids == ["CN115132975B"]
    assert plan.preferred_sections == ["claim", "description", "table"]
    assert metadata.route == "kb_qa"
    assert metadata.source_ids == ["CN115132975B"]
    assert metadata.stage_timings_ms["stage1"] == 12.5
