from __future__ import annotations

import logging

from server.patent.graph_kb.models import (
    PatentDirectAnswerResult,
    PatentExecutionTrace,
    PatentGraphEvidenceBundle,
    PatentGraphQueryPlanV2,
    PatentGraphRagPayload,
    PatentGraphSemanticDecision,
    PatentRawExecutionResult,
)
import server.patent.graph_kb.service as patent_graph_service
from server.patent.graph_kb.service import route_patent_graph_kb_v2


def test_route_v2_returns_skip_graph_result(monkeypatch):
    monkeypatch.setattr(
        patent_graph_service,
        "classify_patent_graph_question_v2",
        lambda **kwargs: PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            diagnostics={"matched_rule": "broad_semantic_question"},
        ),
    )

    result = route_patent_graph_kb_v2(
        question="为什么这种技术路线更有前景？",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
    )

    assert result.mode == "skip_graph"
    assert result.direct_result is None
    assert result.rag_payload is None
    assert result.diagnostics["tri_state_mode"] == "skip_graph"


def test_route_v2_returns_direct_answer_result(monkeypatch):
    plan = PatentGraphQueryPlanV2(
        strategy="template",
        intent="lookup_patent_by_id",
        legacy_template_id="lookup_patent_by_id",
    )
    monkeypatch.setattr(
        patent_graph_service,
        "classify_patent_graph_question_v2",
        lambda **kwargs: PatentGraphSemanticDecision(mode="direct_answer", route_family="precise"),
    )
    monkeypatch.setattr(patent_graph_service, "build_patent_graph_query_plan_v2", lambda **kwargs: plan)
    monkeypatch.setattr(
        patent_graph_service,
        "execute_patent_prepared_query",
        lambda **kwargs: PatentRawExecutionResult(
            rows=({"patent_id": "CN100355122C"},),
            trace=PatentExecutionTrace(strategy="template", matched_path="lookup_patent_by_id", attempted_paths=("lookup_patent_by_id",), guardrail_verdict="trusted_template"),
        ),
    )
    monkeypatch.setattr(
        patent_graph_service,
        "canonicalize_patent_graph_rows",
        lambda **kwargs: PatentGraphEvidenceBundle(
            patent_candidates=("CN100355122C",),
            direct_answerable=True,
            render_slots={"rows": ({"patent_id": "CN100355122C"},)},
        ),
    )
    monkeypatch.setattr(
        patent_graph_service,
        "render_patent_direct_answer",
        lambda **kwargs: PatentDirectAnswerResult(
            handled=True,
            answer="direct graph answer",
            references=("CN100355122C",),
            reference_objects=(
                {
                    "canonical_patent_id": "CN100355122C",
                    "patent_id": "CN100355122C",
                    "title": "示例专利",
                    "source": "patent_graph",
                },
            ),
            metadata={"template_id": "lookup_patent_by_id"},
        ),
    )
    monkeypatch.setattr(
        patent_graph_service,
        "build_patent_graph_rag_payload",
        lambda **kwargs: PatentGraphRagPayload(cache_fingerprint="graph:test"),
    )
    perf_counter_values = iter((10.0, 10.25))
    monkeypatch.setattr(patent_graph_service.time, "perf_counter", lambda: next(perf_counter_values))

    result = route_patent_graph_kb_v2(
        question="CN100355122C 这件专利是什么？",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
    )

    assert result.mode == "direct_answer"
    assert result.direct_result is not None
    assert result.direct_result.handled is True
    assert result.direct_result.answer == "direct graph answer"
    assert result.direct_result.references == ("CN100355122C",)
    assert result.direct_result.latency_ms == 250.0


def test_route_v2_returns_graph_for_rag_payload(monkeypatch):
    plan = PatentGraphQueryPlanV2(strategy="parametric", intent="multi_patent_compare")
    payload = PatentGraphRagPayload(
        stage1_context_block="graph block",
        stage2_patent_candidates=("CN100355122C", "CN100371239C"),
        stage4_fact_block="- fact",
        stage4_graph_candidate_patent_ids=("CN100355122C", "CN100371239C"),
        cache_fingerprint="graph:test",
    )
    monkeypatch.setattr(
        patent_graph_service,
        "classify_patent_graph_question_v2",
        lambda **kwargs: PatentGraphSemanticDecision(mode="graph_for_rag", route_family="hybrid"),
    )
    monkeypatch.setattr(patent_graph_service, "build_patent_graph_query_plan_v2", lambda **kwargs: plan)
    monkeypatch.setattr(
        patent_graph_service,
        "execute_patent_prepared_query",
        lambda **kwargs: PatentRawExecutionResult(
            rows=({"patent_id": "CN100355122C"},),
            trace=PatentExecutionTrace(strategy="parametric", matched_path="compare_patents_process_steps", attempted_paths=("compare_patents_process_steps",), guardrail_verdict="allow"),
        ),
    )
    monkeypatch.setattr(
        patent_graph_service,
        "canonicalize_patent_graph_rows",
        lambda **kwargs: PatentGraphEvidenceBundle(
            patent_candidates=("CN100355122C", "CN100371239C"),
            facts=("fact",),
            direct_answerable=False,
        ),
    )
    monkeypatch.setattr(patent_graph_service, "build_patent_graph_rag_payload", lambda **kwargs: payload)

    result = route_patent_graph_kb_v2(
        question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
    )

    assert result.mode == "graph_for_rag"
    assert result.direct_result is None
    assert result.rag_payload == payload
    assert result.diagnostics["strategy"] == "parametric"


