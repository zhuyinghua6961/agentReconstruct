from __future__ import annotations

from server.patent.graph_kb.query_templates import (
    build_patent_template_candidates,
    get_patent_query_template,
    list_patent_query_templates,
)
from server.patent.graph_kb.slots import extract_patent_graph_slots


def _template_ids() -> set[str]:
    return {item.template_id for item in list_patent_query_templates()}


def test_registry_contains_required_patent_templates():
    assert {
        "lookup_patent_by_id",
        "list_patent_process_steps",
        "list_patent_material_roles",
        "list_patent_experiment_tables",
        "list_patent_problem_solution",
        "list_patent_inventive_scope",
        "list_patent_citations",
        "list_patent_atmospheres",
        "list_patent_embodiment_insights",
        "list_patents_by_applicant",
        "count_patents_by_applicant",
        "list_patents_by_inventor",
        "count_patents_by_inventor",
        "list_patents_by_agency",
        "count_patents_by_agency",
        "list_patents_by_ipc_prefix",
        "count_patents_by_ipc_prefix",
        "list_patents_by_ipc_code_prefix",
        "count_patents_by_ipc_code_prefix",
        "list_patents_by_ipc_full_code",
        "count_patents_by_ipc_full_code",
        "compare_patents_process_steps",
        "compare_patents_material_roles",
        "compare_patents_problem_solution",
        "compare_patents_performance_facts",
        "compare_patents_claim_scope",
        "list_patents_by_material",
        "list_patents_by_material_role",
        "list_patents_by_process_term",
        "performance_by_process_term",
        "performance_by_material_term",
        "rank_materials_by_frequency",
        "rank_processes_by_frequency",
    }.issubset(_template_ids())


def test_templates_do_not_use_stale_paper_labels_but_allow_name_and_title_properties():
    stale_labels = (
        ":doi",
        ":Paper",
        ":Article",
        ":Sample",
        ":recipe",
        ":process",
        ":testing",
        ":name",
        ":title",
        ":__Chunk__",
        ":__Document__",
    )

    all_cypher = "\n".join(item.cypher for item in list_patent_query_templates())
    for label in stale_labels:
        assert label not in all_cypher
    assert ".name" in all_cypher
    assert ".title" in all_cypher


def test_ipc_templates_target_distinct_schema_grains():
    prefix = get_patent_query_template("list_patents_by_ipc_prefix")
    code_prefix = get_patent_query_template("list_patents_by_ipc_code_prefix")
    full_code = get_patent_query_template("list_patents_by_ipc_full_code")

    assert prefix is not None and "IPCPrefix" in prefix.cypher and "sub.subclass = $ipc_prefix" in prefix.cypher
    assert code_prefix is not None and "STARTS WITH $ipc_code_prefix" in code_prefix.cypher
    assert full_code is not None and "ipc.code = $ipc_full_code" in full_code.cypher


def test_specific_patent_facet_candidates_beat_generic_lookup():
    candidates = build_patent_template_candidates(
        extract_patent_graph_slots("CN100355122C 的气氛条件是什么？"),
        limit=20,
    )

    assert candidates[0]["path_id"] == "list_patent_atmospheres"
    assert any(item["path_id"] == "lookup_patent_by_id" for item in candidates)


def test_material_process_rank_templates_are_bounded():
    material_role = get_patent_query_template("list_patents_by_material_role")
    rank_materials = get_patent_query_template("rank_materials_by_frequency")
    rank_processes = get_patent_query_template("rank_processes_by_frequency")

    assert material_role is not None and "MaterialRole" in material_role.cypher
    assert rank_materials is not None and "count" in rank_materials.cypher.lower() and rank_materials.result_cap <= 100
    assert rank_processes is not None and "count" in rank_processes.cypher.lower() and rank_processes.result_cap <= 100


def test_compare_performance_candidate_beats_process_fallback():
    candidates = build_patent_template_candidates(
        extract_patent_graph_slots("比较 CN100355122C 和 CN100371239C 的性能指标差异"),
        limit=20,
    )

    assert candidates[0]["path_id"] == "compare_patents_performance_facts"


def test_material_role_and_rank_candidates_are_reachable_from_natural_questions():
    material = build_patent_template_candidates(extract_patent_graph_slots("涉及石墨烯的专利有哪些？"), limit=20)
    role = build_patent_template_candidates(extract_patent_graph_slots("涉及 main 材料角色的专利有哪些？"), limit=20)
    rank = build_patent_template_candidates(extract_patent_graph_slots("材料出现频次排名是什么？"), limit=20)

    assert material[0]["path_id"] == "list_patents_by_material"
    assert role[0]["path_id"] == "list_patents_by_material_role"
    assert rank[0]["path_id"] == "rank_materials_by_frequency"
