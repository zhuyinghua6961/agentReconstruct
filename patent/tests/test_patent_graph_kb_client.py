from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.patent.graph_kb.client import _cypher_and_params, execute_patent_graph_plan, plan_patent_graph_query


def test_plan_patent_graph_query_builds_direct_lookup_plan():
    plan = plan_patent_graph_query("CN100355122C 这件专利是什么？")

    assert plan is not None
    assert plan.template_id == "lookup_patent_by_id"
    assert plan.params == {"patent_id": "CN100355122C"}


def test_plan_patent_graph_query_builds_process_step_plan():
    plan = plan_patent_graph_query("CN100355122C 的工艺步骤是什么？")

    assert plan is not None
    assert plan.template_id == "list_patent_process_steps"
    assert plan.params == {"patent_id": "CN100355122C"}


def test_plan_patent_graph_query_builds_material_role_plan():
    plan = plan_patent_graph_query("CN100355122C 使用了哪些原料？")

    assert plan is not None
    assert plan.template_id == "list_patent_material_roles"


def test_plan_patent_graph_query_builds_experiment_plan():
    plan = plan_patent_graph_query("CN100355122C 有哪些实验表格和性能数据？")

    assert plan is not None
    assert plan.template_id == "list_patent_experiment_tables"


def test_plan_patent_graph_query_builds_problem_solution_plan():
    plan = plan_patent_graph_query("CN100355122C 解决了什么技术问题，提出了什么方案？")

    assert plan is not None
    assert plan.template_id == "list_patent_problem_solution"


def test_plan_patent_graph_query_builds_inventive_scope_plan():
    plan = plan_patent_graph_query("CN100355122C 的发明点和保护范围是什么？")

    assert plan is not None
    assert plan.template_id == "list_patent_inventive_scope"


def test_plan_patent_graph_query_builds_citation_plan():
    plan = plan_patent_graph_query("CN100355122C 引用了哪些专利？")

    assert plan is not None
    assert plan.template_id == "list_patent_citations"


def test_plan_patent_graph_query_builds_ipc_listing_plan():
    plan = plan_patent_graph_query("H01M10/0525 下有哪些专利？")

    assert plan is not None
    assert plan.template_id == "list_patents_by_ipc"
    assert plan.params == {"ipc_code": "H01M10/0525"}


def test_plan_patent_graph_query_builds_applicant_listing_plan():
    plan = plan_patent_graph_query("宁德时代新能源科技股份有限公司有哪些专利？")

    assert plan is not None
    assert plan.template_id == "list_patents_by_applicant"
    assert plan.params == {"organization_name": "宁德时代新能源科技股份有限公司"}


def test_plan_patent_graph_query_rejects_doi_question():
    assert plan_patent_graph_query("10.1039/c4ra15767b 这篇文献是什么？") is None


def test_plan_patent_graph_query_rejects_multi_patent_question():
    assert plan_patent_graph_query("CN100355122C 和 CN100371239C 有什么区别？") is None


@pytest.mark.parametrize(
    ("question", "required_aliases"),
    [
        (
            "CN100355122C 这件专利是什么？",
            {
                "patent_id",
                "title",
                "abstract",
                "application_date",
                "publication_date",
                "ipc_main",
                "patent_type",
                "legal_status",
                "source_file",
                "stub",
                "ipc_codes",
                "ipc_subclasses",
                "applicants",
                "agencies",
                "inventors",
            },
        ),
        (
            "CN100355122C 的工艺步骤是什么？",
            {"patent_id", "stub", "step_order", "step_name", "step_operation", "step_params_json", "step_template"},
        ),
        (
            "CN100355122C 使用了哪些原料？",
            {"patent_id", "stub", "role_name", "role_type", "role_ratio", "role_note", "material_name", "material_type", "material_canonical_key"},
        ),
        (
            "CN100355122C 有哪些实验表格和性能数据？",
            {"patent_id", "stub", "table_title", "row_label", "measurement_name", "measurement_value", "measurement_unit", "measurement_note"},
        ),
        (
            "CN100355122C 解决了什么技术问题，提出了什么方案？",
            {"patent_id", "stub", "problem_texts", "solution_texts", "scenario_texts"},
        ),
        (
            "CN100355122C 的发明点和保护范围是什么？",
            {
                "patent_id",
                "stub",
                "inventive_point_texts",
                "inventive_categories",
                "performance_fact_texts",
                "performance_categories",
                "protection_scope_texts",
                "protection_kinds",
                "claim_step_labels",
            },
        ),
        (
            "CN100355122C 引用了哪些专利？",
            {"patent_id", "stub", "cited_patent_id", "cited_title", "cited_publication_date", "cited_stub"},
        ),
        (
            "H01M10/0525 下有哪些专利？",
            {"patent_id", "title", "application_date", "publication_date", "ipc_match", "stub"},
        ),
        (
            "宁德时代新能源科技股份有限公司有哪些专利？",
            {"patent_id", "title", "application_date", "publication_date", "applicant_name", "stub"},
        ),
    ],
)
def test_cypher_templates_follow_alias_contracts(question, required_aliases):
    plan = plan_patent_graph_query(question)
    assert plan is not None

    cypher, params = _cypher_and_params(plan)

    assert params == plan.params
    assert "LIMIT" in cypher
    assert "MATCH (d:doi)" not in cypher
    assert ":raw_materials" not in cypher
    assert ":testing" not in cypher
    assert ":process)" not in cypher
    if "patent_id" in plan.params:
        assert "MATCH (p:Patent {patent_id: $patent_id})" in cypher
    for alias in required_aliases:
        assert f" AS {alias}" in cypher


def test_execute_patent_graph_plan_returns_empty_for_unavailable_client():
    plan = plan_patent_graph_query("CN100355122C 这件专利是什么？")
    assert plan is not None

    rows = execute_patent_graph_plan(
        plan,
        neo4j_client=SimpleNamespace(available=False),
        max_rows=5,
        timeout_ms=3000,
    )

    assert rows == []


def test_execute_patent_graph_plan_trims_rows_to_limit():
    plan = plan_patent_graph_query("CN100355122C 这件专利是什么？")
    assert plan is not None

    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            return [{"patent_id": "A"}, {"patent_id": "B"}, {"patent_id": "C"}]

    rows = execute_patent_graph_plan(
        plan,
        neo4j_client=_Client(),
        max_rows=2,
        timeout_ms=3000,
    )

    assert rows == [{"patent_id": "A"}, {"patent_id": "B"}]


def test_execute_patent_graph_plan_propagates_timeout_error():
    plan = plan_patent_graph_query("CN100355122C 这件专利是什么？")
    assert plan is not None

    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            raise TimeoutError("query timed out")

    with pytest.raises(TimeoutError, match="query timed out"):
        execute_patent_graph_plan(
            plan,
            neo4j_client=_Client(),
            max_rows=2,
            timeout_ms=3000,
        )
