from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from app.modules.graph_kb.client import execute_graph_kb_plan
from app.modules.graph_kb.guardrail import inspect_cypher
from app.modules.graph_kb.models import ExecutionTrace, GraphQueryPlanV2, RawExecutionResult
from app.modules.graph_kb.schema_registry import SchemaRegistry, build_default_schema_registry


logger = logging.getLogger(__name__)


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


def _compact_cypher(cypher: str, *, limit: int = 260) -> str:
    compact = " ".join(str(cypher or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _summarize_params(params: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in sorted((params or {}).items()):
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            summary[str(key)] = {"count": len(items), "sample": [str(item)[:80] for item in items[:3]]}
        elif isinstance(value, dict):
            summary[str(key)] = {"keys": sorted(str(item) for item in value.keys())[:8]}
        else:
            text = str(value or "")
            summary[str(key)] = text[:120]
    return summary


def _row_dois(rows: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    values: list[str] = []
    for row in rows:
        doi = str(row.get("doi") or "").strip()
        if doi and doi not in values:
            values.append(doi)
        for item in tuple(row.get("dois") or ()):
            text = str(item or "").strip()
            if text and text not in values:
                values.append(text)
    return tuple(values)


def execute_prepared_query(
    *,
    plan: GraphQueryPlanV2,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 0,
    max_path_attempts: int = 1,
    registry: SchemaRegistry | None = None,
) -> RawExecutionResult:
    started = time.perf_counter()
    graph = getattr(neo4j_client, "graph", None)
    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    logger.info(
        "graph_kb_v2 executor_start strategy=%s intent=%s max_rows=%s timeout_ms=%s max_path_attempts=%s graph_available=%s "
        "candidate_count=%s",
        plan.strategy,
        plan.intent,
        max_rows,
        timeout_ms,
        max_path_attempts,
        bool(graph is not None and getattr(neo4j_client, "available", False)),
        len(candidate_queries),
    )
    if graph is None or not bool(getattr(neo4j_client, "available", False)):
        logger.info(
            "graph_kb_v2 executor_end strategy=%s row_count=0 fallback_reason=neo4j_unavailable latency_ms=%.3f",
            plan.strategy,
            (time.perf_counter() - started) * 1000.0,
        )
        return RawExecutionResult(
            rows=(),
            trace=ExecutionTrace(strategy=plan.strategy, fallback_reason="neo4j_unavailable", neo4j_client="neo4jgraph"),
        )

    if plan.strategy == "template" and plan.legacy_template_plan is not None:
        template_started = time.perf_counter()
        logger.info(
            "graph_kb_v2 template_execute_start template_id=%s timeout_ms=%s max_rows=%s",
            plan.legacy_template_id or plan.legacy_template_plan.template_id,
            timeout_ms,
            max_rows,
        )
        rows = tuple(
            execute_graph_kb_plan(
                plan.legacy_template_plan,
                neo4j_client=neo4j_client,
                max_rows=max_rows,
                timeout_ms=timeout_ms,
            )
        )
        logger.info(
            "graph_kb_v2 template_execute_done template_id=%s row_count=%s fallback_reason=%s latency_ms=%.3f",
            plan.legacy_template_id or plan.legacy_template_plan.template_id,
            len(rows),
            "" if rows else "empty_result",
            (time.perf_counter() - template_started) * 1000.0,
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
    if not candidate_queries:
        logger.info(
            "graph_kb_v2 executor_end strategy=%s row_count=0 fallback_reason=no_candidate_queries latency_ms=%.3f",
            plan.strategy,
            (time.perf_counter() - started) * 1000.0,
        )
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
    collected_rows: list[dict[str, Any]] = []
    rejected_guardrail = False
    executed_candidate = False
    matched_path = ""
    last_guardrail_verdict = "allow"
    candidate_dois: tuple[str, ...] = ()
    for candidate in candidate_queries[: max(1, int(max_path_attempts or 1))]:
        path_id = str(candidate.get("path_id") or "generated.primary")
        attempted_paths.append(path_id)
        inspected = inspect_cypher(cypher=str(candidate.get("cypher") or ""), registry=registry)
        logger.info(
            "graph_kb_v2 candidate_guardrail path_id=%s verdict=%s issues=%s",
            path_id,
            inspected.verdict,
            inspected.issues,
        )
        if inspected.verdict != "allow":
            rejected_guardrail = True
            last_guardrail_verdict = inspected.verdict
            continue
        executed_candidate = True
        params = dict(candidate.get("params") or {})
        is_expansion_path = path_id.startswith("hybrid.expand")
        if plan.strategy == "multi_stage" and is_expansion_path and "candidate_dois" in params:
            params["candidate_dois"] = candidate_dois
            logger.info(
                "graph_kb_v2 candidate_doi_handoff path_id=%s candidate_doi_count=%s",
                path_id,
                len(candidate_dois),
            )
        candidate_started = time.perf_counter()
        normalized_cypher = inspected.normalized_cypher
        logger.info(
            "graph_kb_v2 candidate_execute_start path_id=%s strategy=%s cypher_sha1=%s cypher_preview=%s params=%s",
            path_id,
            plan.strategy,
            hashlib.sha1(str(normalized_cypher or "").encode("utf-8")).hexdigest()[:12],
            _compact_cypher(str(normalized_cypher or "")),
            _summarize_params(params),
        )
        rows = _run_cypher_once(
            graph=graph,
            cypher=normalized_cypher,
            params=params,
        )
        logger.info(
            "graph_kb_v2 candidate_execute_done path_id=%s row_count=%s doi_count=%s latency_ms=%.3f",
            path_id,
            len(rows),
            len(_row_dois(rows)),
            (time.perf_counter() - candidate_started) * 1000.0,
        )
        if rows:
            if plan.strategy == "multi_stage":
                collected_rows.extend(rows)
                if not is_expansion_path:
                    candidate_dois = _row_dois(rows)
                matched_path = path_id
                continue
            returned_rows = rows[: max(1, int(max_rows or 1))]
            logger.info(
                "graph_kb_v2 executor_end strategy=%s matched_path=%s row_count=%s fallback_reason= latency_ms=%.3f",
                plan.strategy,
                path_id,
                len(returned_rows),
                (time.perf_counter() - started) * 1000.0,
            )
            return RawExecutionResult(
                rows=returned_rows,
                trace=ExecutionTrace(
                    strategy=plan.strategy,
                    matched_path=path_id,
                    attempted_paths=tuple(attempted_paths),
                    fallback_reason="",
                    guardrail_verdict=inspected.verdict,
                    neo4j_client="neo4jgraph",
                ),
            )

    if collected_rows:
        logger.info(
            "graph_kb_v2 executor_end strategy=%s matched_path=%s row_count=%s fallback_reason= latency_ms=%.3f",
            plan.strategy,
            matched_path,
            len(collected_rows),
            (time.perf_counter() - started) * 1000.0,
        )
        return RawExecutionResult(
            rows=tuple(collected_rows),
            trace=ExecutionTrace(
                strategy=plan.strategy,
                matched_path=matched_path,
                attempted_paths=tuple(attempted_paths),
                fallback_reason="",
                guardrail_verdict="allow",
                neo4j_client="neo4jgraph",
            ),
        )

    fallback_reason = "guardrail_reject" if rejected_guardrail and not executed_candidate else "empty_result"
    logger.info(
        "graph_kb_v2 executor_end strategy=%s matched_path= row_count=0 fallback_reason=%s attempted_paths=%s latency_ms=%.3f",
        plan.strategy,
        fallback_reason,
        tuple(attempted_paths),
        (time.perf_counter() - started) * 1000.0,
    )
    return RawExecutionResult(
        rows=(),
        trace=ExecutionTrace(
            strategy=plan.strategy,
            matched_path="",
            attempted_paths=tuple(attempted_paths),
            fallback_reason=fallback_reason,
            guardrail_verdict=last_guardrail_verdict if rejected_guardrail and not executed_candidate else "allow",
            neo4j_client="neo4jgraph",
        ),
    )
