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


def test_process_method_template_is_bounded_and_direct_capable():
    paths = build_v1_query_paths(intent="list_by_process_method", slots={"process_terms": ("sintering",)}, limit=20)

    assert paths
    assert paths[0].path_id == "process.method"
    assert paths[0].direct_answer_eligible is True
    assert "$terms" in paths[0].cypher
    assert "$limit" in paths[0].cypher
    assert "preparation_methods" in paths[0].expected_columns


def test_process_method_template_filters_by_material_target_when_present():
    paths = build_v1_query_paths(
        intent="list_by_process_method",
        slots={"process_terms": ("制备", "方法"), "material_terms": ("lifepo4",)},
        limit=20,
    )

    assert paths
    assert "target_terms" in paths[0].params
    assert paths[0].params["target_terms"] == ("lifepo4",)
    assert "sample_names" in paths[0].cypher
    assert "raw_materials" in paths[0].cypher


def test_hybrid_property_analysis_returns_candidate_and_expansion_paths():
    paths = build_v1_query_paths(
        intent="hybrid_property_analysis",
        slots={"property_field": "discharge_capacity", "title_terms": ("lifepo4",), "operator": ">", "threshold": 150},
        limit=20,
    )

    assert len(paths) >= 2
    assert paths[0].path_id == "hybrid.performance.discharge_capacity_candidates"
    assert paths[1].path_id.startswith("hybrid.expand.")
    assert "candidate_dois" in paths[1].params
    assert "$candidate_dois" in paths[1].cypher
    assert all("$limit" in path.cypher for path in paths)
    assert all(not path.direct_answer_eligible for path in paths)


def test_community_profile_template_is_available_and_bounded():
    paths = build_v1_query_paths(intent="community_profile", slots={"community_id": 7}, limit=20)

    assert paths
    assert paths[0].path_id == "community.profile"
    assert "$community_id" in paths[0].cypher
    assert "$limit" in paths[0].cypher
    assert "paper_count" in paths[0].expected_columns


def test_deferred_fields_do_not_build_numeric_templates():
    for field in ("energy_density", "power_density", "surface_area", "morphology"):
        paths = build_v1_query_paths(intent="numeric_property_query", slots={"property_field": field}, limit=20)

        assert paths == ()
