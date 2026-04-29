from __future__ import annotations

from app.modules.graph_kb.canonicalizer import canonicalize_graph_rows
from app.modules.graph_kb.models import GraphKbQueryPlan, GraphQueryPlanV2


def test_canonicalizer_extracts_fact_rows_into_graph_evidence_bundle():
    bundle = canonicalize_graph_rows(
        plan=GraphQueryPlanV2(
            strategy="template",
            legacy_template_id="lookup_by_doi",
            legacy_template_plan=GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": "10.1000/test"}),
        ),
        rows=[{"doi": "10.1000/test", "title": "Test Paper", "raw_materials": ["LFP powder"]}],
    )

    assert bundle.doi_candidates == ("10.1000/test",)
    assert bundle.facts
    assert bundle.direct_answerable is True


def test_canonicalizer_filters_suspicious_dois_for_direct_rendering():
    plan = GraphQueryPlanV2(strategy="route_template", intent="list_by_carbon_source")
    rows = [
        {"doi": "10.1021/jp1005692", "title": "Valid", "carbon_source": "sucrose"},
        {"doi": "10.1007/s12598-", "title": "Suspicious", "carbon_source": "sucrose"},
    ]

    bundle = canonicalize_graph_rows(plan=plan, rows=rows)

    assert "10.1021/jp1005692" in bundle.doi_candidates
    assert "10.1007/s12598-" not in bundle.direct_render_dois
    assert bundle.diagnostics["suspicious_doi_count"] == 1


def test_canonicalizer_preserves_original_capacity_text_and_parse_result():
    plan = GraphQueryPlanV2(strategy="route_template", intent="numeric_property_query")
    rows = [{"doi": "10.1/test", "capacity": "0.5C_initial_141.2 mA h g⁻¹"}]

    bundle = canonicalize_graph_rows(plan=plan, rows=rows)

    assert "141.2" in bundle.facts[0]
    assert bundle.render_slots["rows"][0]["original_value"] == "0.5C_initial_141.2 mA h g⁻¹"


def test_canonicalizer_maps_count_row_to_render_slots():
    plan = GraphQueryPlanV2(strategy="route_template", intent="count_by_structured_field")
    rows = [{"count": 69, "field_label": "carbon_source", "term": "sucrose"}]

    bundle = canonicalize_graph_rows(plan=plan, rows=rows)

    assert bundle.render_slots["count"] == 69
    assert bundle.render_slots["field_label"] == "carbon_source"
    assert bundle.render_slots["term"] == "sucrose"
    assert bundle.render_slots["direct_answerable"] is True


def test_canonicalizer_promotes_community_representative_dois_to_candidates():
    plan = GraphQueryPlanV2(strategy="route_template", intent="community_find_by_term")
    rows = [
        {
            "community_id": 585242,
            "dois": ("10.1039/c4ra15767b", "10.1007/s12598-"),
            "titles": ("High performance LiFePO4 cathode",),
            "materials": ("LiFePO4/C",),
            "preparation_methods": ("solvothermal synthesis",),
        }
    ]

    bundle = canonicalize_graph_rows(plan=plan, rows=rows)

    assert bundle.doi_candidates == ("10.1039/c4ra15767b",)
    assert bundle.direct_render_dois == ("10.1039/c4ra15767b",)
    assert bundle.diagnostics["suspicious_doi_count"] == 1


def test_canonicalizer_builds_rag_constraints_from_numeric_plan_slots():
    plan = GraphQueryPlanV2(
        strategy="multi_stage",
        intent="hybrid_property_analysis",
        parametric_slots={
            "slots": {
                "property_field": "discharge_capacity",
                "operator": ">",
                "threshold": 150,
            }
        },
    )

    bundle = canonicalize_graph_rows(
        plan=plan,
        rows=[{"doi": "10.1021/jp1005692", "title": "Valid", "value": "155 mAh/g"}],
    )

    assert any(
        constraint.field == "performance.discharge_capacity" and constraint.operator == ">" and constraint.value == 150
        for constraint in bundle.constraints_for_rag
    )
    assert bundle.entity_hints["titles"] == ("Valid",)


def test_canonicalizer_filters_hybrid_numeric_rows_before_promoting_dois():
    plan = GraphQueryPlanV2(
        strategy="multi_stage",
        intent="hybrid_property_analysis",
        parametric_slots={
            "slots": {
                "property_field": "discharge_capacity",
                "operator": ">",
                "threshold": 150,
            }
        },
    )

    bundle = canonicalize_graph_rows(
        plan=plan,
        rows=[
            {"doi": "10.1021/above", "title": "Above", "value": "155 mAh/g"},
            {"doi": "10.1021/below", "title": "Below", "value": "120 mAh/g"},
        ],
    )

    assert bundle.doi_candidates == ("10.1021/above",)
    assert "Above" in bundle.entity_hints["titles"]
    assert "Below" not in bundle.entity_hints["titles"]
    assert all("below" not in fact for fact in bundle.facts)


def test_canonicalizer_applies_top_ranking_limit_after_numeric_parse():
    plan = GraphQueryPlanV2(
        strategy="multi_stage",
        intent="hybrid_property_analysis",
        parametric_slots={
            "slots": {
                "property_field": "compaction_density",
                "ranking": "top",
                "limit": 2,
            }
        },
    )

    bundle = canonicalize_graph_rows(
        plan=plan,
        rows=[
            {"doi": "10.1021/mid", "title": "Middle", "value": "2.4 g/cm3"},
            {"doi": "10.1021/high", "title": "High", "value": "2.8 g/cm3"},
            {"doi": "10.1021/low", "title": "Low", "value": "2.1 g/cm3"},
        ],
    )

    assert bundle.doi_candidates == ("10.1021/high", "10.1021/mid")
    assert bundle.entity_hints["titles"] == ("High", "Middle")
