from __future__ import annotations

from server.patent.models import PatentRetrievalClaim
from server.patent.retrieval_guardrails import apply_patent_stage2_query_guardrails
from server.patent.stage2_controls import PatentStage2RuntimeToggles


def _toggles(**overrides):
    defaults = dict(
        convergence_enabled=True,
        force_keyword_injection_enabled=True,
        entity_lock_enabled=True,
        rerank_enabled=False,
        rerank_candidates=20,
        rerank_top_patents=10,
        min_results_per_claim=1,
        max_results_per_claim=3,
        max_global_patents=10,
        validation_enabled=True,
        c_patent_scoring_enabled=False,
        c_global_chunk_recall_enabled=False,
        c_table_metric_boost_enabled=False,
        rerank_provider="none",
        rerank_model="",
        rerank_base_url="",
        rerank_timeout_seconds=20.0,
        rerank_endpoint_family="",
    )
    defaults.update(overrides)
    return PatentStage2RuntimeToggles(**defaults)


def test_guardrail_preserves_lfp_capacity_threshold():
    guarded = apply_patent_stage2_query_guardrails(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claim=PatentRetrievalClaim(claim="碳包覆改性实现高容量", keywords=["LiFePO4"]),
        queries=["carbon coated cathode material high capacity"],
        toggles=_toggles(),
        graph_context=None,
    )

    assert guarded.queries
    final_query = guarded.queries[0]
    assert "LFP" in final_query or "LiFePO4" in final_query
    assert "150" in final_query
    assert "mAh/g" in final_query
    assert guarded.diagnostics["injected_thresholds"]


def test_guardrail_preserves_explicit_id_material_metric_name_and_density_unit():
    guarded = apply_patent_stage2_query_guardrails(
        user_question="请对比 CN123456789A 中 LFP 的压实密度超过 2.4 g/cm3 的实施例",
        retrieval_claim=PatentRetrievalClaim(claim="找高压实密度正极材料", keywords=[]),
        queries=["cathode embodiment"],
        toggles=_toggles(),
        graph_context=None,
    )

    final_query = guarded.queries[0]
    assert "CN123456789A" in final_query
    assert "LFP" in final_query
    assert "压实密度" in final_query
    assert "2.4 g/cm3" in final_query
    assert guarded.diagnostics["injected_entities"]
    assert guarded.diagnostics["injected_metrics"]
    assert guarded.diagnostics["query_rewrites"][0]["original_query"] == "cathode embodiment"
    assert guarded.diagnostics["query_rewrites"][0]["final_query"] == final_query


def test_guardrail_is_noop_when_convergence_disabled():
    guarded = apply_patent_stage2_query_guardrails(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claim=PatentRetrievalClaim(claim="x", keywords=[]),
        queries=["plain query"],
        toggles=_toggles(convergence_enabled=False),
        graph_context=None,
    )

    assert guarded.queries == ["plain query"]
    assert guarded.diagnostics["enabled"] is False
