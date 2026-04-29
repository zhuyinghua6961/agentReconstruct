from __future__ import annotations

from server.patent.graph_kb.classifier_v2 import classify_patent_graph_question_v2


def test_classifier_v2_routes_patent_lookup_to_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="CN100355122C 这件专利是什么？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.standalone is True
    assert decision.requires_context_resolution is False
    assert decision.diagnostics["matched_rule"] == "patent_lookup"
    assert decision.diagnostics["candidate_path_ids"][0] == "lookup_patent_by_id"
    assert decision.diagnostics["patent_ids"] == ("CN100355122C",)


def test_classifier_v2_routes_ipc_listing_to_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="H01M10/0525 下有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "ipc_full_code_listing"
    assert decision.diagnostics["ipc_full_codes"] == ("H01M10/0525",)


def test_classifier_v2_routes_ipc_subclass_query_to_precise_graph_path():
    decision = classify_patent_graph_question_v2(
        question="H01M10 下有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "ipc_code_prefix_listing"
    assert decision.diagnostics["anchor_kind"] == "ipc_code_prefix"
    assert decision.diagnostics["ipc_code_prefixes"] == ("H01M10",)


def test_classifier_v2_routes_applicant_listing_to_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="宁德时代新能源科技股份有限公司有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "applicant_listing"
    assert decision.diagnostics["applicant_name"] == "宁德时代新能源科技股份有限公司"


def test_classifier_v2_routes_inventor_listing_to_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="发明人张三有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "inventor_listing"
    assert decision.diagnostics["entity_kind"] == "inventor"
    assert decision.diagnostics["inventor_name"] == "张三"


def test_classifier_v2_routes_agency_listing_to_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="代理机构北京理工专利事务所有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "agency_listing"
    assert decision.diagnostics["entity_kind"] == "agency"
    assert decision.diagnostics["agency_name"] == "北京理工专利事务所"


def test_classifier_v2_routes_suffix_agency_phrases_to_direct_answer():
    listing = classify_patent_graph_question_v2(
        question="北京品源专利代理有限公司代理了哪些专利？",
        conversation_context={},
    )
    count = classify_patent_graph_question_v2(
        question="北京品源专利代理有限公司代理了多少专利？",
        conversation_context={},
    )

    assert listing.mode == "direct_answer"
    assert listing.diagnostics["matched_rule"] == "agency_listing"
    assert listing.diagnostics["agency_name"] == "北京品源专利代理有限公司"
    assert count.mode == "direct_answer"
    assert count.diagnostics["matched_rule"] == "agency_count"
    assert count.diagnostics["agency_name"] == "北京品源专利代理有限公司"


def test_classifier_v2_routes_specific_patent_facets_to_direct_answer():
    cases = {
        "CN100355122C 的工艺步骤是什么？": "single_patent_process",
        "CN100355122C 使用了哪些原料？": "single_patent_materials",
        "CN100355122C 的技术问题和技术方案是什么？": "single_patent_problem_solution",
        "CN100355122C 的发明点和保护范围是什么？": "single_patent_inventive_scope",
        "CN100355122C 的气氛条件是什么？": "single_patent_atmosphere",
        "CN101209823B 的实施例洞察是什么？": "single_patent_embodiment",
    }

    for question, matched_rule in cases.items():
        decision = classify_patent_graph_question_v2(question=question, conversation_context={})
        assert decision.mode == "direct_answer"
        assert decision.route_family == "precise"
        assert decision.diagnostics["matched_rule"] == matched_rule


def test_classifier_v2_routes_multi_patent_compare_to_graph_for_rag():
    decision = classify_patent_graph_question_v2(
        question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        conversation_context={},
    )

    assert decision.mode == "graph_for_rag"
    assert decision.route_family == "hybrid"
    assert decision.standalone is True
    assert decision.diagnostics["matched_rule"] == "multi_patent_compare"
    assert decision.diagnostics["patent_ids"] == ("CN100355122C", "CN100371239C")


def test_classifier_v2_downgrades_file_context_turn_from_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="CN100355122C 的工艺步骤是什么？",
        conversation_context={
            "source_selection": {
                "source_scope": "pdf+kb",
                "selected_file_ids": [11],
                "execution_files": [{"file_id": 11, "file_type": "pdf"}],
            }
        },
    )

    assert decision.mode == "graph_for_rag"
    assert decision.route_family == "precise"
    assert decision.standalone is False
    assert decision.requires_context_resolution is True
    assert decision.diagnostics["override"] == "file_context_present"


