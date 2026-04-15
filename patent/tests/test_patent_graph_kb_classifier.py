from __future__ import annotations

from server.patent.graph_kb.classifier import classify_patent_graph_kb_question


def test_classifier_tries_graph_for_direct_patent_lookup():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 这件专利是什么？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "patent_id_lookup"
    assert decision.standalone is True


def test_classifier_tries_graph_for_process_step_lookup():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 的工艺步骤是什么？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "patent_process_steps"


def test_classifier_tries_graph_for_material_role_lookup():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 使用了哪些原料？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "patent_material_roles"


def test_classifier_tries_graph_for_experiment_table_lookup():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 有哪些实验表格和性能数据？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "patent_experiment_tables"


def test_classifier_tries_graph_for_problem_solution_lookup():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 解决了什么技术问题，提出了什么方案？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "patent_problem_solution"


def test_classifier_tries_graph_for_inventive_scope_lookup():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 的发明点和保护范围是什么？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "patent_inventive_scope"


def test_classifier_tries_graph_for_citation_lookup():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 引用了哪些专利？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "patent_citations"


def test_classifier_tries_graph_for_ipc_listing():
    decision = classify_patent_graph_kb_question(
        "H01M10/0525 下有哪些专利？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "ipc_listing"


def test_classifier_tries_graph_for_applicant_listing():
    decision = classify_patent_graph_kb_question(
        "宁德时代新能源科技股份有限公司有哪些专利？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "applicant_listing"


def test_classifier_skips_doi_question():
    decision = classify_patent_graph_kb_question(
        "10.1039/c4ra15767b 这篇文献是什么？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "doi_not_supported"
    assert decision.standalone is True


def test_classifier_skips_broad_semantic_question():
    decision = classify_patent_graph_kb_question(
        "为什么这种技术路线更有前景？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "broad_semantic_question"


def test_classifier_skips_ambiguous_followup():
    decision = classify_patent_graph_kb_question(
        "它的工艺步骤是什么？",
        conversation_context={
            "conversation_state": {"last_turn_route": "kb_qa"},
            "recent_turns_for_llm": [{"role": "assistant", "content": "上轮刚提到一件专利"}],
        },
    )

    assert decision.decision == "skip"
    assert decision.reason == "ambiguous_followup"
    assert decision.standalone is False


def test_classifier_skips_when_source_selection_contains_file_context():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 这件专利是什么？",
        conversation_context={
            "source_selection": {
                "source_scope": "pdf+kb",
                "selected_file_ids": [11],
                "execution_files": [{"file_id": 11, "file_type": "pdf"}],
            }
        },
    )

    assert decision.decision == "skip"
    assert decision.reason == "file_context_present"
    assert decision.standalone is False


def test_classifier_skips_empty_question():
    decision = classify_patent_graph_kb_question(
        "   ",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "no_graph_signal"


def test_classifier_exposes_explainability_fields():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 这件专利是什么？",
        conversation_context={},
    )

    assert isinstance(decision.reason, str)
    assert isinstance(decision.standalone, bool)
    assert isinstance(decision.signals, tuple)


def test_classifier_skips_multi_patent_question():
    decision = classify_patent_graph_kb_question(
        "CN100355122C 和 CN100371239C 有什么区别？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "multiple_patent_ids"
    assert decision.standalone is True
