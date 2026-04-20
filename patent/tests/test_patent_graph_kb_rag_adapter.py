from __future__ import annotations

from server.patent.graph_kb.models import (
    PatentGraphConstraint,
    PatentGraphEvidenceBundle,
    PatentGraphQueryPlanV2,
    PatentGraphSemanticDecision,
)
from server.patent.graph_kb.rag_adapter import build_patent_graph_rag_payload


def test_rag_adapter_builds_stable_payload_and_context_blocks():
    decision = PatentGraphSemanticDecision(
        mode="graph_for_rag",
        route_family="hybrid",
        diagnostics={"matched_rule": "multi_patent_compare"},
    )
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="multi_patent_compare",
        question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        diagnostics={"strategy": "parametric"},
    )
    bundle = PatentGraphEvidenceBundle(
        patent_candidates=("CN100355122C", "CN100371239C"),
        ipc_candidates=("H01M10/0525",),
        organization_candidates=("宁德时代新能源科技股份有限公司",),
        inventor_candidates=("张三",),
        facts=("patent_id=CN100355122C; step_name=配料混合", "patent_id=CN100371239C; step_name=前驱体合成"),
        constraints_for_rag=(PatentGraphConstraint(field="person.inventor", operator="eq", value="张三"),),
        diagnostics={"bundle_source": "canonicalizer"},
    )

    payload = build_patent_graph_rag_payload(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )
    repeated = build_patent_graph_rag_payload(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )

    assert payload.stage1_context_block == repeated.stage1_context_block
    assert payload.cache_fingerprint == repeated.cache_fingerprint
    assert "graph_mode: graph_for_rag" in payload.stage1_context_block
    assert "graph_route_family: hybrid" in payload.stage1_context_block
    assert "CN100355122C" in payload.stage1_context_block
    assert payload.stage2_patent_candidates == ("CN100355122C", "CN100371239C")
    assert payload.stage2_constraints[0].field == "person.inventor"
    assert payload.stage2_entity_hints["ipc_codes"] == ("H01M10/0525",)
    assert payload.stage2_entity_hints["organizations"] == ("宁德时代新能源科技股份有限公司",)
    assert payload.stage2_entity_hints["inventors"] == ("张三",)
    assert payload.stage4_fact_block.startswith("- patent_id=CN100355122C")
    assert payload.stage4_graph_candidate_patent_ids == ("CN100355122C", "CN100371239C")
    assert payload.diagnostics["bundle_source"] == "canonicalizer"


def test_rag_adapter_fingerprint_changes_with_material_payload_differences():
    decision = PatentGraphSemanticDecision(mode="graph_for_rag", route_family="hybrid")
    plan = PatentGraphQueryPlanV2(strategy="parametric", intent="inventor_listing")
    first = build_patent_graph_rag_payload(
        decision=decision,
        plan=plan,
        bundle=PatentGraphEvidenceBundle(
            patent_candidates=("CN100355122C",),
            facts=("fact_a",),
        ),
    )
    second = build_patent_graph_rag_payload(
        decision=decision,
        plan=plan,
        bundle=PatentGraphEvidenceBundle(
            patent_candidates=("CN100355122C",),
            facts=("fact_b",),
        ),
    )

    assert first.cache_fingerprint != second.cache_fingerprint