def test_classifier_v2_skips_broad_semantic_question():
    decision = classify_patent_graph_question_v2(
        question="为什么这种技术路线更有前景？",
        conversation_context={},
    )

    assert decision.mode == "skip_graph"
    assert decision.route_family == "semantic"
    assert decision.diagnostics["matched_rule"] == "broad_semantic_question"


def test_classifier_v2_routes_anchored_process_material_why_to_hybrid():
    decision = classify_patent_graph_question_v2(
        question="为什么喷雾干燥能提升磷酸铁锂倍率性能？",
        conversation_context={},
    )

    assert decision.mode == "graph_for_rag"
    assert decision.route_family == "hybrid"
    assert decision.diagnostics["matched_rule"] == "hybrid_graph_anchor"


def test_classifier_v2_routes_material_and_rank_template_candidates():
    material = classify_patent_graph_question_v2(
        question="涉及石墨烯的专利有哪些？",
        conversation_context={},
    )
    material_performance = classify_patent_graph_question_v2(
        question="石墨烯材料对性能有什么影响？",
        conversation_context={},
    )
    material_rank = classify_patent_graph_question_v2(
        question="材料出现频次排名是什么？",
        conversation_context={},
    )
    process_rank = classify_patent_graph_question_v2(
        question="工艺出现频次排名是什么？",
        conversation_context={},
    )

    assert material.mode == "direct_answer"
    assert material.diagnostics["candidate_path_ids"][0] == "list_patents_by_material"
    assert material_performance.mode == "graph_for_rag"
    assert material_performance.diagnostics["candidate_path_ids"][0] == "performance_by_material_term"
    assert material_rank.mode == "direct_answer"
    assert material_rank.diagnostics["candidate_path_ids"][0] == "rank_materials_by_frequency"
    assert process_rank.mode == "direct_answer"
    assert process_rank.diagnostics["candidate_path_ids"][0] == "rank_processes_by_frequency"


def test_classifier_v2_routes_applicant_landscape_to_community():
    decision = classify_patent_graph_question_v2(
        question="宁德时代在磷酸铁锂方面的工艺路线有什么特点？",
        conversation_context={},
    )

    assert decision.mode == "graph_for_rag"
    assert decision.route_family == "community"
    assert decision.diagnostics["matched_rule"] == "community_landscape"


def test_classifier_v2_skips_doi_question():
    decision = classify_patent_graph_question_v2(
        question="10.1039/c4ra15767b 这篇文献是什么？",
        conversation_context={},
    )

    assert decision.mode == "skip_graph"
    assert decision.route_family == "semantic"
    assert decision.diagnostics["matched_rule"] == "doi_not_supported"


def test_classifier_v2_downgrades_ambiguous_followup():
    decision = classify_patent_graph_question_v2(
        question="它的工艺步骤是什么？",
        conversation_context={
            "conversation_state": {"last_turn_route": "kb_qa"},
            "recent_turns_for_llm": [{"role": "assistant", "content": "上轮刚提到一件专利"}],
        },
    )

    assert decision.mode == "graph_for_rag"
    assert decision.route_family == "hybrid"
    assert decision.standalone is False
    assert decision.requires_context_resolution is True
    assert decision.diagnostics["override"] == "ambiguous_followup"
