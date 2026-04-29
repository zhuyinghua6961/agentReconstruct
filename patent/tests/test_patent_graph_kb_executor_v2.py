from __future__ import annotations

from types import SimpleNamespace

from server.patent.graph_kb.client import build_patent_parametric_query_candidates
from server.patent.graph_kb.models import PatentGraphKbQueryPlan, PatentGraphQueryPlanV2
from server.patent.graph_kb.schema_registry import build_default_patent_schema_registry
import server.patent.graph_kb.executor_v2 as executor_v2
from server.patent.graph_kb.executor_v2 import execute_patent_prepared_query


def test_executor_v2_delegates_template_plans_to_legacy_executor(monkeypatch):
    calls = {}

    def _fake_execute(plan, *, neo4j_client, max_rows, timeout_ms):
        calls["template_id"] = plan.template_id
        calls["max_rows"] = max_rows
        calls["timeout_ms"] = timeout_ms
        return [{"patent_id": "CN100355122C"}]

    monkeypatch.setattr(executor_v2, "execute_patent_graph_plan", _fake_execute)

    result = execute_patent_prepared_query(
        plan=PatentGraphQueryPlanV2(
            strategy="template",
            intent="lookup_patent_by_id",
            legacy_template_id="lookup_patent_by_id",
            legacy_template_plan=PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": "CN100355122C"}),
        ),
        neo4j_client=SimpleNamespace(available=True),
        max_rows=5,
        timeout_ms=3000,
    )

    assert result.rows == ({"patent_id": "CN100355122C"},)
    assert result.trace.strategy == "template"
    assert result.trace.matched_path == "lookup_patent_by_id"
    assert result.trace.attempted_paths == ("lookup_patent_by_id",)
    assert result.trace.guardrail_verdict == "trusted_template"
    assert calls == {
        "template_id": "lookup_patent_by_id",
        "max_rows": 5,
        "timeout_ms": 3000,
    }


def test_executor_v2_runs_guardrailed_parametric_query():
    calls = {}

    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            calls["cypher"] = cypher
            calls["params"] = params
            calls["timeout_ms"] = timeout_ms
            return [{"patent_id": "CN100355122C", "inventor_name": "张三"}]

    result = execute_patent_prepared_query(
        plan=PatentGraphQueryPlanV2(
            strategy="parametric",
            intent="inventor_listing",
            question="发明人张三有哪些专利？",
            parametric_slots={
                "candidate_queries": build_patent_parametric_query_candidates("发明人张三有哪些专利？"),
            },
        ),
        neo4j_client=_Client(),
        max_rows=5,
        timeout_ms=3000,
        registry=build_default_patent_schema_registry(),
    )

    assert result.rows == ({"patent_id": "CN100355122C", "inventor_name": "张三"},)
    assert result.trace.strategy == "parametric"
    assert result.trace.matched_path == "list_patents_by_inventor"
    assert result.trace.attempted_paths == ("list_patents_by_inventor",)
    assert result.trace.guardrail_verdict == "allow"
    assert calls["params"]["inventor_name"] == "张三"
    assert calls["timeout_ms"] == 3000


def test_executor_v2_returns_empty_trace_for_missing_candidates():
    result = execute_patent_prepared_query(
        plan=PatentGraphQueryPlanV2(
            strategy="parametric",
            intent="inventor_listing",
            parametric_slots={"candidate_queries": []},
        ),
        neo4j_client=SimpleNamespace(available=True),
        max_rows=5,
        registry=build_default_patent_schema_registry(),
    )

    assert result.rows == ()
    assert result.trace.attempted_paths == ()
    assert result.trace.fallback_reason == "no_candidate_queries"
    assert result.trace.guardrail_verdict == "not_run"


def test_executor_v2_records_guardrail_rejection():
    result = execute_patent_prepared_query(
        plan=PatentGraphQueryPlanV2(
            strategy="parametric",
            intent="unsafe",
            parametric_slots={
                "candidate_queries": [
                    {
                        "path_id": "unsafe.write",
                        "cypher": "MATCH (p:Patent) SET p.title = 'x' RETURN p",
                        "params": {},
                    }
                ]
            },
        ),
        neo4j_client=SimpleNamespace(available=True),
        max_rows=5,
        registry=build_default_patent_schema_registry(),
    )

    assert result.rows == ()
    assert result.trace.attempted_paths == ("unsafe.write",)
    assert result.trace.matched_path == ""
    assert result.trace.fallback_reason == "guardrail_reject"
    assert result.trace.guardrail_verdict == "reject"


def test_executor_v2_honors_max_path_attempts_and_tracks_match():
    calls = []

    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            calls.append((cypher, params, timeout_ms))
            if params.get("inventor_name") == "张三":
                return []
            if params.get("agency_name") == "北京理工专利事务所":
                return [{"patent_id": "CN100355122C", "agency_name": "北京理工专利事务所"}]
            raise AssertionError("unexpected candidate execution")

    result = execute_patent_prepared_query(
        plan=PatentGraphQueryPlanV2(
            strategy="parametric",
            intent="comparison",
            parametric_slots={
                "candidate_queries": [
                    build_patent_parametric_query_candidates("发明人张三有哪些专利？")[0],
                    build_patent_parametric_query_candidates("代理机构北京理工专利事务所有哪些专利？")[0],
                    {
                        "path_id": "never.run",
                        "cypher": "MATCH (p:Patent) RETURN p LIMIT 1",
                        "params": {},
                    },
                ]
            },
        ),
        neo4j_client=_Client(),
        max_rows=5,
        max_path_attempts=2,
        timeout_ms=3000,
        registry=build_default_patent_schema_registry(),
    )

    assert result.rows == ({"patent_id": "CN100355122C", "agency_name": "北京理工专利事务所"},)
    assert result.trace.attempted_paths == ("list_patents_by_inventor", "list_patents_by_agency")
    assert result.trace.matched_path == "list_patents_by_agency"
    assert len(calls) == 2


def test_executor_v2_degrades_safely_for_timeout_and_unavailable_client():
    class _TimeoutClient:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            raise TimeoutError("query timed out")

    timeout_result = execute_patent_prepared_query(
        plan=PatentGraphQueryPlanV2(
            strategy="parametric",
            intent="inventor_listing",
            parametric_slots={
                "candidate_queries": build_patent_parametric_query_candidates("发明人张三有哪些专利？"),
            },
        ),
        neo4j_client=_TimeoutClient(),
        max_rows=5,
        timeout_ms=3000,
        registry=build_default_patent_schema_registry(),
    )

    assert timeout_result.rows == ()
    assert timeout_result.trace.attempted_paths == ("list_patents_by_inventor",)
    assert timeout_result.trace.fallback_reason == "timeout"

    unavailable_result = execute_patent_prepared_query(
        plan=PatentGraphQueryPlanV2(
            strategy="parametric",
            intent="inventor_listing",
            parametric_slots={
                "candidate_queries": build_patent_parametric_query_candidates("发明人张三有哪些专利？"),
            },
        ),
        neo4j_client=SimpleNamespace(available=False),
        max_rows=5,
        registry=build_default_patent_schema_registry(),
    )

    assert unavailable_result.rows == ()
    assert unavailable_result.trace.fallback_reason == "neo4j_unavailable"
