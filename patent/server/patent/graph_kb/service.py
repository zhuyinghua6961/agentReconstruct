from __future__ import annotations

import logging
import time
from typing import Any

from server.patent.graph_kb.classifier import classify_patent_graph_kb_question
from server.patent.graph_kb.classifier_v2 import classify_patent_graph_question_v2
from server.patent.graph_kb.canonicalizer import canonicalize_patent_graph_rows
from server.patent.graph_kb.client import execute_patent_graph_plan, plan_patent_graph_query
from server.patent.graph_kb.direct_renderer import render_patent_direct_answer
from server.patent.graph_kb.executor_v2 import execute_patent_prepared_query
from server.patent.graph_kb.metadata import build_patent_graph_route_metadata
from server.patent.graph_kb.models import PatentGraphKbExecutionResult, PatentGraphRoutingResult
from server.patent.graph_kb.planner_v2 import build_patent_graph_query_plan_v2
from server.patent.graph_kb.rag_adapter import build_patent_graph_rag_payload
from server.patent.graph_kb.rendering import render_patent_graph_answer
from server.patent.graph_kb.schema_registry import build_default_patent_schema_registry
from server.patent.graph_kb.slots import extract_patent_graph_slots


_LOGGER = logging.getLogger("patent.graph_kb")


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
    trace_id: str = "",
) -> PatentGraphRoutingResult:
    _ = generation_runtime
    started = time.perf_counter()
    normalized_trace_id = str(trace_id or "").strip()
    _LOGGER.info(
        "patent_graph.route_start trace=%s question_chars=%s max_rows=%s timeout_ms=%s",
        normalized_trace_id,
        len(str(question or "")),
        int(max_rows or 0),
        int(timeout_ms or 0),
    )
    slots = extract_patent_graph_slots(question)
    _LOGGER.info(
        "patent_graph.slots_done trace=%s patent_ids=%s ipc_prefixes=%s ipc_code_prefixes=%s ipc_full_codes=%s",
        normalized_trace_id,
        len(slots.patent_ids),
        len(slots.ipc_prefixes),
        len(slots.ipc_code_prefixes),
        len(slots.ipc_full_codes),
    )
    decision = classify_patent_graph_question_v2(
        question=question,
        conversation_context=conversation_context or {},
    )
    _LOGGER.info(
        "patent_graph.classify_done trace=%s mode=%s route_family=%s matched_rule=%s",
        normalized_trace_id,
        decision.mode,
        decision.route_family,
        str(dict(decision.diagnostics or {}).get("matched_rule") or ""),
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
    _LOGGER.info(
        "patent_graph.plan_done trace=%s strategy=%s intent=%s",
        normalized_trace_id,
        plan.strategy if plan is not None else "",
        plan.intent if plan is not None else "",
    )

    if decision.mode == "skip_graph" or plan is None:
        _LOGGER.info(
            "patent_graph.route_end trace=%s final_mode=skip_graph reason=%s",
            normalized_trace_id,
            str(diagnostics.get("matched_rule") or "no_plan"),
        )
        return PatentGraphRoutingResult(mode="skip_graph", diagnostics=diagnostics)

    execution = execute_patent_prepared_query(
        plan=plan,
        neo4j_client=neo4j_client,
        max_rows=max_rows,
        timeout_ms=timeout_ms,
        max_path_attempts=2,
    )
    _LOGGER.info(
        "patent_graph.execute_done trace=%s matched_path=%s attempted_paths=%s row_count=%s guardrail=%s fallback=%s",
        normalized_trace_id,
        execution.trace.matched_path,
        list(execution.trace.attempted_paths or ()),
        len(tuple(execution.rows or ())),
        execution.trace.guardrail_verdict,
        execution.trace.fallback_reason,
    )
    diagnostics["matched_path"] = execution.trace.matched_path
    diagnostics["attempted_paths"] = execution.trace.attempted_paths
    diagnostics["guardrail_verdict"] = execution.trace.guardrail_verdict
    diagnostics["neo4j_client"] = execution.trace.neo4j_client
    if execution.trace.fallback_reason:
        diagnostics["fallback_reason"] = execution.trace.fallback_reason

    bundle = canonicalize_patent_graph_rows(plan=plan, rows=execution.rows, matched_path=execution.trace.matched_path)
    _LOGGER.info(
        "patent_graph.canonicalize_done trace=%s candidate_count=%s fact_count=%s direct_answerable=%s",
        normalized_trace_id,
        len(tuple(bundle.patent_candidates or ())),
        len(tuple(bundle.facts or ())),
        bool(bundle.direct_answerable),
    )
    diagnostics.update(dict(bundle.diagnostics or {}))
    rag_payload = build_patent_graph_rag_payload(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )
    _LOGGER.info(
        "patent_graph.rag_payload_done trace=%s candidate_count=%s has_stage1=%s has_stage4=%s",
        normalized_trace_id,
        len(tuple(rag_payload.stage2_patent_candidates or ())),
        bool(rag_payload.stage1_context_block),
        bool(rag_payload.stage4_fact_block),
    )

    if decision.mode == "direct_answer":
        direct = render_patent_direct_answer(
            decision=decision,
            plan=plan,
            bundle=bundle,
        )
        _LOGGER.info(
            "patent_graph.direct_render_done trace=%s handled=%s reason=%s",
            normalized_trace_id,
            bool(direct.handled),
            str(dict(direct.metadata or {}).get("reason") or ""),
        )
        if direct.handled:
            template_id = str(plan.legacy_template_id or bundle.render_slots.get("path_id") or "")
            direct_metadata = dict(direct.metadata or {})
            direct_metadata.update(
                build_patent_graph_route_metadata(
                    attempted=True,
                    mode="direct_answer",
                    route_family=decision.route_family,
                    strategy=plan.strategy,
                    template_id=template_id,
                    path_id=str(bundle.render_slots.get("path_id") or ""),
                    fingerprint=str(rag_payload.cache_fingerprint or "none"),
                    row_count=len(tuple(bundle.render_slots.get("rows") or ())),
                    evidence_quality=dict(bundle.diagnostics.get("evidence_quality") or {}),
                )
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            _LOGGER.info(
                "patent_graph.route_end trace=%s final_mode=direct_answer template_id=%s",
                normalized_trace_id,
                template_id,
            )
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
        _LOGGER.info(
            "patent_graph.route_end trace=%s final_mode=%s direct_fallback_reason=%s",
            normalized_trace_id,
            "graph_for_rag" if has_rag_signal else "skip_graph",
            diagnostics["direct_fallback_reason"],
        )
        return PatentGraphRoutingResult(
            mode="graph_for_rag" if has_rag_signal else "skip_graph",
            rag_payload=rag_payload if has_rag_signal else None,
            diagnostics=diagnostics,
        )

    _LOGGER.info(
        "patent_graph.route_end trace=%s final_mode=%s",
        normalized_trace_id,
        decision.mode,
    )
    return PatentGraphRoutingResult(
        mode=decision.mode,
        rag_payload=rag_payload,
        diagnostics=diagnostics,
    )
