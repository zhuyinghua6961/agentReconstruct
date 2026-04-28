from __future__ import annotations

from app.modules.graph_kb.models import (
    GraphConstraint,
    GraphExecutionMode,
    GraphRagPayload,
    GraphRouteFamily,
    GraphRoutingResult,
)


def test_graph_route_family_values_are_stable():
    assert GraphRouteFamily.PRECISE.value == "precise"
    assert GraphRouteFamily.SEMANTIC.value == "semantic"
    assert GraphRouteFamily.HYBRID.value == "hybrid"
    assert GraphRouteFamily.COMMUNITY.value == "community"


def test_graph_execution_mode_values_are_stable():
    assert GraphExecutionMode.DIRECT_ANSWER.value == "direct_answer"
    assert GraphExecutionMode.GRAPH_FOR_RAG.value == "graph_for_rag"
    assert GraphExecutionMode.SKIP_GRAPH.value == "skip_graph"


def test_graph_rag_payload_has_stable_cache_fingerprint():
    payload = GraphRagPayload(
        stage1_context_block="doi:10.1000/test",
        stage2_doi_candidates=["10.1000/test"],
        stage2_constraints=[GraphConstraint(field="paper.doi", operator="eq", value="10.1000/test")],
        stage2_entity_hints={"materials": ("LFP",)},
        stage4_fact_block="fact block",
        cache_fingerprint="abc123",
    )

    assert payload.cache_fingerprint == "abc123"
    assert payload.stage2_doi_candidates == ("10.1000/test",)


def test_graph_routing_result_keeps_direct_and_rag_slots_separate():
    payload = GraphRagPayload(cache_fingerprint="abc123")
    result = GraphRoutingResult(
        mode="graph_for_rag",
        direct_result=None,
        rag_payload=payload,
        diagnostics={"legacy_route": "semantic"},
    )

    assert result.mode == "graph_for_rag"
    assert result.direct_result is None
    assert result.rag_payload is payload
    assert result.diagnostics["legacy_route"] == "semantic"
