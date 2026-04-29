from __future__ import annotations

from app.modules.graph_kb.metadata import build_graph_route_metadata


def test_metadata_exposes_canonical_and_compatibility_route_keys():
    metadata = build_graph_route_metadata(
        route_family="community",
        tri_state_mode="graph_for_rag",
        strategy="community_representatives",
        intent="community_representatives",
        result_count=3,
        rag_injection_enabled=True,
    )

    assert metadata["knowledge_route_family"] == "community"
    assert metadata["legacy_route_family"] == "community"
    assert metadata["tri_state_mode"] == "graph_for_rag"
    assert metadata["graph_strategy"] == "community_representatives"
    assert metadata["graph_intent"] == "community_representatives"
    assert metadata["graph_result_count"] == 3
    assert metadata["graph_rag_injection_enabled"] is True


def test_metadata_exposes_graph_canonical_keys_with_compatibility_aliases():
    metadata = build_graph_route_metadata(
        route_family="hybrid",
        tri_state_mode="graph_for_rag",
        strategy="multi_stage",
        intent="hybrid_property_process",
        confidence=0.82,
        direct_answer_eligible=False,
        rag_injection_enabled=True,
        fallback_reason="",
    )

    assert metadata["graph_route_family"] == "hybrid"
    assert metadata["graph_execution_mode"] == "graph_for_rag"
    assert metadata["graph_strategy"] == "multi_stage"
    assert metadata["graph_intent"] == "hybrid_property_process"
    assert metadata["graph_rag_injected"] is True
    assert metadata["knowledge_route_family"] == "hybrid"
    assert metadata["legacy_route_family"] == "hybrid"
    assert metadata["tri_state_mode"] == "graph_for_rag"
    assert metadata["graph_rag_injection_enabled"] is True
    assert metadata["graph_confidence"] == 0.82
    assert metadata["graph_direct_answer_eligible"] is False
