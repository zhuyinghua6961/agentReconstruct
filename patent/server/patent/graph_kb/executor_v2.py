from __future__ import annotations

from typing import Any

from server.patent.graph_kb.client import execute_patent_graph_plan
from server.patent.graph_kb.guardrail import inspect_patent_cypher
from server.patent.graph_kb.models import PatentExecutionTrace, PatentGraphQueryPlanV2, PatentRawExecutionResult, PatentSchemaRegistry
from server.patent.graph_kb.schema_registry import build_default_patent_schema_registry


def _normalize_rows(rows: Any) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for item in list(rows or []):
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        data = getattr(item, "data", None)
        if callable(data):
            payload = data()
            if isinstance(payload, dict):
                normalized.append(dict(payload))
    return tuple(normalized)


def execute_patent_prepared_query(
    *,
    plan: PatentGraphQueryPlanV2,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 0,
    max_path_attempts: int = 1,
    registry: PatentSchemaRegistry | None = None,
) -> PatentRawExecutionResult:
    if not bool(getattr(neo4j_client, "available", False)):
        return PatentRawExecutionResult(
            rows=(),
            trace=PatentExecutionTrace(
                strategy=plan.strategy,
                fallback_reason="neo4j_unavailable",
                guardrail_verdict="not_run",
            ),
        )

    if plan.strategy == "template" and plan.legacy_template_plan is not None:
        template_id = plan.legacy_template_id or plan.legacy_template_plan.template_id
        try:
            rows = _normalize_rows(
                execute_patent_graph_plan(
                    plan.legacy_template_plan,
                    neo4j_client=neo4j_client,
                    max_rows=max_rows,
                    timeout_ms=timeout_ms,
                )
            )
        except TimeoutError:
            return PatentRawExecutionResult(
                rows=(),
                trace=PatentExecutionTrace(
                    strategy="template",
                    attempted_paths=(template_id,),
                    fallback_reason="timeout",
                    guardrail_verdict="trusted_template",
                ),
            )

        return PatentRawExecutionResult(
            rows=rows,
            trace=PatentExecutionTrace(
                strategy="template",
                matched_path=template_id,
                attempted_paths=(template_id,),
                fallback_reason="" if rows else "empty_result",
                guardrail_verdict="trusted_template",
            ),
        )

    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    if not candidate_queries:
        return PatentRawExecutionResult(
            rows=(),
            trace=PatentExecutionTrace(
                strategy=plan.strategy,
                attempted_paths=(),
                fallback_reason="no_candidate_queries",
                guardrail_verdict="not_run",
            ),
        )

    registry = registry or build_default_patent_schema_registry()
    attempted_paths: list[str] = []
    saw_reject = False
    saw_allow = False

    for candidate in candidate_queries[: max(1, int(max_path_attempts or 1))]:
        path_id = str(candidate.get("path_id") or "parametric.unknown")
        attempted_paths.append(path_id)
        inspected = inspect_patent_cypher(
            cypher=str(candidate.get("cypher") or ""),
            registry=registry,
        )
        if inspected.verdict != "allow":
            saw_reject = True
            continue

        saw_allow = True
        query = getattr(neo4j_client, "query", None)
        if not callable(query):
            return PatentRawExecutionResult(
                rows=(),
                trace=PatentExecutionTrace(
                    strategy=plan.strategy,
                    attempted_paths=tuple(attempted_paths),
                    fallback_reason="neo4j_unavailable",
                    guardrail_verdict=inspected.verdict,
                ),
            )
        try:
            rows = _normalize_rows(
                query(
                    inspected.normalized_cypher,
                    dict(candidate.get("params") or {}),
                    timeout_ms=int(timeout_ms or 0),
                )
            )
        except TimeoutError:
            return PatentRawExecutionResult(
                rows=(),
                trace=PatentExecutionTrace(
                    strategy=plan.strategy,
                    attempted_paths=tuple(attempted_paths),
                    fallback_reason="timeout",
                    guardrail_verdict=inspected.verdict,
                ),
            )

        if rows:
            return PatentRawExecutionResult(
                rows=rows[: max(1, int(max_rows or 1))],
                trace=PatentExecutionTrace(
                    strategy=plan.strategy,
                    matched_path=path_id,
                    attempted_paths=tuple(attempted_paths),
                    fallback_reason="",
                    guardrail_verdict=inspected.verdict,
                ),
            )

    fallback_reason = "guardrail_reject" if saw_reject and not saw_allow else "empty_result"
    guardrail_verdict = "reject" if saw_reject and not saw_allow else ("allow" if saw_allow else "not_run")
    return PatentRawExecutionResult(
        rows=(),
        trace=PatentExecutionTrace(
            strategy=plan.strategy,
            attempted_paths=tuple(attempted_paths),
            fallback_reason=fallback_reason,
            guardrail_verdict=guardrail_verdict,
        ),
    )
