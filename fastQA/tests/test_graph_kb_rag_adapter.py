from __future__ import annotations

from app.modules.graph_kb.models import GraphConstraint, GraphEvidenceBundle, GraphQueryPlanV2, SemanticDecision
from app.modules.graph_kb.rag_adapter import build_graph_rag_payload


def test_rag_adapter_builds_cache_fingerprint_and_fact_blocks():
    payload = build_graph_rag_payload(
        decision=SemanticDecision(mode="graph_for_rag", legacy_route="semantic"),
        plan=GraphQueryPlanV2(strategy="parametric", intent="legacy_precise_parametric"),
        bundle=GraphEvidenceBundle(
            doi_candidates=("10.1000/test",),
            facts=("structured fact 1", "structured fact 2"),
            render_slots={},
            direct_answerable=False,
        ),
    )

    assert payload.cache_fingerprint
    assert payload.stage2_doi_candidates == ("10.1000/test",)
    assert "structured fact" in payload.stage4_fact_block
    assert "Graph structured facts" in payload.stage4_fact_block


def test_rag_adapter_includes_route_specific_entity_hints():
    bundle = GraphEvidenceBundle(
        doi_candidates=("10.1021/jp1005692",),
        facts=("carbon_source=sucrose doi=10.1021/jp1005692",),
        render_slots={"rows": [{"carbon_source": "sucrose", "title": "A title"}]},
    )

    payload = build_graph_rag_payload(
        decision=SemanticDecision(mode="graph_for_rag", legacy_route="hybrid"),
        plan=GraphQueryPlanV2(strategy="route_template", intent="list_by_carbon_source"),
        bundle=bundle,
    )

    assert "sucrose" in payload.stage2_entity_hints["carbon_sources"]
    assert "carbon_source" in payload.stage4_fact_block


def test_rag_adapter_includes_constraints_and_route_context():
    payload = build_graph_rag_payload(
        decision=SemanticDecision(mode="graph_for_rag", legacy_route="hybrid", route_family="hybrid"),
        plan=GraphQueryPlanV2(strategy="multi_stage", intent="hybrid_property_analysis"),
        bundle=GraphEvidenceBundle(
            doi_candidates=("10.1021/jp1005692",),
            facts=("doi=10.1021/jp1005692; value=155 mAh/g",),
            constraints_for_rag=(GraphConstraint(field="performance.discharge_capacity", operator=">", value=150),),
            render_slots={"rows": [{"title": "Capacity paper", "sample_name": "LFP/C"}]},
        ),
    )

    assert "graph_route_family: hybrid" in payload.stage1_context_block
    assert "graph_execution_mode: graph_for_rag" in payload.stage1_context_block
    assert "performance.discharge_capacity > 150" in payload.stage1_context_block
    assert payload.stage2_constraints[0].field == "performance.discharge_capacity"
    assert "Capacity paper" in payload.stage2_entity_hints["titles"]
