from __future__ import annotations

from app.modules.graph_kb.classifier_v2 import classify_graph_question_v2


def test_classifier_v2_preserves_commander_numeric_precise_order():
    decision = classify_graph_question_v2(question="压实密度最高的LFP材料有哪些？", conversation_context={})

    assert decision.legacy_route == "precise"
    assert decision.mode in {"direct_answer", "graph_for_rag"}


def test_classifier_v2_preserves_legacy_community_branch_before_semantic_fallback():
    decision = classify_graph_question_v2(question="请分析该数据集里材料关系网络的机制关联", conversation_context={})

    assert decision.legacy_route == "community"
    assert decision.mode == "skip_graph"


def test_classifier_v2_preserves_semantic_keyword_priority_over_graph_enumeration():
    decision = classify_graph_question_v2(question="为什么 LFP 的循环性能更稳定？", conversation_context={})

    assert decision.legacy_route == "semantic"
    assert decision.mode == "graph_for_rag"


def test_classifier_v2_preserves_numeric_only_precise_route_before_entity_fallback():
    decision = classify_graph_question_v2(question="压实密度大于 2.4 的材料有哪些？", conversation_context={})

    assert decision.legacy_route == "precise"
    assert decision.diagnostics["matched_rule"] == "numeric_attribute_only"


def test_classifier_v2_preserves_entity_keyword_fallback():
    decision = classify_graph_question_v2(question="LFP 有哪些文献？", conversation_context={})

    assert decision.legacy_route == "precise"


def test_classifier_v2_doi_semantic_overlap_prefers_semantic_graph_for_rag():
    decision = classify_graph_question_v2(question="10.1000/test 这篇文献为什么循环性能更稳定？", conversation_context={})

    assert decision.legacy_route == "semantic"
    assert decision.mode == "graph_for_rag"


def test_classifier_v2_file_context_downgrades_to_graph_for_rag_instead_of_skip():
    decision = classify_graph_question_v2(
        question="LFP 有哪些文献？",
        conversation_context={
            "conversation_state": {"last_turn_route": "pdf_qa"},
            "source_selection": {"source_scope": "pdf", "selected_file_ids": [1]},
        },
    )

    assert decision.mode == "graph_for_rag"
    assert decision.diagnostics["override"] == "file_context_present"


def test_classifier_v2_followup_requires_context_resolution_instead_of_skip():
    decision = classify_graph_question_v2(
        question="那篇文献为什么循环性能更稳定？",
        conversation_context={"recent_turns_for_llm": [{"role": "assistant", "content": "前面提到了 10.1000/test"}]},
    )

    assert decision.mode == "graph_for_rag"
    assert decision.diagnostics["requires_context_resolution"] is True