def test_route_v2_downgrades_failed_direct_render_to_graph_for_rag(monkeypatch):
    plan = PatentGraphQueryPlanV2(strategy="template", intent="lookup_patent_by_id", legacy_template_id="lookup_patent_by_id")
    payload = PatentGraphRagPayload(stage1_context_block="graph block", cache_fingerprint="graph:test")
    monkeypatch.setattr(
        patent_graph_service,
        "classify_patent_graph_question_v2",
        lambda **kwargs: PatentGraphSemanticDecision(mode="direct_answer", route_family="precise"),
    )
    monkeypatch.setattr(patent_graph_service, "build_patent_graph_query_plan_v2", lambda **kwargs: plan)
    monkeypatch.setattr(
        patent_graph_service,
        "execute_patent_prepared_query",
        lambda **kwargs: PatentRawExecutionResult(
            rows=({"patent_id": "CN100355122C"},),
            trace=PatentExecutionTrace(strategy="template", matched_path="lookup_patent_by_id", attempted_paths=("lookup_patent_by_id",), guardrail_verdict="trusted_template"),
        ),
    )
    monkeypatch.setattr(
        patent_graph_service,
        "canonicalize_patent_graph_rows",
        lambda **kwargs: PatentGraphEvidenceBundle(
            patent_candidates=("CN100355122C",),
            facts=("fact",),
            direct_answerable=True,
            render_slots={"rows": ({"patent_id": "CN100355122C"},)},
        ),
    )
    monkeypatch.setattr(
        patent_graph_service,
        "render_patent_direct_answer",
        lambda **kwargs: PatentDirectAnswerResult(handled=False, metadata={"reason": "render_empty"}),
    )
    monkeypatch.setattr(patent_graph_service, "build_patent_graph_rag_payload", lambda **kwargs: payload)

    result = route_patent_graph_kb_v2(
        question="CN100355122C 这件专利是什么？",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
    )

    assert result.mode == "graph_for_rag"
    assert result.direct_result is None
    assert result.rag_payload == payload
    assert result.diagnostics["direct_fallback_reason"] == "render_empty"


def test_route_v2_logs_graph_pipeline_steps(monkeypatch, caplog):
    plan = PatentGraphQueryPlanV2(strategy="parametric", intent="list_patents_by_inventor")
    payload = PatentGraphRagPayload(stage1_context_block="graph block", cache_fingerprint="graph:test")
    monkeypatch.setattr(
        patent_graph_service,
        "classify_patent_graph_question_v2",
        lambda **kwargs: PatentGraphSemanticDecision(mode="graph_for_rag", route_family="hybrid", diagnostics={"matched_rule": "inventor"}),
    )
    monkeypatch.setattr(patent_graph_service, "build_patent_graph_query_plan_v2", lambda **kwargs: plan)
    monkeypatch.setattr(
        patent_graph_service,
        "execute_patent_prepared_query",
        lambda **kwargs: PatentRawExecutionResult(
            rows=({"patent_id": "CN100355122C"},),
            trace=PatentExecutionTrace(strategy="parametric", matched_path="list_patents_by_inventor", attempted_paths=("list_patents_by_inventor",), guardrail_verdict="allow"),
        ),
    )
    monkeypatch.setattr(
        patent_graph_service,
        "canonicalize_patent_graph_rows",
        lambda **kwargs: PatentGraphEvidenceBundle(
            patent_candidates=("CN100355122C",),
            facts=("fact",),
            diagnostics={"row_count": 1, "evidence_quality": {"has_rows": True}},
        ),
    )
    monkeypatch.setattr(patent_graph_service, "build_patent_graph_rag_payload", lambda **kwargs: payload)

    with caplog.at_level(logging.INFO, logger="patent.graph_kb"):
        route_patent_graph_kb_v2(
            question="发明人张三有哪些专利？",
            conversation_context={},
            neo4j_client=object(),
            max_rows=10,
            trace_id="trace-1",
        )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "patent_graph.route_start" in log_text
    assert "patent_graph.classify_done" in log_text
    assert "patent_graph.plan_done" in log_text
    assert "patent_graph.execute_done" in log_text
    assert "patent_graph.canonicalize_done" in log_text
    assert "patent_graph.rag_payload_done" in log_text
    assert "patent_graph.route_end" in log_text
