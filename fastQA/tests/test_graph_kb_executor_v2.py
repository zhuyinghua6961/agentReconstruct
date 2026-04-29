from __future__ import annotations

from types import SimpleNamespace

from app.integrations.neo4j.client import bootstrap_neo4j
from app.modules.graph_kb.executor_v2 import execute_prepared_query
from app.modules.graph_kb.models import GraphKbQueryPlan, GraphQueryPlanV2


def test_executor_tries_reverse_path_when_forward_path_is_empty():
    calls: list[str] = []

    class _Graph:
        def query(self, cypher, params):
            calls.append(str(cypher))
            if params.get("marker") == "forward":
                return []
            return [{"doi": "10.1000/test", "title": "Reverse Match"}]

    plan = GraphQueryPlanV2(
        strategy="parametric",
        parametric_slots={
            "candidate_queries": [
                {"path_id": "name.forward", "cypher": "MATCH (n:doi) RETURN n.name AS doi LIMIT 5", "params": {"marker": "forward"}},
                {"path_id": "name.reverse", "cypher": "MATCH (n:doi) RETURN n.name AS doi, $marker AS marker LIMIT 5", "params": {"marker": "reverse"}},
            ]
        },
    )

    result = execute_prepared_query(
        plan=plan,
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
        max_path_attempts=2,
    )

    assert result.trace.matched_path == "name.reverse"
    assert result.trace.attempted_paths == ("name.forward", "name.reverse")
    assert len(calls) == 2


def test_executor_uses_bootstrapped_neo4jgraph_instead_of_second_client():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [{"doi": "10.1000/test", "title": "Bootstrapped"}]

    neo4j_client = bootstrap_neo4j(
        url="bolt://127.0.0.1:7687",
        username="neo4j",
        password="secret",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        graph_factory=lambda **kwargs: _Graph(),
    )
    plan = GraphQueryPlanV2(
        strategy="template",
        legacy_template_id="lookup_by_doi",
        legacy_template_plan=GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": "10.1000/test"}),
    )

    result = execute_prepared_query(
        plan=plan,
        neo4j_client=neo4j_client,
        max_rows=5,
    )

    assert result.trace.strategy in {"template", "parametric", "llm_cypher"}
    assert result.trace.neo4j_client == "neo4jgraph"
    assert result.rows[0]["doi"] == "10.1000/test"


def test_executor_does_not_fall_back_to_global_doi_scan_when_candidate_queries_missing():
    calls: list[str] = []

    class _Graph:
        def query(self, cypher, params):
            _ = params
            calls.append(str(cypher))
            return [{"doi": "10.1000/test"}]

    result = execute_prepared_query(
        plan=GraphQueryPlanV2(strategy="parametric", parametric_slots={}),
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert result.rows == ()
    assert result.trace.fallback_reason == "no_candidate_queries"
    assert calls == []


def test_executor_continues_to_next_candidate_after_guardrail_reject():
    calls: list[str] = []

    class _Graph:
        def query(self, cypher, params):
            _ = params
            calls.append(str(cypher))
            return [{"doi": "10.1000/test"}]

    plan = GraphQueryPlanV2(
        strategy="parametric",
        parametric_slots={
            "candidate_queries": [
                {"path_id": "bad", "cypher": "MATCH (d:forbidden) RETURN d.name AS doi LIMIT 5", "params": {}},
                {"path_id": "good", "cypher": "MATCH (d:doi) RETURN d.name AS doi LIMIT 5", "params": {}},
            ]
        },
    )

    result = execute_prepared_query(
        plan=plan,
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
        max_path_attempts=2,
    )

    assert result.trace.matched_path == "good"
    assert result.rows[0]["doi"] == "10.1000/test"
    assert calls == ["MATCH (d:doi) RETURN d.name AS doi LIMIT 5"]


def test_executor_reports_unavailable_and_caps_rows():
    result = execute_prepared_query(
        plan=GraphQueryPlanV2(
            strategy="parametric",
            parametric_slots={
                "candidate_queries": [{"path_id": "doi.scan", "cypher": "MATCH (d:doi) RETURN d.name AS doi LIMIT 5", "params": {}}]
            },
        ),
        neo4j_client=SimpleNamespace(graph=None, available=False),
        max_rows=1,
    )

    assert result.rows == ()
    assert result.trace.fallback_reason == "neo4j_unavailable"

    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [{"doi": "10.1000/a"}, {"doi": "10.1000/b"}]

    capped = execute_prepared_query(
        plan=GraphQueryPlanV2(
            strategy="parametric",
            parametric_slots={
                "candidate_queries": [{"path_id": "doi.scan", "cypher": "MATCH (d:doi) RETURN d.name AS doi LIMIT 5", "params": {}}]
            },
        ),
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True),
        max_rows=1,
    )

    assert capped.rows == ({"doi": "10.1000/a"},)


