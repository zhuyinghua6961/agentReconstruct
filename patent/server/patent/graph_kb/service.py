from __future__ import annotations

import time
from typing import Any

from server.patent.graph_kb.classifier import classify_patent_graph_kb_question
from server.patent.graph_kb.classifier_v2 import classify_patent_graph_question_v2
from server.patent.graph_kb.canonicalizer import canonicalize_patent_graph_rows
from server.patent.graph_kb.client import execute_patent_graph_plan, plan_patent_graph_query
from server.patent.graph_kb.direct_renderer import render_patent_direct_answer
from server.patent.graph_kb.executor_v2 import execute_patent_prepared_query
from server.patent.graph_kb.models import PatentGraphKbExecutionResult, PatentGraphRoutingResult
from server.patent.graph_kb.planner_v2 import build_patent_graph_query_plan_v2
from server.patent.graph_kb.rag_adapter import build_patent_graph_rag_payload
from server.patent.graph_kb.rendering import render_patent_graph_answer
from server.patent.graph_kb.schema_registry import build_default_patent_schema_registry


def try_patent_graph_kb_answer(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int,
    generation_runtime: Any | None = None,
) -> PatentGraphKbExecutionResult:
    _ = generation_runtime
    decision = classify_patent_graph_kb_question(question, conversation_context=conversation_context or {})
    if decision.decision != "try_graph":
        return PatentGraphKbExecutionResult(handled=False, fallback_reason=decision.reason)

    plan = plan_patent_graph_query(question)
    if plan is None:
        return PatentGraphKbExecutionResult(handled=False, fallback_reason="no_plan")

    started = time.perf_counter()
    try:
        rows = execute_patent_graph_plan(
            plan,
            neo4j_client=neo4j_client,
            max_rows=max_rows,
            timeout_ms=timeout_ms,
        )
    except TimeoutError:
        return PatentGraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            fallback_reason="timeout",
        )

    latency_ms = (time.perf_counter() - started) * 1000.0
    if not rows:
        return PatentGraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            latency_ms=latency_ms,
            fallback_reason="empty_result",
        )

    answer, references, reference_objects, metadata = render_patent_graph_answer(plan, rows)
    if not answer.strip():
        return PatentGraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            latency_ms=latency_ms,
            fallback_reason=str(metadata.get("stub_fallback_reason") or "render_empty"),
            metadata=dict(metadata),
        )

    return PatentGraphKbExecutionResult(
        handled=True,
        answer=answer,
        references=references,
        reference_objects=reference_objects,
        query_mode="patent_graph_kb",
        template_id=plan.template_id,
        result_count=len(rows),
        latency_ms=latency_ms,
        fallback_reason="",
        metadata=dict(metadata),
    )


def route_patent_graph_kb_v2(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 3000,
    generation_runtime: Any | None = None,
) -> PatentGraphRoutingResult:
    _ = generation_runtime
    started = time.perf_counter()
    decision = classify_patent_graph_question_v2(
        question=question,
        conversation_context=conversation_context or {},
    )
    plan = build_patent_graph_query_plan_v2(
        question=question,
        decision=decision,
        schema_registry=build_default_patent_schema_registry(),
    )
    diagnostics = dict(decision.diagnostics or {})
    diagnostics["route_family"] = decision.route_family
    diagnostics["graph_pipeline_version"] = "v2"
    diagnostics["tri_state_mode"] = decision.mode
    diagnostics["strategy"] = plan.strategy if plan is not None else ""

    if decision.mode == "skip_graph" or plan is None:
        return PatentGraphRoutingResult(mode="skip_graph", diagnostics=diagnostics)

    execution = execute_patent_prepared_query(
        plan=plan,
        neo4j_client=neo4j_client,
        max_rows=max_rows,
        timeout_ms=timeout_ms,
        max_path_attempts=2,
    )
    diagnostics["matched_path"] = execution.trace.matched_path
    diagnostics["attempted_paths"] = execution.trace.attempted_paths
    diagnostics["guardrail_verdict"] = execution.trace.guardrail_verdict
    diagnostics["neo4j_client"] = execution.trace.neo4j_client
    if execution.trace.fallback_reason:
        diagnostics["fallback_reason"] = execution.trace.fallback_reason

    bundle = canonicalize_patent_graph_rows(plan=plan, rows=execution.rows)
    diagnostics.update(dict(bundle.diagnostics or {}))
    rag_payload = build_patent_graph_rag_payload(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )

    if decision.mode == "direct_answer":
        direct = render_patent_direct_answer(
            decision=decision,
            plan=plan,
            bundle=bundle,
        )
        if direct.handled:
            template_id = str(plan.legacy_template_id or bundle.render_slots.get("path_id") or "")
            direct_metadata = dict(direct.metadata or {})
            direct_metadata.setdefault("graph_pipeline_version", "v2")
            direct_metadata.setdefault("graph_kb_strategy", plan.strategy)
            latency_ms = (time.perf_counter() - started) * 1000.0
            return PatentGraphRoutingResult(
                mode="direct_answer",
                direct_result=PatentGraphKbExecutionResult(
                    handled=True,
                    answer=direct.answer,
                    references=direct.references,
                    reference_objects=direct.reference_objects,
                    query_mode="patent_graph_kb",
                    template_id=template_id,
                    result_count=len(tuple(bundle.render_slots.get("rows") or ())),
                    latency_ms=latency_ms,
                    fallback_reason="",
                    metadata=direct_metadata,
                ),
                diagnostics=diagnostics,
            )

        diagnostics["direct_fallback_reason"] = str(direct.metadata.get("reason") or execution.trace.fallback_reason or "render_unavailable")
        has_rag_signal = bool(
            rag_payload.stage1_context_block
            or rag_payload.stage2_patent_candidates
            or rag_payload.stage2_constraints
            or rag_payload.stage4_fact_block
        )
        return PatentGraphRoutingResult(
            mode="graph_for_rag" if has_rag_signal else "skip_graph",
            rag_payload=rag_payload if has_rag_signal else None,
            diagnostics=diagnostics,
        )

    return PatentGraphRoutingResult(
        mode=decision.mode,
        rag_payload=rag_payload,
        diagnostics=diagnostics,
    )
