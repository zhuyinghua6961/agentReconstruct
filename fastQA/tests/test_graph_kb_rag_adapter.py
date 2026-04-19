from __future__ import annotations

from app.modules.graph_kb.models import GraphEvidenceBundle, GraphQueryPlanV2, SemanticDecision
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
