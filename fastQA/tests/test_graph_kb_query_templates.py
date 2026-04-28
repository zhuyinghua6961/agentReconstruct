from __future__ import annotations

from app.modules.graph_kb.query_templates import build_v1_query_paths


def test_carbon_source_template_uses_explicit_path():
    paths = build_v1_query_paths(intent="list_by_carbon_source", slots={"carbon_source_terms": ("sucrose",)}, limit=20)

    assert paths
    cypher = paths[0].cypher
    assert "[:recipe]" in cypher
    assert "[:carbon_source]" in cypher
    assert "type(r)" not in cypher


def test_capacity_template_uses_two_hop_child_path():
    paths = build_v1_query_paths(
        intent="numeric_property_query",
        slots={"property_field": "discharge_capacity", "title_terms": ("lifepo4",)},
        limit=20,
    )

    cypher = " ".join(path.cypher for path in paths)
    assert "[:discharge_capacity]->" in cypher
    assert "(d:doi)-[:name]->(s:name)" in cypher
    assert "(s)-[:name]->(d:doi)" not in cypher


def test_count_template_uses_structured_field_path():
    paths = build_v1_query_paths(
        intent="count_by_structured_field",
        slots={"field": "recipe.carbon_source", "carbon_source_terms": ("sucrose",)},
        limit=20,
    )

    assert paths
    cypher = paths[0].cypher
    assert "count(DISTINCT d)" in cypher
    assert "[:carbon_source]" in cypher


def test_raw_material_count_template_is_available_for_legacy_count_questions():
    paths = build_v1_query_paths(
        intent="count_by_structured_field",
        slots={"field": "raw_material.name", "terms": ("lfp",)},
        limit=20,
    )

    assert paths
    cypher = paths[0].cypher
    assert "[:raw_materials]" in cypher
    assert "count(DISTINCT d)" in cypher


def test_material_template_uses_doi_to_name_direction():
    paths = build_v1_query_paths(intent="list_by_title_or_material", slots={"terms": ("lfp",)}, limit=20)

    cypher = paths[0].cypher
    assert "(d)-[:name]->(s:name)" in cypher
    assert "(s:name)-[:name]->(d)" not in cypher


def test_community_template_returns_representative_evidence():
    paths = build_v1_query_paths(intent="community_find_by_term", slots={"terms": ("lifepo4",)}, limit=20)

    cypher = paths[0].cypher
    assert "titles" in cypher
    assert "materials" in cypher
    assert "preparation_methods" in cypher


def test_doi_expansion_template_is_distinct_from_lookup():
    lookup = build_v1_query_paths(intent="lookup_by_doi", slots={"doi": "10.1021/jp1005692"}, limit=20)
    expansion = build_v1_query_paths(intent="expand_doi_context", slots={"doi": "10.1021/jp1005692"}, limit=20)

    assert lookup[0].path_id != expansion[0].path_id
    assert "title" in lookup[0].cypher
    assert "bucket" in expansion[0].cypher or "value" in expansion[0].cypher
