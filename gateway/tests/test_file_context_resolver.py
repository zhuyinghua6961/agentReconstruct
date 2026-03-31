from app.models.files import ConversationFileRow
from app.services.file_context_resolver import FileContextResolver


resolver = FileContextResolver()

PDF = ConversationFileRow(file_id=11, file_type="pdf", file_name="solid-state-review.pdf")
PDF_2 = ConversationFileRow(file_id=22, file_type="pdf", file_name="battery-paper.pdf")
TABLE = ConversationFileRow(
    file_id=33,
    file_type="excel",
    file_name="cells.xlsx",
    file_meta={"columns": ["电芯编号", "开路电压_V", "供应商"]},
)
PROCESSING_PDF = ConversationFileRow(
    file_id=44,
    file_type="pdf",
    file_name="processing.pdf",
    parse_status="uploaded",
    index_status="pending",
    processing_stage="indexing",
    display_no=1,
)
FAILED_PDF = ConversationFileRow(
    file_id=55,
    file_type="pdf",
    file_name="failed.pdf",
    parse_status="failed",
    index_status="failed",
    processing_stage="failed",
    display_no=1,
)


class _StubClassifier:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def classify(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


def test_no_files_and_plain_question_stays_kb():
    decision = resolver.resolve(question="磷酸铁锂电压范围是多少？")

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.selected_file_ids == []


def test_pdf_files_exist_but_plain_question_stays_kb():
    decision = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={"all_available_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.selected_file_ids == []


def test_selected_files_only_do_not_direct_route_plain_question():
    decision = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.selected_file_ids == [11]
    assert decision.strategy == "selected_ids_no_file_intent"


def test_selected_files_with_explicit_file_action_routes_to_file_qa():
    decision = resolver.resolve(
        question="请总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [11]


def test_classifier_is_not_called_for_deterministic_explicit_route():
    from app.services.route_classifier import ClassifierDecision

    classifier = _StubClassifier(
        ClassifierDecision(
            route="kb_qa",
            turn_mode="kb_only",
            source_scope="kb",
            confidence=0.95,
            reason_codes=["CLASSIFIER_KB_QA"],
        )
    )
    resolver = FileContextResolver(route_classifier=classifier, classifier_enabled=True)

    decision = resolver.resolve(
        question="请总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "pdf_qa"
    assert classifier.calls == []


def test_classifier_is_called_for_ambiguity_cases_only():
    from app.services.route_classifier import ClassifierDecision

    classifier = _StubClassifier(
        ClassifierDecision(
            route="kb_qa",
            turn_mode="kb_only",
            source_scope="kb",
            confidence=0.7,
            reason_codes=["CLASSIFIER_KB_QA"],
        )
    )
    resolver = FileContextResolver(route_classifier=classifier, classifier_enabled=True)

    decision = resolver.resolve(
        question="帮我看一下",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "kb_qa"
    assert len(classifier.calls) == 1


def test_classifier_is_not_called_for_plain_kb_questions_when_only_available_files_exist():
    from app.services.route_classifier import ClassifierDecision

    classifier = _StubClassifier(
        ClassifierDecision(
            route="pdf_qa",
            turn_mode="file_only",
            source_scope="pdf",
            confidence=0.95,
            reason_codes=["CLASSIFIER_FILE_QA"],
        )
    )
    resolver = FileContextResolver(route_classifier=classifier, classifier_enabled=True)

    decision = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={"all_available_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.classifier_used is False
    assert classifier.calls == []


def test_classifier_low_confidence_falls_back_to_rule_default():
    from app.services.route_classifier import ClassifierDecision

    classifier = _StubClassifier(
        ClassifierDecision(
            route="pdf_qa",
            turn_mode="file_only",
            source_scope="pdf",
            confidence=0.5,
            reason_codes=["CLASSIFIER_FILE_QA"],
        )
    )
    resolver = FileContextResolver(route_classifier=classifier, classifier_enabled=True)

    decision = resolver.resolve(
        question="帮我看一下",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "kb_qa"
    assert decision.classifier_used is False


def test_classifier_high_confidence_can_choose_pdf_route():
    from app.services.route_classifier import ClassifierDecision

    classifier = _StubClassifier(
        ClassifierDecision(
            route="pdf_qa",
            turn_mode="file_only",
            source_scope="pdf",
            confidence=0.91,
            reason_codes=["CLASSIFIER_FILE_QA"],
        )
    )
    resolver = FileContextResolver(route_classifier=classifier, classifier_enabled=True)

    decision = resolver.resolve(
        question="帮我看一下",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [11]
    assert decision.classifier_used is True


def test_classifier_mid_conflict_file_route_does_not_override_rule_layer():
    from app.services.route_classifier import ClassifierDecision

    classifier = _StubClassifier(
        ClassifierDecision(
            route="pdf_qa",
            turn_mode="file_only",
            source_scope="pdf",
            confidence=0.7,
            reason_codes=["CLASSIFIER_FILE_QA"],
        )
    )
    resolver = FileContextResolver(route_classifier=classifier, classifier_enabled=True)

    decision = resolver.resolve(
        question="帮我看一下",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "kb_qa"
    assert decision.classifier_used is False


def test_classifier_high_confidence_can_choose_hybrid_route():
    from app.services.route_classifier import ClassifierDecision

    classifier = _StubClassifier(
        ClassifierDecision(
            route="hybrid_qa",
            turn_mode="mixed",
            source_scope="pdf+kb",
            confidence=0.9,
            reason_codes=["CLASSIFIER_HYBRID_QA"],
        )
    )
    resolver = FileContextResolver(route_classifier=classifier, classifier_enabled=True)

    decision = resolver.resolve(
        question="帮我一起看一下",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "hybrid_qa"
    assert decision.turn_mode == "mixed"
    assert decision.allow_kb_verification is True
    assert decision.classifier_used is True


def test_selected_files_with_non_deictic_file_action_routes_to_file_qa():
    decision = resolver.resolve(
        question="请总结所选文件",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [11, 22]
    assert decision.strategy == "selected_scope"


def test_selected_files_with_action_target_pattern_routes_to_file_qa():
    decision = resolver.resolve(
        question="请比较文献",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [11, 22]
    assert decision.strategy == "selected_scope"


def test_explicit_ref_uses_display_order_reference_universe():
    decision = resolver.resolve(
        question="#1",
        pdf_context={"all_available_ids": [11, 22]},
        available_files=[
            ConversationFileRow(file_id=11, file_type="pdf", file_name="b.pdf", display_no=2, file_no=2),
            ConversationFileRow(file_id=22, file_type="pdf", file_name="a.pdf", display_no=1, file_no=1),
        ],
    )

    assert decision.route == "pdf_qa"
    assert decision.selected_file_ids == [22]


def test_ordinal_ref_uses_display_order_reference_universe():
    decision = resolver.resolve(
        question="前 2 个文件",
        pdf_context={"all_available_ids": [11, 22, 33]},
        available_files=[
            ConversationFileRow(file_id=11, file_type="pdf", file_name="b.pdf", display_no=3, file_no=3),
            ConversationFileRow(file_id=22, file_type="pdf", file_name="a.pdf", display_no=1, file_no=1),
            ConversationFileRow(file_id=33, file_type="pdf", file_name="c.pdf", display_no=2, file_no=2),
        ],
    )

    assert decision.route == "hybrid_qa" or decision.route == "pdf_qa"
    assert decision.selected_file_ids == [22, 33]


def test_explicit_ref_to_processing_file_returns_file_not_ready_status():
    decision = resolver.resolve(
        question="#1",
        pdf_context={"all_available_ids": [44]},
        available_files=[PROCESSING_PDF],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [44]
    assert decision.execution_files == []
    assert decision.status_code == "FILE_NOT_READY"
    assert decision.status_retriable is True


def test_explicit_ref_to_failed_file_returns_processing_failed_status():
    decision = resolver.resolve(
        question="#1",
        pdf_context={"all_available_ids": [55]},
        available_files=[FAILED_PDF],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [55]
    assert decision.execution_files == []
    assert decision.status_code == "FILE_PROCESSING_FAILED"
    assert decision.status_retriable is False


def test_explicit_ref_without_metadata_requires_clarification():
    decision = resolver.resolve(
        question="#1",
        pdf_context={"all_available_ids": [77]},
        available_files=[],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.needs_clarification is True
    assert decision.status_code == ""
    assert decision.selected_file_ids == [77]


def test_out_of_range_ordinal_ref_requires_clarification():
    decision = resolver.resolve(
        question="第 3 个文件",
        pdf_context={"all_available_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.needs_clarification is True
    assert decision.status_code == ""


def test_out_of_range_reverse_ordinal_ref_requires_clarification():
    decision = resolver.resolve(
        question="倒数第 3 个文件",
        pdf_context={"all_available_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.needs_clarification is True
    assert decision.status_code == ""


def test_multiple_table_candidates_include_candidate_summary_in_clarification():
    table_2 = ConversationFileRow(
        file_id=34,
        file_type="excel",
        file_name="cells-2.xlsx",
        file_meta={"columns": ["温度", "电压"]},
    )
    decision = resolver.resolve(
        question="请总结这个表格",
        pdf_context={"selected_ids": [33, 34]},
        available_files=[TABLE, table_2],
    )

    assert decision.needs_clarification is True
    assert decision.strategy == "clarify_required"
    assert [candidate["file_id"] for candidate in decision.clarify_candidates] == [33, 34]


def test_selected_files_with_explicit_mixed_intent_routes_to_hybrid():
    decision = resolver.resolve(
        question="请结合知识库总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "mixed"
    assert decision.allow_kb_verification is True


def test_selected_files_with_non_deictic_mixed_intent_routes_to_mixed_path():
    decision = resolver.resolve(
        question="参考所选文件并结合知识库分析",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "mixed"
    assert decision.selected_file_ids == [11, 22]
    assert decision.strategy == "selected_scope"
    assert decision.allow_kb_verification is True


def test_selected_files_with_action_target_mixed_question_routes_to_mixed_path():
    decision = resolver.resolve(
        question="请结合知识库总结文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "pdf_qa"
    assert decision.turn_mode == "mixed"
    assert decision.selected_file_ids == [11]
    assert decision.strategy == "selected_scope"
    assert decision.allow_kb_verification is True


def test_selected_scope_does_not_route_generic_file_topic_questions():
    for question in (
        "文献分析方法有哪些？",
        "请解释文献综述应该怎么写",
        "论文对比方法有哪些？",
        "请说明文件上传失败怎么处理",
    ):
        decision = resolver.resolve(
            question=question,
            pdf_context={"selected_ids": [11, 22]},
            available_files=[PDF, PDF_2],
        )

        assert decision.route == "kb_qa"
        assert decision.turn_mode == "kb_only"
        assert decision.strategy == "selected_ids_no_file_intent"


def test_selected_scope_does_not_route_generic_file_topic_with_single_selected_file():
    decision = resolver.resolve(
        question="如何分析论文的实验设计？",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.strategy == "selected_ids_no_file_intent"


def test_invalid_selected_ids_do_not_direct_route_selected_scope_actions():
    decision = resolver.resolve(
        question="请总结所选文件",
        pdf_context={"selected_ids": [999]},
        available_files=[],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.selected_file_ids == [999]
    assert decision.strategy == "clarify_required"


def test_invalid_selected_ids_clarify_implicit_selected_scope_actions():
    decision = resolver.resolve(
        question="请比较文献",
        pdf_context={"selected_ids": [999], "all_available_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.needs_clarification is True
    assert decision.strategy == "clarify_required"


def test_invalid_selected_ids_do_not_force_singular_file_reference_route():
    decision = resolver.resolve(
        question="请总结这篇文献",
        pdf_context={"selected_ids": [999], "all_available_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.strategy == "clarify_required"


def test_invalid_selected_ids_do_not_force_table_reference_route():
    decision = resolver.resolve(
        question="请统计这个表格",
        pdf_context={"selected_ids": [999], "all_available_ids": [33]},
        available_files=[TABLE],
    )

    assert decision.route == "tabular_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [33]
    assert decision.strategy == "single_candidate"


def test_single_token_table_words_do_not_route_pdf_turns_to_tabular():
    for question in ("列出 3 种原因", "进行分析", "表明厚电极存在极化"):
        decision = resolver.resolve(
            question=question,
            pdf_context={"all_available_ids": [11, 22]},
            available_files=[PDF, PDF_2],
        )

        assert decision.route == "kb_qa"
        assert decision.turn_mode == "kb_only"


def test_explicit_table_operations_route_selected_table_to_tabular():
    decision = resolver.resolve(
        question="按电压列筛选并输出前 5 行",
        pdf_context={"selected_ids": [33]},
        available_files=[TABLE],
    )

    assert decision.route == "tabular_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [33]


def test_explicit_table_operations_filter_out_pdf_candidates():
    decision = resolver.resolve(
        question="按电压列筛选并输出前 5 行",
        pdf_context={"selected_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )

    assert decision.route == "tabular_qa"
    assert decision.turn_mode == "file_only"
    assert decision.selected_file_ids == [33]


def test_structured_table_patterns_do_not_route_when_no_table_candidates_exist():
    for question in ("请输出前5行结论", "请看第3行公式", "按下列方式分析厚电极极化原因"):
        decision = resolver.resolve(
            question=question,
            pdf_context={"all_available_ids": [11, 22]},
            available_files=[PDF, PDF_2],
        )

        assert decision.route == "kb_qa"
        assert decision.turn_mode == "kb_only"


def test_invalid_newly_uploaded_ids_do_not_force_latest_upload_route():
    decision = resolver.resolve(
        question="请总结最新上传的文献",
        pdf_context={"newly_uploaded_ids": [999], "all_available_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.strategy == "clarify_required"


def test_latest_file_phrase_does_not_trigger_latest_upload_reuse():
    decision = resolver.resolve(
        question="please summarize the latest file",
        pdf_context={"newly_uploaded_ids": [22], "all_available_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"


def test_last_focus_requires_real_route_name_not_generic_mode_words():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_ids": [22],
            "last_turn_route": "mixed",
        },
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.strategy == "clarify_required"


def test_selected_scope_mixed_rule_does_not_route_generic_mixed_questions():
    decision = resolver.resolve(
        question="结合知识库分析文献综述应该怎么写",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.strategy == "selected_ids_no_file_intent"


def test_selected_scope_action_pattern_does_not_match_generic_suffix_questions():
    plain = resolver.resolve(
        question="请分析文件，应该从哪些维度入手？",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )
    mixed = resolver.resolve(
        question="请结合知识库分析文件内容，应该关注哪些方面？",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert plain.route == "kb_qa"
    assert plain.turn_mode == "kb_only"
    assert mixed.route == "kb_qa"
    assert mixed.turn_mode == "kb_only"


def test_selected_scope_reference_meta_questions_do_not_direct_route():
    plain = resolver.resolve(
        question="应该怎么总结所选文件？",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )
    mixed = resolver.resolve(
        question="参考所选文件并结合知识库分析应该关注哪些方面？",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert plain.route == "kb_qa"
    assert plain.turn_mode == "kb_only"
    assert mixed.route == "kb_qa"
    assert mixed.turn_mode == "kb_only"


def test_invalid_selected_ids_selected_scope_meta_questions_stay_kb_only():
    decision = resolver.resolve(
        question="请说明所选文件上传失败怎么处理",
        pdf_context={"selected_ids": [999], "all_available_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.needs_clarification is False
    assert decision.strategy == "selected_ids_no_file_intent"


def test_last_focus_without_deictic_reference_does_not_force_file_route():
    decision = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_ids": [22],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"
    assert decision.selected_file_ids == []


def test_valid_deictic_last_focus_reuse_routes_to_file():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_ids": [22],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "pdf_qa"
    assert decision.selected_file_ids == [22]
    assert decision.strategy == "last_focus"


def test_invalid_last_focus_reuse_does_not_force_file_route():
    decision = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_ids": [999],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"


def test_last_turn_route_alone_does_not_force_route():
    decision = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )

    assert decision.route == "kb_qa"
    assert decision.turn_mode == "kb_only"


def test_newly_uploaded_is_only_reused_for_latest_upload_language():
    plain = resolver.resolve(
        question="请总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "newly_uploaded_ids": [22],
        },
        available_files=[PDF, PDF_2],
    )
    latest = resolver.resolve(
        question="请总结最新上传的文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "newly_uploaded_ids": [22],
        },
        available_files=[PDF, PDF_2],
    )

    assert plain.needs_clarification is True
    assert plain.selected_file_ids == [11, 22]
    assert latest.route == "pdf_qa"
    assert latest.selected_file_ids == [22]
    assert latest.strategy == "latest_new_upload"