def test_executor_reports_guardrail_reject_when_all_candidates_rejected():
    result = execute_prepared_query(
        plan=GraphQueryPlanV2(
            strategy="parametric",
            parametric_slots={
                "candidate_queries": [
                    {"path_id": "bad.label", "cypher": "MATCH (d:forbidden) RETURN d.name AS doi LIMIT 5", "params": {}}
                ]
            },
        ),
        neo4j_client=SimpleNamespace(graph=SimpleNamespace(query=lambda *_args, **_kwargs: []), available=True),
        max_rows=5,
    )

    assert result.rows == ()
    assert result.trace.attempted_paths == ("bad.label",)
    assert result.trace.fallback_reason == "guardrail_reject"


def test_executor_multi_stage_collects_rows_from_all_successful_paths():
    calls: list[str] = []

    class _Graph:
        def query(self, cypher, params):
            _ = params
            calls.append(str(cypher))
            if "candidate" in str(cypher):
                return [{"doi": "10.1000/test", "value": "155 mAh/g"}]
            return [{"doi": "10.1000/test", "preparation_methods": ["solid-state"], "carbon_sources": ["sucrose"]}]

    plan = GraphQueryPlanV2(
        strategy="multi_stage",
        parametric_slots={
            "candidate_queries": [
                {"path_id": "hybrid.candidate", "cypher": "MATCH (d:doi) RETURN d.name AS doi, 'candidate' AS stage LIMIT 5", "params": {}},
                {"path_id": "hybrid.expand", "cypher": "MATCH (d:doi) RETURN d.name AS doi, 'expand' AS stage LIMIT 5", "params": {}},
            ]
        },
    )

    result = execute_prepared_query(
        plan=plan,
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True),
        max_rows=10,
        max_path_attempts=2,
    )

    assert result.trace.matched_path == "hybrid.expand"
    assert result.trace.attempted_paths == ("hybrid.candidate", "hybrid.expand")
    assert result.rows == (
        {"doi": "10.1000/test", "value": "155 mAh/g"},
        {"doi": "10.1000/test", "preparation_methods": ["solid-state"], "carbon_sources": ["sucrose"]},
    )
    assert len(calls) == 2


def test_executor_multi_stage_passes_candidate_dois_to_expansion_paths():
    calls: list[tuple[str, dict]] = []

    class _Graph:
        def query(self, cypher, params):
            calls.append((str(cypher), dict(params)))
            if params.get("candidate_dois"):
                return [{"doi": doi, "preparation_methods": ["solid-state"]} for doi in params.get("candidate_dois", ())]
            return [{"doi": "10.1000/a", "value": "155 mAh/g"}, {"doi": "10.1000/b", "value": "145 mAh/g"}]

    plan = GraphQueryPlanV2(
        strategy="multi_stage",
        parametric_slots={
            "candidate_queries": [
                {"path_id": "hybrid.candidate", "cypher": "MATCH (d:doi) RETURN d.name AS doi, 'candidate' AS stage LIMIT 5", "params": {}},
                {
                    "path_id": "hybrid.expand",
                    "cypher": "MATCH (d:doi) WHERE d.name IN $candidate_dois RETURN d.name AS doi, 'expand' AS stage LIMIT 5",
                    "params": {"candidate_dois": ()},
                },
            ]
        },
    )

    result = execute_prepared_query(
        plan=plan,
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True),
        max_rows=10,
        max_path_attempts=2,
    )

    assert calls[1][1]["candidate_dois"] == ("10.1000/a", "10.1000/b")
    assert {"doi": "10.1000/a", "preparation_methods": ["solid-state"]} in result.rows
