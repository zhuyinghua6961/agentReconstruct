from __future__ import annotations

import time
from typing import Any

from server.patent.graph_kb.classifier import classify_patent_graph_kb_question
from server.patent.graph_kb.client import execute_patent_graph_plan, plan_patent_graph_query
from server.patent.graph_kb.models import PatentGraphKbExecutionResult
from server.patent.graph_kb.rendering import render_patent_graph_answer


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
