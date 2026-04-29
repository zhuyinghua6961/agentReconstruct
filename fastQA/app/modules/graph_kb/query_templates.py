from __future__ import annotations

from typing import Any

from app.modules.graph_kb.models import GraphQueryPath


def _limit(value: int | None) -> int:
    try:
        parsed = int(value or 20)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(parsed, 100))


def _terms(slots: dict[str, Any], *keys: str) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        raw = slots.get(key)
        if isinstance(raw, str):
            values.append(raw)
        else:
            values.extend(str(item) for item in tuple(raw or ()))
    return tuple(item.strip().lower() for item in values if item and item.strip())


_GENERIC_PROCESS_TERMS = {"工艺", "制备", "方法", "preparation", "method", "process", "路线", "流程"}


def _specific_process_terms(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in values if item not in _GENERIC_PROCESS_TERMS)


def _path(path_id: str, cypher: str, params: dict[str, Any], columns: tuple[str, ...], *, direct: bool = False) -> GraphQueryPath:
    return GraphQueryPath(
        path_id=path_id,
        cypher=" ".join(str(cypher or "").split()),
        params=params,
        expected_columns=columns,
        direct_answer_eligible=direct,
    )


def _lookup_by_doi(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    return [
        _path(
            "doi.lookup",
            "MATCH (d:doi {name: $doi}) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "RETURN d.name AS doi, t.name AS title LIMIT $limit",
            {"doi": str(slots.get("doi") or ""), "limit": _limit(limit)},
            ("doi", "title"),
            direct=True,
        )
    ]


def _expand_doi_context(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    return [
        _path(
            "doi.context",
            "MATCH (d:doi {name: $doi}) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "OPTIONAL MATCH (d)-[:testing]->(:testing)-[:testing]->(tv:testing) "
            "OPTIONAL MATCH (d)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method) "
            "RETURN d.name AS doi, t.name AS title, 'context_bucket' AS bucket, "
            "collect(DISTINCT rm.name)[0..5] AS raw_materials, "
            "collect(DISTINCT tv.name)[0..5] AS testing_items, "
            "collect(DISTINCT pm.name)[0..5] AS value LIMIT $limit",
            {"doi": str(slots.get("doi") or ""), "limit": _limit(limit)},
            ("doi", "title", "bucket", "raw_materials", "testing_items", "value"),
            direct=True,
        )
    ]


def _list_by_title_or_material(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    terms = _terms(slots, "terms", "title_terms", "material_terms", "entities")
    return [
        _path(
            "paper.title_or_material",
            "MATCH (d:doi) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "OPTIONAL MATCH (d)-[:name]->(s:name) "
            "WITH d, t, collect(DISTINCT rm.name) AS raw_materials, collect(DISTINCT s.name) AS sample_names "
            "WHERE any(term IN $terms WHERE toLower(d.name) CONTAINS term "
            "OR toLower(coalesce(t.name, '')) CONTAINS term "
            "OR any(item IN raw_materials WHERE toLower(coalesce(item, '')) CONTAINS term) "
            "OR any(item IN sample_names WHERE toLower(coalesce(item, '')) CONTAINS term)) "
            "RETURN d.name AS doi, t.name AS title, raw_materials, sample_names LIMIT $limit",
            {"terms": terms, "limit": _limit(limit)},
            ("doi", "title", "raw_materials", "sample_names"),
            direct=True,
        )
    ]


def _list_by_raw_material(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    terms = _terms(slots, "raw_material_terms", "terms")
    return [
        _path(
            "raw_material.name",
            "MATCH (d:doi)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "WHERE any(term IN $terms WHERE toLower(coalesce(rm.name, '')) CONTAINS term) "
            "RETURN d.name AS doi, t.name AS title, collect(DISTINCT rm.name)[0..3] AS matched_raw_materials LIMIT $limit",
            {"terms": terms, "limit": _limit(limit)},
            ("doi", "title", "matched_raw_materials"),
            direct=True,
        )
    ]


def _list_by_carbon_source(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    terms = _terms(slots, "carbon_source_terms", "terms")
    return [
        _path(
            "recipe.carbon_source",
            "MATCH (d:doi)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "WHERE any(term IN $terms WHERE toLower(coalesce(cs.name, '')) CONTAINS term) "
            "RETURN d.name AS doi, t.name AS title, collect(DISTINCT cs.name)[0..3] AS carbon_sources LIMIT $limit",
            {"terms": terms, "limit": _limit(limit)},
            ("doi", "title", "carbon_sources"),
            direct=True,
        )
    ]


def _list_by_process_method(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    raw_terms = _terms(slots, "process_terms", "terms")
    target_terms = _terms(slots, "material_terms", "title_terms", "entities")
    terms = _specific_process_terms(raw_terms)
    if not target_terms and not terms:
        terms = raw_terms
    return [
        _path(
            "process.method",
            "MATCH (d:doi)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:name]->(s:name) "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "WITH d, t, pm, collect(DISTINCT s.name) AS sample_names, collect(DISTINCT rm.name) AS raw_materials "
            "WHERE (size($target_terms) = 0 OR any(term IN $target_terms WHERE toLower(d.name) CONTAINS term "
            "OR toLower(coalesce(t.name, '')) CONTAINS term "
            "OR any(item IN sample_names WHERE toLower(coalesce(item, '')) CONTAINS term) "
            "OR any(item IN raw_materials WHERE toLower(coalesce(item, '')) CONTAINS term))) "
            "AND (size($terms) = 0 OR any(term IN $terms WHERE toLower(coalesce(pm.name, '')) CONTAINS term)) "
            "RETURN d.name AS doi, t.name AS title, collect(DISTINCT pm.name)[0..5] AS preparation_methods LIMIT $limit",
            {"terms": terms, "target_terms": target_terms, "limit": _limit(limit)},
            ("doi", "title", "preparation_methods"),
            direct=True,
        )
    ]


def _count_by_structured_field(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    field = str(slots.get("field") or "").strip()
    if field == "recipe.carbon_source":
        terms = _terms(slots, "carbon_source_terms", "terms")
        return [
            _path(
                "recipe.carbon_source.count",
                "MATCH (d:doi)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source) "
                "WHERE any(term IN $terms WHERE toLower(coalesce(cs.name, '')) CONTAINS term) "
                "RETURN count(DISTINCT d) AS count LIMIT $limit",
                {"terms": terms, "limit": _limit(limit)},
                ("count",),
                direct=True,
            )
        ]
    if field == "raw_material.name":
        terms = _terms(slots, "raw_material_terms", "terms")
        return [
            _path(
                "raw_material.name.count",
                "MATCH (d:doi)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
                "WHERE any(term IN $terms WHERE toLower(coalesce(rm.name, '')) CONTAINS term) "
                "RETURN count(DISTINCT d) AS count, 'raw_material' AS field_label, head($terms) AS term LIMIT $limit",
                {"terms": terms, "limit": _limit(limit)},
                ("count", "field_label", "term"),
                direct=True,
            )
        ]
    return []


def _numeric_property_query(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    field = str(slots.get("property_field") or "").strip()
    terms = _terms(slots, "title_terms", "material_terms", "entities")
    params = {"terms": terms, "query_terms": terms, "limit": _limit(limit)}
    if field == "discharge_capacity":
        return [
            _path(
                "performance.discharge_capacity_child",
                "MATCH (d:doi)-[:name]->(s:name)-[:discharge_capacity]->(:discharge_capacity)-[:discharge_capacity]->(dc:discharge_capacity) "
                "OPTIONAL MATCH (d)-[:title]->(t:title) "
                "WHERE size($terms) = 0 OR any(term IN $terms WHERE toLower(coalesce(s.name, '')) CONTAINS term OR toLower(coalesce(t.name, '')) CONTAINS term) "
                "RETURN d.name AS doi, t.name AS title, s.name AS sample_name, dc.name AS value LIMIT $limit",
                params,
                ("doi", "title", "sample_name", "value"),
            )
        ]
    relation = {
        "compaction_density": "compaction_density",
        "tap_density": "tap_density",
        "cycling_stability": "cycling_stability",
        "conductivity": "conductivity",
    }.get(field)
    if relation:
        return [
            _path(
                f"performance.{relation}",
                f"MATCH (d:doi)-[:name]->(s:name)-[:{relation}]->(v:{relation}) "
                "OPTIONAL MATCH (d)-[:title]->(t:title) "
                "WHERE size($terms) = 0 OR any(term IN $terms WHERE toLower(coalesce(s.name, '')) CONTAINS term OR toLower(coalesce(t.name, '')) CONTAINS term) "
                "RETURN d.name AS doi, t.name AS title, s.name AS sample_name, v.name AS value LIMIT $limit",
                params,
                ("doi", "title", "sample_name", "value"),
            )
        ]
    return []


def _hybrid_property_analysis(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    field = str(slots.get("property_field") or "").strip()
    terms = _terms(slots, "title_terms", "material_terms", "entities")
    params = {
        "terms": terms,
        "operator": str(slots.get("operator") or ""),
        "threshold": slots.get("threshold"),
        "candidate_dois": (),
        "limit": _limit(limit),
    }
    if field == "discharge_capacity":
        return [
            _path(
                "hybrid.performance.discharge_capacity_candidates",
                "MATCH (d:doi)-[:name]->(s:name)-[:discharge_capacity]->(:discharge_capacity)-[:discharge_capacity]->(dc:discharge_capacity) "
                "OPTIONAL MATCH (d)-[:title]->(t:title) "
                "WHERE size($terms) = 0 OR any(term IN $terms WHERE toLower(coalesce(s.name, '')) CONTAINS term OR toLower(coalesce(t.name, '')) CONTAINS term) "
                "RETURN d.name AS doi, t.name AS title, s.name AS sample_name, dc.name AS value LIMIT $limit",
                params,
                ("doi", "title", "sample_name", "value"),
            ),
            _path(
                "hybrid.expand.process_recipe_by_doi",
                "MATCH (d:doi) "
                "WHERE size($candidate_dois) > 0 AND d.name IN $candidate_dois "
                "OPTIONAL MATCH (d)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method) "
                "OPTIONAL MATCH (d)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source) "
                "RETURN d.name AS doi, collect(DISTINCT pm.name)[0..5] AS preparation_methods, collect(DISTINCT cs.name)[0..5] AS carbon_sources LIMIT $limit",
                params,
                ("doi", "preparation_methods", "carbon_sources"),
            ),
        ]
    if field == "compaction_density":
        return [
            _path(
                "hybrid.performance.compaction_density_candidates",
                "MATCH (d:doi)-[:name]->(s:name)-[:compaction_density]->(v:compaction_density) "
                "OPTIONAL MATCH (d)-[:title]->(t:title) "
                "WHERE size($terms) = 0 OR any(term IN $terms WHERE toLower(coalesce(s.name, '')) CONTAINS term OR toLower(coalesce(t.name, '')) CONTAINS term) "
                "RETURN d.name AS doi, t.name AS title, s.name AS sample_name, v.name AS value LIMIT $limit",
                params,
                ("doi", "title", "sample_name", "value"),
            ),
            _path(
                "hybrid.expand.recipe_by_doi",
                "MATCH (d:doi) "
                "WHERE size($candidate_dois) > 0 AND d.name IN $candidate_dois "
                "OPTIONAL MATCH (d)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source) "
                "RETURN d.name AS doi, collect(DISTINCT cs.name)[0..5] AS carbon_sources LIMIT $limit",
                params,
                ("doi", "carbon_sources"),
            ),
        ]
    return []


def _community_find_by_term(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    terms = _terms(slots, "terms", "entities")
    return [
        _path(
            "community.find_by_term",
            "MATCH (seed) "
            "WHERE seed.louvainCommunityId IS NOT NULL AND any(term IN $terms WHERE toLower(coalesce(seed.name, '')) CONTAINS term) "
            "WITH seed.louvainCommunityId AS community_id, collect(DISTINCT seed.name)[0..10] AS representative_terms "
            "MATCH (d:doi) "
            "WHERE d.louvainCommunityId = community_id "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:name]->(m:name) "
            "OPTIONAL MATCH (d)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method) "
            "RETURN community_id, representative_terms, collect(DISTINCT d.name)[0..10] AS dois, "
            "collect(DISTINCT t.name)[0..10] AS titles, collect(DISTINCT m.name)[0..10] AS materials, "
            "collect(DISTINCT pm.name)[0..10] AS preparation_methods LIMIT $limit",
            {"terms": terms, "limit": _limit(limit)},
            ("community_id", "representative_terms", "dois", "titles", "materials", "preparation_methods"),
        )
    ]


def _community_representative_titles(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    return [
        _path(
            "community.representative_titles",
            "MATCH (d:doi)-[:title]->(t:title) "
            "WHERE d.louvainCommunityId = $community_id "
            "RETURN d.louvainCommunityId AS community_id, d.name AS doi, t.name AS title LIMIT $limit",
            {"community_id": slots.get("community_id"), "limit": _limit(limit)},
            ("community_id", "doi", "title"),
            direct=True,
        )
    ]


def _community_representative_methods(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    return [
        _path(
            "community.representative_methods",
            "MATCH (d:doi)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method) "
            "WHERE d.louvainCommunityId = $community_id "
            "RETURN d.louvainCommunityId AS community_id, d.name AS doi, collect(DISTINCT pm.name)[0..5] AS preparation_methods LIMIT $limit",
            {"community_id": slots.get("community_id"), "limit": _limit(limit)},
            ("community_id", "doi", "preparation_methods"),
        )
    ]


def _community_profile(slots: dict[str, Any], limit: int) -> list[GraphQueryPath]:
    return [
        _path(
            "community.profile",
            "MATCH (d:doi) "
            "WHERE d.louvainCommunityId = $community_id "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "RETURN d.louvainCommunityId AS community_id, count(DISTINCT d) AS paper_count, collect(DISTINCT t.name)[0..10] AS titles LIMIT $limit",
            {"community_id": slots.get("community_id"), "limit": _limit(limit)},
            ("community_id", "paper_count", "titles"),
        )
    ]


_BUILDERS = {
    "lookup_by_doi": _lookup_by_doi,
    "expand_doi_context": _expand_doi_context,
    "list_by_title_or_material": _list_by_title_or_material,
    "list_by_raw_material": _list_by_raw_material,
    "list_by_carbon_source": _list_by_carbon_source,
    "list_by_process_method": _list_by_process_method,
    "count_by_structured_field": _count_by_structured_field,
    "numeric_property_query": _numeric_property_query,
    "hybrid_property_analysis": _hybrid_property_analysis,
    "community_find_by_term": _community_find_by_term,
    "community_representative_titles": _community_representative_titles,
    "community_representative_methods": _community_representative_methods,
    "community_profile": _community_profile,
}


def build_v1_query_paths(*, intent: str, slots: dict[str, Any] | None = None, limit: int = 20) -> tuple[GraphQueryPath, ...]:
    builder = _BUILDERS.get(str(intent or "").strip())
    if builder is None:
        return ()
    return tuple(builder(dict(slots or {}), _limit(limit)))
