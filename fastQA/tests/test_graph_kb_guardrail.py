from __future__ import annotations

from app.modules.graph_kb.guardrail import inspect_cypher
from app.modules.graph_kb.query_templates import build_v1_query_paths
from app.modules.graph_kb.schema_registry import build_default_schema_registry


def test_guardrail_rejects_write_cypher():
    result = inspect_cypher(cypher="MATCH (n) DELETE n", registry=build_default_schema_registry())

    assert result.verdict == "reject"
    assert "write_clause" in result.issues


def test_guardrail_rejects_disallowed_label():
    result = inspect_cypher(
        cypher="MATCH (d:forbidden) RETURN d",
        registry=build_default_schema_registry(),
    )

    assert result.verdict == "reject"
    assert "label_not_allowed" in result.issues


def test_guardrail_keeps_parameterized_limit_without_appending_second_limit():
    result = inspect_cypher(
        cypher="MATCH (d:doi) RETURN d.name AS doi LIMIT $limit",
        registry=build_default_schema_registry(),
    )

    assert result.verdict == "allow"
    assert result.normalized_cypher.endswith("LIMIT $limit")
    assert "LIMIT $limit LIMIT 20" not in result.normalized_cypher


def test_guardrail_accepts_explicit_carbon_source_query():
    registry = build_default_schema_registry()
    cypher = (
        "MATCH (d:doi)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source) "
        "WHERE toLower(cs.name) CONTAINS toLower($term) "
        "RETURN d.name AS doi, cs.name AS carbon_source LIMIT 20"
    )

    result = inspect_cypher(cypher=cypher, registry=registry)

    assert result.allowed


def test_guardrail_rejects_unknown_dynamic_relationship_type():
    registry = build_default_schema_registry()
    cypher = (
        "MATCH (d:doi)-[:recipe]->(:recipe)-[r]->(v) "
        "WHERE type(r) IN ['carbon_source', 'evil_relation'] "
        "RETURN v.name AS value LIMIT 20"
    )

    result = inspect_cypher(cypher=cypher, registry=registry)

    assert not result.allowed


def test_all_v1_templates_pass_guardrail():
    registry = build_default_schema_registry()
    sample_template_cases = [
        ("lookup_by_doi", {"doi": "10.1021/jp1005692"}),
        ("expand_doi_context", {"doi": "10.1021/jp1005692"}),
        ("list_by_title_or_material", {"terms": ("lifepo4",)}),
        ("list_by_raw_material", {"raw_material_terms": ("LiFePO4",)}),
        ("list_by_carbon_source", {"carbon_source_terms": ("sucrose",)}),
        ("list_by_process_method", {"process_terms": ("milling",)}),
        ("count_by_structured_field", {"field": "recipe.carbon_source", "carbon_source_terms": ("sucrose",)}),
        ("numeric_property_query", {"property_field": "discharge_capacity", "title_terms": ("lifepo4",)}),
        ("community_find_by_term", {"terms": ("lifepo4",)}),
        ("community_representative_titles", {"community_id": 1}),
        ("community_representative_methods", {"community_id": 1}),
        ("community_profile", {"community_id": 1}),
    ]

    for intent, slots in sample_template_cases:
        for path in build_v1_query_paths(intent=intent, slots=slots, limit=20):
            assert inspect_cypher(cypher=path.cypher, registry=registry).allowed, path.path_id
