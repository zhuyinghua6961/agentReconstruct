from __future__ import annotations

from typing import Any

from app.modules.graph_kb.client import execute_graph_kb_plan
from app.modules.graph_kb.guardrail import inspect_cypher
from app.modules.graph_kb.models import ExecutionTrace, GraphQueryPlanV2, RawExecutionResult
from app.modules.graph_kb.schema_registry import SchemaRegistry, build_default_schema_registry


def _normalize_rows(rows: Any) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for item in list(rows or []):
        if isinstance(item, dict):
            normalized.append(dict(item))
        elif hasattr(item, "data"):
            data = item.data()
            if isinstance(data, dict):
                normalized.append(dict(data))
    return tuple(normalized)


def _run_cypher_once(
    *,
    graph: Any,
    cypher: str,
    params: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    if hasattr(graph, "query"):
        return _normalize_rows(graph.query(cypher, params))
    driver = getattr(graph, "_driver", None)
    if driver is None:
        return ()
    rows, _, _ = driver.execute_query(cypher, database_=getattr(graph, "_database", None), parameters_=params)
    return _normalize_rows(rows)


def execute_prepared_query(
    *,
    plan: GraphQueryPlanV2,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 0,
    max_path_attempts: int = 1,
    registry: SchemaRegistry | None = None,
) -> RawExecutionResult:
    _ = timeout_ms
    graph = getattr(neo4j_client, "graph", None)
    if graph is None or not bool(getattr(neo4j_client, "available", False)):
        return RawExecutionResult(
            rows=(),
            trace=ExecutionTrace(strategy=plan.strategy, fallback_reason="neo4j_unavailable", neo4j_client="neo4jgraph"),
        )

    if plan.strategy == "template" and plan.legacy_template_plan is not None:
        rows = tuple(
            execute_graph_kb_plan(
                plan.legacy_template_plan,
                neo4j_client=neo4j_client,
                max_rows=max_rows,
                timeout_ms=timeout_ms,
            )
        )
        return RawExecutionResult(
            rows=rows,
            trace=ExecutionTrace(
                strategy="template",
                matched_path=plan.legacy_template_id or plan.legacy_template_plan.template_id,
                attempted_paths=(plan.legacy_template_id or plan.legacy_template_plan.template_id,),
                fallback_reason="" if rows else "empty_result",
                guardrail_verdict="trusted_template",
                neo4j_client="neo4jgraph",
            ),
        )

    registry = registry or build_default_schema_registry()
    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    if not candidate_queries:
        return RawExecutionResult(
            rows=(),
            trace=ExecutionTrace(
                strategy=plan.strategy,
                matched_path="",
                attempted_paths=(),
                fallback_reason="no_candidate_queries",
                guardrail_verdict="not_run",
                neo4j_client="neo4jgraph",
            ),
        )

    attempted_paths: list[str] = []
    rejected_guardrail = False
    executed_candidate = False
    last_guardrail_verdict = "allow"
    for candidate in candidate_queries[: max(1, int(max_path_attempts or 1))]:
        path_id = str(candidate.get("path_id") or "generated.primary")
        attempted_paths.append(path_id)
        inspected = inspect_cypher(cypher=str(candidate.get("cypher") or ""), registry=registry)
        if inspected.verdict != "allow":
            rejected_guardrail = True
            last_guardrail_verdict = inspected.verdict
            continue
        executed_candidate = True
        rows = _run_cypher_once(
            graph=graph,
            cypher=inspected.normalized_cypher,
            params=dict(candidate.get("params") or {}),
        )
        if rows:
            return RawExecutionResult(
            rows=rows[: max(1, int(max_rows or 1))],
            trace=ExecutionTrace(
                strategy=plan.strategy,
                matched_path=path_id,
                    attempted_paths=tuple(attempted_paths),
                    fallback_reason="",
                    guardrail_verdict=inspected.verdict,
                    neo4j_client="neo4jgraph",
                ),
            )

    return RawExecutionResult(
        rows=(),
        trace=ExecutionTrace(
            strategy=plan.strategy,
            matched_path="",
            attempted_paths=tuple(attempted_paths),
            fallback_reason="guardrail_reject" if rejected_guardrail and not executed_candidate else "empty_result",
            guardrail_verdict=last_guardrail_verdict if rejected_guardrail and not executed_candidate else "allow",
            neo4j_client="neo4jgraph",
        ),
    )
