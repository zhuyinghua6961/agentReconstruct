from __future__ import annotations

from app.modules.graph_kb.classifier import classify_graph_kb_question


def test_classifier_tries_graph_for_doi_question():
    decision = classify_graph_kb_question(
        "10.1000/test 这篇文献是什么？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "doi_lookup"
    assert decision.standalone is True


def test_classifier_tries_graph_for_literature_listing_question():
    decision = classify_graph_kb_question(
        "有哪些关于LFP的文献？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "literature_listing"


def test_classifier_tries_graph_for_raw_material_listing_question():
    decision = classify_graph_kb_question(
        "有哪些使用LiFePO4作为原料的文献？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "raw_material_listing"


def test_classifier_tries_graph_for_doi_context_expansion_question():
    decision = classify_graph_kb_question(
        "10.1039/c4ra15767b 这篇文献做了哪些测试和工艺？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "doi_context_lookup"


def test_classifier_skips_doi_material_system_question_to_main_kb_path():
    decision = classify_graph_kb_question(
        "10.1039/c4ra15767b 这篇文献的材料体系是什么？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "doi_lookup"


def test_classifier_allows_digit_bearing_material_name_for_literature_listing():
    decision = classify_graph_kb_question(
        "有哪些关于LiFePO4的文献？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "literature_listing"


def test_classifier_allows_non_ranking_keyword_containing_qian_character():
    decision = classify_graph_kb_question(
        "有哪些关于前驱体的文献？",
        conversation_context={},
    )

    assert decision.decision == "try_graph"
    assert decision.reason == "literature_listing"


def test_classifier_skips_pronoun_followup():
    decision = classify_graph_kb_question(
        "它的DOI是什么？",
        conversation_context={
            "conversation_state": {"last_turn_route": "kb_qa"},
            "recent_turns_for_llm": [{"role": "assistant", "content": "上轮提到了几篇文献"}],
        },
    )

    assert decision.decision == "skip"
    assert decision.reason == "ambiguous_followup"
    assert decision.standalone is False


def test_classifier_skips_when_last_turn_is_file_route():
    decision = classify_graph_kb_question(
        "最高的是哪篇？",
        conversation_context={
            "conversation_state": {"last_turn_route": "hybrid_qa"},
        },
    )

    assert decision.decision == "skip"
    assert decision.reason == "file_context_present"


def test_classifier_skips_numeric_property_question_to_vector_db():
    decision = classify_graph_kb_question(
        "磷酸铁锂的压实密度是多少？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "no_graph_signal"


def test_classifier_skips_general_attribute_question_to_vector_db():
    decision = classify_graph_kb_question(
        "磷酸铁锂的电压是多少？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "no_graph_signal"


def test_classifier_skips_literature_question_with_numeric_filter_suffix():
    decision = classify_graph_kb_question(
        "LFP有多少篇文献的压实密度大于3.5？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "no_graph_signal"


def test_classifier_skips_material_wording_for_raw_material_template():
    decision = classify_graph_kb_question(
        "有哪些使用LiFePO4作为材料的文献？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "no_graph_signal"


def test_classifier_skips_literature_question_when_keyword_is_property_phrase():
    decision = classify_graph_kb_question(
        "有哪些关于压实密度大于3.5的文献？",
        conversation_context={},
    )

    assert decision.decision == "skip"
    assert decision.reason == "no_graph_signal"


def test_classifier_skips_when_source_selection_contains_file_context():
    decision = classify_graph_kb_question(
        "10.1000/test 这篇文献是什么？",
        conversation_context={
            "source_selection": {
                "source_scope": "pdf",
                "selected_file_ids": [1],
                "execution_files": [{"file_id": 1, "file_type": "pdf"}],
            }
        },
    )

    assert decision.decision == "skip"
    assert decision.reason == "file_context_present"


def test_classifier_exposes_explainability_fields():
    decision = classify_graph_kb_question(
        "有哪些关于LFP的文献？",
        conversation_context={},
    )

    assert decision.decision in {"skip", "try_graph"}
    assert isinstance(decision.reason, str)
    assert isinstance(decision.standalone, bool)
    assert isinstance(decision.signals, tuple)
