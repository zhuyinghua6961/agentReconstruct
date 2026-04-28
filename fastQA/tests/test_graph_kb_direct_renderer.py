from __future__ import annotations

from app.modules.graph_kb.direct_renderer import render_direct_answer
from app.modules.graph_kb.models import GraphEvidenceBundle, GraphQueryPlanV2, SemanticDecision


def test_renders_carbon_source_list_direct_answer():
    decision = SemanticDecision(mode="direct_answer", legacy_route="precise")
    plan = GraphQueryPlanV2(strategy="route_template", intent="list_by_carbon_source")
    bundle = GraphEvidenceBundle(
        doi_candidates=("10.1021/jp1005692",),
        direct_render_dois=("10.1021/jp1005692",),
        render_slots={
            "rows": [
                {
                    "doi": "10.1021/jp1005692",
                    "title": "Example title",
                    "carbon_source": "sucrose",
                }
            ]
        },
    )

    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled
    assert "sucrose" in result.answer
    assert "10.1021/jp1005692" in result.answer


def test_numeric_without_parser_confidence_downgrades():
    decision = SemanticDecision(mode="direct_answer", legacy_route="precise")
    plan = GraphQueryPlanV2(strategy="route_template", intent="numeric_property_query")
    bundle = GraphEvidenceBundle(render_slots={"rows": [{"original_value": "unknown"}]})

    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert not result.handled
    assert result.metadata["reason"] == "direct_renderer_unavailable"


def test_renders_count_direct_answer():
    decision = SemanticDecision(mode="direct_answer", legacy_route="precise")
    plan = GraphQueryPlanV2(strategy="route_template", intent="count_by_structured_field")
    bundle = GraphEvidenceBundle(render_slots={"count": 69, "field_label": "carbon_source", "term": "sucrose"})

    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled
    assert "69" in result.answer


def test_community_direct_answer_uses_label_not_raw_id():
    decision = SemanticDecision(mode="direct_answer", legacy_route="community")
    plan = GraphQueryPlanV2(strategy="route_template", intent="community_representatives")
    bundle = GraphEvidenceBundle(
        render_slots={
            "community_label": "LiFePO4 solvothermal synthesis cluster",
            "community_id": 585242,
            "rows": [{"doi": "10.1039/c4ra15767b", "title": "High performance LiFePO4 cathode"}],
        }
    )

    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled
    assert "LiFePO4" in result.answer
    assert "585242" not in result.answer


def test_doi_lookup_with_suspicious_doi_does_not_render_directly():
    decision = SemanticDecision(mode="direct_answer", legacy_route="precise")
    plan = GraphQueryPlanV2(strategy="route_template", intent="lookup_by_doi", legacy_template_id="lookup_by_doi")
    bundle = GraphEvidenceBundle(
        doi_candidates=(),
        direct_render_dois=(),
        render_slots={"rows": [{"doi": "10.1007/s12598-", "title": "Broken DOI"}]},
    )

    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert not result.handled
    assert result.metadata["reason"] == "suspicious_doi"
