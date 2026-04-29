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
    assert "$terms" in plan.parametric_slots["candidate_queries"][0]["cypher"]


def test_planner_v2_builds_constrained_v1_template_queries_for_graph_slots():
    decision = classify_graph_question_v2(question="请总结 LFP 的制备方法和测试表征", conversation_context={})

    plan = build_graph_query_plan_v2(
        question="请总结 LFP 的制备方法和测试表征",
        decision=decision,
        schema_registry=build_default_schema_registry(),
    )

    assert plan is not None
    assert plan.strategy == "v1_template"
    assert plan.parametric_slots["candidate_queries"]
    assert "$terms" in plan.parametric_slots["candidate_queries"][0]["cypher"]


def test_planner_v2_skips_plan_when_classifier_skips_graph():
    decision = classify_graph_question_v2(question="请总结锂电行业趋势", conversation_context={})

    plan = build_graph_query_plan_v2(
        question="请总结锂电行业趋势",
        decision=decision,
        schema_registry=build_default_schema_registry(),
    )

    assert plan is None


def test_planner_precise_carbon_source_uses_carbon_source_path():
    question = "列出使用蔗糖作为碳源的文献"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is not None
    assert plan.intent == "list_by_carbon_source"
    assert any(path["path_id"] == "recipe.carbon_source" for path in plan.parametric_slots["candidate_queries"])


def test_planner_precise_count_uses_count_intent():
    question = "统计使用 sucrose 作为碳源的文献数量"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is not None
    assert plan.intent == "count_by_structured_field"
    assert any(path["path_id"].endswith(".count") for path in plan.parametric_slots["candidate_queries"])


def test_planner_legacy_material_count_keeps_graph_count_path():
    question = "LFP有多少篇文献？"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is not None
    assert plan.intent == "count_by_structured_field"
    assert any(path["path_id"] == "raw_material.name.count" for path in plan.parametric_slots["candidate_queries"])


def test_planner_distinguishes_doi_lookup_and_expansion():
    lookup_q = "10.1021/jp1005692 这篇文献是什么？"
    expand_q = "展开 10.1021/jp1005692 的测试、工艺和原料信息"
    lookup_decision = classify_graph_question_v2(question=lookup_q, conversation_context={})
    expand_decision = classify_graph_question_v2(question=expand_q, conversation_context={})

    lookup_plan = build_graph_query_plan_v2(question=lookup_q, decision=lookup_decision, schema_registry=build_default_schema_registry())
    expand_plan = build_graph_query_plan_v2(question=expand_q, decision=expand_decision, schema_registry=build_default_schema_registry())

    assert lookup_plan is not None
    assert expand_plan is not None
    assert lookup_plan.intent == "lookup_by_doi"
    assert expand_plan.intent == "expand_doi_context"


def test_planner_community_has_community_paths():
    question = "LiFePO4的关系网络和机制关联是什么？"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is not None
    assert plan.intent.startswith("community")
    assert plan.strategy == "community"
    assert any("community" in path["path_id"] for path in plan.parametric_slots["candidate_queries"])


def test_planner_hybrid_property_analysis_uses_multi_stage_strategy():
    question = "放电容量超过150 mAh/g的LFP有哪些特点？"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is not None
    assert plan.strategy == "multi_stage"
    assert plan.intent == "hybrid_property_analysis"
    assert any(path["path_id"].startswith("hybrid.") for path in plan.parametric_slots["candidate_queries"])


def test_planner_hybrid_property_analysis_preserves_ranking_and_limit_slots():
    question = "请分析压实密度最高的前10个LiFePO4样品有哪些特点？"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is not None
    assert plan.strategy == "multi_stage"
    slots = plan.parametric_slots["slots"]
    assert slots["ranking"] == "top"
    assert slots["limit"] == 10
    assert slots["unit"] == ""
    assert plan.parametric_slots["candidate_queries"][0]["params"]["limit"] == 50


def test_planner_process_method_preserves_material_terms_for_target_filtering():
    question = "LiFePO4 的制备方法有哪些？"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is not None
    assert plan.intent == "list_by_process_method"
    slots = plan.parametric_slots["slots"]
    assert "lifepo4" in slots["material_terms"]
    assert plan.parametric_slots["candidate_queries"][0]["params"]["target_terms"]


def test_planner_deferred_numeric_field_does_not_build_unverified_template():
    question = "能量密度最高的 LFP 有哪些？"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is None


def test_semantic_no_graph_slots_returns_none():
    question = "为什么电池安全性很重要？"
    decision = classify_graph_question_v2(question=question, conversation_context={})

    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())

    assert plan is None
