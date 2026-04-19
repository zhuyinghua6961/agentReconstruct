from __future__ import annotations

from types import SimpleNamespace

from app.modules.graph_kb.client import (
    build_legacy_template_query_plan,
    execute_graph_kb_plan,
    plan_graph_kb_query,
)


def test_plan_lookup_by_doi():
    plan = plan_graph_kb_query("10.1000/test 这篇文献是什么？")

    assert plan is not None
    assert plan.template_id == "lookup_by_doi"
    assert plan.params["doi"] == "10.1000/test"


def test_build_legacy_template_query_plan_reuses_existing_hardcoded_templates():
    plan = build_legacy_template_query_plan("10.1000/test 这篇文献是什么？")

    assert plan is not None
    assert plan.template_id == "lookup_by_doi"


def test_plan_expand_doi_context_by_doi():
    plan = plan_graph_kb_query("10.1039/c4ra15767b 这篇文献做了哪些测试和工艺？")

    assert plan is not None
    assert plan.template_id == "expand_doi_context_by_doi"
    assert plan.params["doi"] == "10.1039/c4ra15767b"
    assert plan.params["include_testing"] is True
    assert plan.params["include_process"] is True


def test_plan_list_by_material():
    plan = plan_graph_kb_query("有哪些关于LFP的文献？")

    assert plan is not None
    assert plan.template_id == "list_by_material"
    assert plan.params["material_name"] == "LFP"


def test_plan_list_by_raw_material():
    plan = plan_graph_kb_query("有哪些使用LiFePO4作为原料的文献？")

    assert plan is not None
    assert plan.template_id == "list_by_raw_material"
    assert plan.params["material_name"] == "LiFePO4"


def test_plan_keeps_generic_doi_material_question_on_default_doi_lookup():
    plan = plan_graph_kb_query("10.1039/c4ra15767b 这篇文献的材料体系是什么？")

    assert plan is not None
    assert plan.template_id == "lookup_by_doi"
    assert plan.params["doi"] == "10.1039/c4ra15767b"


def test_plan_rejects_material_wording_for_raw_material_template():
    assert plan_graph_kb_query("有哪些使用LiFePO4作为材料的文献？") is None


def test_plan_allows_digit_bearing_material_name_for_literature_listing():
    plan = plan_graph_kb_query("有哪些关于LiFePO4的文献？")

    assert plan is not None
    assert plan.template_id == "list_by_material"
    assert plan.params["material_name"] == "LiFePO4"


def test_plan_allows_non_ranking_keyword_containing_qian_character():
    plan = plan_graph_kb_query("有哪些关于前驱体的文献？")

    assert plan is not None
    assert plan.template_id == "list_by_material"
    assert plan.params["material_name"] == "前驱体"


def test_plan_rejects_numeric_property_query():
    assert plan_graph_kb_query("压实密度大于3.5的材料有哪些？") is None


def test_plan_rejects_literature_query_with_trailing_numeric_filter():
    assert plan_graph_kb_query("LFP有多少篇文献的压实密度大于3.5？") is None


def test_plan_rejects_literature_query_when_keyword_is_property_phrase():
    assert plan_graph_kb_query("有哪些关于压实密度大于3.5的文献？") is None


def test_plan_count_by_filter():
    plan = plan_graph_kb_query("LFP有多少篇文献？")

    assert plan is not None
    assert plan.template_id == "count_by_filter"
    assert plan.params["material_name"] == "LFP"


def test_plan_rejects_relation_query():
    assert plan_graph_kb_query("LFP和石墨是否存在相关关系？") is None


def test_plan_rejects_unknown_numeric_property():
    assert plan_graph_kb_query("未知指标大于3的材料有哪些？") is None


def test_plan_lookup_by_doi_uses_literature_graph_shape():
    captured: dict[str, object] = {}

    class _Graph:
        def query(self, cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return [{"doi": "10.1000/test", "title": "Test Paper"}]

    plan = plan_graph_kb_query("10.1000/test 这篇文献是什么？")
    rows = execute_graph_kb_plan(
        plan,
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=2,
    )

    assert rows == [{"doi": "10.1000/test", "title": "Test Paper"}]
    assert ":doi" in str(captured["cypher"])
    assert ":title" in str(captured["cypher"])


def test_plan_expand_doi_context_uses_testing_and_process_shape():
    captured: dict[str, object] = {}

    class _Graph:
        def query(self, cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return [{"doi": "10.1039/c4ra15767b", "title": "Test Paper"}]

    plan = plan_graph_kb_query("10.1039/c4ra15767b 这篇文献做了哪些测试和工艺？")
    rows = execute_graph_kb_plan(
        plan,
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=2,
    )

    assert rows == [{"doi": "10.1039/c4ra15767b", "title": "Test Paper"}]
    assert ":testing" in str(captured["cypher"])
    assert ":process" in str(captured["cypher"])


def test_execute_graph_kb_plan_trims_rows_to_limit():
    captured: dict[str, object] = {}

    class _Graph:
        def query(self, cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return [{"name": f"item-{idx}"} for idx in range(5)]

    plan = plan_graph_kb_query("有哪些关于LFP的文献？")
    rows = execute_graph_kb_plan(
        plan,
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=2,
    )

    assert len(rows) == 2
    assert "MATCH" in str(captured["cypher"])
    assert captured["params"]["material_name"] == "LFP"


def test_execute_graph_kb_plan_uses_driver_timeout_when_available():
    captured: dict[str, object] = {}

    class _Record:
        def __init__(self, payload):
            self._payload = payload

        def data(self):
            return dict(self._payload)

    class _Driver:
        def execute_query(self, query, database_=None, parameters_=None):
            captured["timeout"] = getattr(query, "timeout", None)
            captured["database"] = database_
            captured["params"] = dict(parameters_ or {})
            return ([_Record({"doi": "10.1/a"})], None, None)

    plan = plan_graph_kb_query("10.1000/test 这篇文献是什么？")
    rows = execute_graph_kb_plan(
        plan,
        neo4j_client=SimpleNamespace(
            graph=SimpleNamespace(_driver=_Driver(), _database="neo4j", sanitize=False),
            available=True,
            degraded=False,
        ),
        max_rows=5,
        timeout_ms=1500,
    )

    assert rows == [{"doi": "10.1/a"}]
    assert captured["timeout"] == 1.5
    assert captured["database"] == "neo4j"


def test_execute_graph_kb_plan_converts_driver_timeout_to_timeout_error():
    class _TimeoutError(Exception):
        code = "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"
        message = "The transaction has timed out"

    class _Driver:
        def execute_query(self, query, database_=None, parameters_=None):
            raise _TimeoutError()

    plan = plan_graph_kb_query("10.1000/test 这篇文献是什么？")

    try:
        execute_graph_kb_plan(
            plan,
            neo4j_client=SimpleNamespace(
                graph=SimpleNamespace(_driver=_Driver(), _database="neo4j", sanitize=False),
                available=True,
                degraded=False,
            ),
            max_rows=5,
            timeout_ms=1500,
        )
    except TimeoutError as exc:
        assert "timed out" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("expected TimeoutError")
