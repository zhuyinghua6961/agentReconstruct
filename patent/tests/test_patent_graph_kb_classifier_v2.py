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
    assert decision.diagnostics["matched_rule"] == "legacy_template"
    assert decision.diagnostics["legacy_template_id"] == "lookup_patent_by_id"
    assert decision.diagnostics["patent_ids"] == ("CN100355122C",)


def test_classifier_v2_routes_ipc_listing_to_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="H01M10/0525 下有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "legacy_template"
    assert decision.diagnostics["legacy_template_id"] == "list_patents_by_ipc"
    assert decision.diagnostics["ipc_codes"] == ("H01M10/0525",)


def test_classifier_v2_routes_ipc_subclass_query_to_precise_graph_path():
    decision = classify_patent_graph_question_v2(
        question="H01M10 下有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "ipc_subclass_listing"
    assert decision.diagnostics["anchor_kind"] == "ipc_subclass"
    assert decision.diagnostics["ipc_subclasses"] == ("H01M10",)


def test_classifier_v2_routes_applicant_listing_to_direct_answer():
    decision = classify_patent_graph_question_v2(
        question="宁德时代新能源科技股份有限公司有哪些专利？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "legacy_template"
    assert decision.diagnostics["legacy_template_id"] == "list_patents_by_applicant"
    assert decision.diagnostics["organization_name"] == "宁德时代新能源科技股份有限公司"


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
