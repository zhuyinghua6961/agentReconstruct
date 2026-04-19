from __future__ import annotations

from app.modules.graph_kb.classifier_v2 import classify_graph_question_v2
from app.modules.graph_kb.planner_v2 import build_graph_query_plan_v2
from app.modules.graph_kb.schema_registry import build_default_schema_registry


def test_planner_v2_preserves_legacy_template_for_old_supported_queries():
    decision = classify_graph_question_v2(question="10.1000/test 这篇文献是什么？", conversation_context={})

    plan = build_graph_query_plan_v2(
        question="10.1000/test 这篇文献是什么？",
        decision=decision,
        schema_registry=build_default_schema_registry(),
    )

    assert plan is not None
    assert plan.strategy == "template"
    assert plan.legacy_template_id == "lookup_by_doi"


def test_planner_v2_uses_parametric_strategy_for_precise_numeric_question_without_legacy_template():
    decision = classify_graph_question_v2(question="压实密度最高的LFP材料有哪些？", conversation_context={})

    plan = build_graph_query_plan_v2(
        question="压实密度最高的LFP材料有哪些？",
        decision=decision,
        schema_registry=build_default_schema_registry(),
    )

    assert plan is not None
    assert plan.strategy == "parametric"
    assert plan.legacy_template_id == ""
    assert plan.parametric_slots["candidate_queries"]
    assert "$query_terms" in plan.parametric_slots["candidate_queries"][0]["cypher"]


def test_planner_v2_builds_constrained_candidate_queries_for_llm_cypher_strategy():
    decision = classify_graph_question_v2(question="请总结 LFP 的制备方法和测试表征", conversation_context={})

    plan = build_graph_query_plan_v2(
        question="请总结 LFP 的制备方法和测试表征",
        decision=decision,
        schema_registry=build_default_schema_registry(),
    )

    assert plan is not None
    assert plan.strategy == "llm_cypher"
    assert plan.parametric_slots["candidate_queries"]
    assert "$query_terms" in plan.parametric_slots["candidate_queries"][0]["cypher"]


def test_planner_v2_skips_plan_when_classifier_skips_graph():
    decision = classify_graph_question_v2(question="请总结锂电行业趋势", conversation_context={})

    plan = build_graph_query_plan_v2(
        question="请总结锂电行业趋势",
        decision=decision,
        schema_registry=build_default_schema_registry(),
    )

    assert plan is None
