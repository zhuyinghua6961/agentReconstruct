from __future__ import annotations

from server.patent.graph_kb.client import build_patent_parametric_query_candidates
from server.patent.graph_kb.models import PatentGraphSemanticDecision
from server.patent.graph_kb.planner_v2 import build_patent_graph_query_plan_v2
from server.patent.graph_kb.schema_registry import build_default_patent_schema_registry


def test_planner_v2_returns_template_plan_for_legacy_question():
    registry = build_default_patent_schema_registry()
    decision = PatentGraphSemanticDecision(
        mode="direct_answer",
        route_family="precise",
        diagnostics={"matched_rule": "legacy_template"},
    )

    plan = build_patent_graph_query_plan_v2(
        question="CN100355122C 的工艺步骤是什么？",
        decision=decision,
        schema_registry=registry,
    )

    assert plan is not None
    assert plan.strategy == "template"
    assert plan.intent == "list_patent_process_steps"
    assert plan.legacy_template_id == "list_patent_process_steps"
    assert plan.legacy_template_plan is not None
    assert plan.legacy_template_plan.template_id == "list_patent_process_steps"
    assert plan.diagnostics["matched_rule"] == "legacy_template"
    assert plan.diagnostics["strategy"] == "template"


def test_planner_v2_returns_parametric_plan_for_inventor_listing():
    registry = build_default_patent_schema_registry()
    decision = PatentGraphSemanticDecision(
        mode="direct_answer",
        route_family="precise",
        diagnostics={"matched_rule": "inventor_listing"},
    )

    plan = build_patent_graph_query_plan_v2(
        question="发明人张三有哪些专利？",
        decision=decision,
        schema_registry=registry,
    )

    assert plan is not None
    assert plan.strategy == "parametric"
    assert plan.intent == "inventor_listing"
    assert plan.parametric_slots["question"] == "发明人张三有哪些专利？"
    assert "Patent" in plan.parametric_slots["allowed_labels"]
    assert "HAS_INVENTOR" in plan.parametric_slots["allowed_relations"]
    assert [item["path_id"] for item in plan.parametric_slots["candidate_queries"]] == ["list_patents_by_inventor"]
    assert plan.diagnostics["strategy"] == "parametric"
    assert plan.diagnostics["candidate_path_ids"] == ("list_patents_by_inventor",)


def test_planner_v2_returns_none_for_skip_graph_decision():
    registry = build_default_patent_schema_registry()
    decision = PatentGraphSemanticDecision(mode="skip_graph", route_family="semantic")

    assert (
        build_patent_graph_query_plan_v2(
            question="为什么这种技术路线更有前景？",
            decision=decision,
            schema_registry=registry,
        )
        is None
    )


def test_parametric_query_builders_cover_phase2_families():
    candidates = build_patent_parametric_query_candidates("发明人张三有哪些专利？")
    assert [item["path_id"] for item in candidates] == ["list_patents_by_inventor"]
    assert candidates[0]["params"] == {"inventor_name": "张三"}
    assert " AS inventor_name" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("代理机构北京理工专利事务所有哪些专利？")
    assert [item["path_id"] for item in candidates] == ["list_patents_by_agency"]
    assert candidates[0]["params"] == {"agency_name": "北京理工专利事务所"}
    assert " AS agency_name" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("H01M10 下有哪些专利？")
    assert [item["path_id"] for item in candidates] == ["list_patents_by_ipc_subclass"]
    assert candidates[0]["params"] == {"ipc_subclass": "H01M10"}
    assert " AS ipc_subclass" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("CN100355122C 使用了哪些气氛条件？")
    assert [item["path_id"] for item in candidates] == ["list_patent_atmospheres"]
    assert candidates[0]["params"] == {"patent_id": "CN100355122C"}
    assert " AS atmosphere_options" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("CN100355122C 有哪些实施例洞察？")
    assert [item["path_id"] for item in candidates] == ["list_patent_embodiment_insights"]
    assert candidates[0]["params"] == {"patent_id": "CN100355122C"}
    assert " AS insight_conclusion" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("H01M10/0525 有多少专利？")
    assert [item["path_id"] for item in candidates] == ["count_patents_by_ipc"]
    assert candidates[0]["params"] == {"ipc_code": "H01M10/0525"}
    assert " AS patent_count" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("宁德时代新能源科技股份有限公司有多少专利？")
    assert [item["path_id"] for item in candidates] == ["count_patents_by_applicant"]
    assert candidates[0]["params"] == {"organization_name": "宁德时代新能源科技股份有限公司"}
    assert " AS applicant_name" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("发明人张三有多少专利？")
    assert [item["path_id"] for item in candidates] == ["count_patents_by_inventor"]
    assert candidates[0]["params"] == {"inventor_name": "张三"}
    assert " AS inventor_name" in candidates[0]["cypher"]


def test_parametric_query_builders_cover_compare_safe_families():
    candidates = build_patent_parametric_query_candidates("比较 CN100355122C 和 CN100371239C 的工艺步骤差异")
    assert [item["path_id"] for item in candidates] == ["compare_patents_process_steps"]
    assert candidates[0]["params"] == {"patent_ids": ["CN100355122C", "CN100371239C"]}
    assert " AS step_order" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("比较 CN100355122C 和 CN100371239C 的材料角色差异")
    assert [item["path_id"] for item in candidates] == ["compare_patents_material_roles"]
    assert candidates[0]["params"] == {"patent_ids": ["CN100355122C", "CN100371239C"]}
    assert " AS material_name" in candidates[0]["cypher"]

    candidates = build_patent_parametric_query_candidates("比较 CN100355122C 和 CN100371239C 的技术问题和方案差异")
    assert [item["path_id"] for item in candidates] == ["compare_patents_problem_solution"]
    assert candidates[0]["params"] == {"patent_ids": ["CN100355122C", "CN100371239C"]}
    assert " AS solution_texts" in candidates[0]["cypher"]
