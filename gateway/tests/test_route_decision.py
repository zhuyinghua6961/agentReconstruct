from app.models.files import ConversationFileRow
from app.services.file_context_resolver import FileContextResolver
from app.services.route_decision import RouteDecisionService


resolver = FileContextResolver()
router = RouteDecisionService()


PDF = ConversationFileRow(file_id=11, file_type="pdf", file_name="solid-state-review.pdf")
PDF_2 = ConversationFileRow(file_id=22, file_type="pdf", file_name="battery-paper.pdf")
TABLE = ConversationFileRow(
    file_id=33,
    file_type="excel",
    file_name="cells.xlsx",
    file_meta={"columns": ["电芯编号", "开路电压_V", "供应商"]},
)


def test_plain_question_keeps_requested_mode():
    decision = resolver.resolve(question="磷酸铁锂电压范围是多少？", pdf_context={"selected_ids": [11]})
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.actual_mode == "thinking"
    assert routed.route == "kb_qa"


def test_generic_literature_topic_does_not_force_file_route():
    decision = resolver.resolve(question="文献综述一般怎么写？", pdf_context={"selected_ids": [11]})
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.actual_mode == "thinking"
    assert decision.route == "kb_qa"


def test_file_question_forces_fast_mode():
    decision = resolver.resolve(question="请总结这篇文献", pdf_context={"selected_ids": [11]})
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.actual_mode == "fast"
    assert routed.route == "pdf_qa"


def test_singular_reference_with_multiple_candidates_requires_clarification():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={"selected_ids": [11, 22]},
    )
    assert decision.needs_clarification is True
    assert decision.strategy == "clarify_required"


def test_last_focus_reuses_previous_file_route():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_ids": [22],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )
    assert decision.needs_clarification is False
    assert decision.selected_file_ids == [22]
    assert decision.strategy == "last_focus"
    assert decision.route == "pdf_qa"


def test_mixed_question_sets_mixed_turn_mode():
    decision = resolver.resolve(
        question="请结合知识库总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert decision.turn_mode == "mixed"
    assert decision.allow_kb_verification is True
    assert routed.actual_mode == "fast"
    assert routed.source_scope == "pdf+kb"
    assert routed.kb_enabled is True


def test_mixed_pdf_turn_exposes_canonical_file_aware_fields():
    decision = resolver.resolve(
        question="请结合知识库总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "pdf+kb"
    assert routed.kb_enabled is True
    assert routed.selected_file_ids == [11]
    assert routed.primary_file_id == 11
    assert routed.file_selection == {
        "strategy": "selected_single",
        "selected_file_ids": [11],
        "turn_mode": "mixed",
        "source_scope": "pdf+kb",
        "kb_enabled": True,
    }


def test_latest_upload_prefers_newly_uploaded_file():
    decision = resolver.resolve(
        question="请总结最新上传的文献",
        pdf_context={
            "selected_ids": [11],
            "newly_uploaded_ids": [11, 33],
            "all_available_ids": [11, 22, 33],
        },
        available_files=[PDF, PDF_2, TABLE],
    )
    assert decision.selected_file_ids == [33]
    assert decision.strategy == "latest_new_upload"


def test_ordinal_reference_resolves_first_file():
    decision = resolver.resolve(
        question="请总结第一个文件",
        pdf_context={"all_available_ids": [101, 102, 103]},
    )
    assert decision.selected_file_ids == [101]
    assert decision.strategy == "ordinal_ref"


def test_front_ordinal_resolves_multiple_files():
    decision = resolver.resolve(
        question="请比较前两个文件",
        pdf_context={"all_available_ids": [101, 102, 103]},
    )
    assert decision.selected_file_ids == [101, 102]
    assert decision.strategy == "ordinal_ref"


def test_deictic_count_plural_reference_resolves_all_current_files():
    decision = resolver.resolve(
        question="对比一下这三篇文献",
        pdf_context={"all_available_ids": [11, 22, 33]},
        available_files=[PDF, PDF_2, ConversationFileRow(file_id=33, file_type="pdf", file_name="third-paper.pdf")],
    )
    assert decision.route == "pdf_qa"
    assert decision.selected_file_ids == [11, 22, 33]
    assert decision.strategy == "deictic_count_scope"


def test_table_question_routes_to_tabular():
    decision = resolver.resolve(
        question="请统计这个表格的列分布",
        pdf_context={"selected_ids": [33]},
        available_files=[TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert decision.route == "tabular_qa"
    assert routed.actual_mode == "fast"
    assert routed.source_scope == "table"
    assert routed.kb_enabled is False


def test_column_name_focus_routes_selected_table_to_tabular():
    decision = resolver.resolve(
        question="开路电压_V 的分布是什么？",
        pdf_context={"selected_ids": [33]},
        available_files=[TABLE],
    )
    assert decision.route == "tabular_qa"
    assert decision.strategy == "metadata_focus_scope"


def test_filename_focus_routes_selected_pdf_to_pdf_qa():
    decision = resolver.resolve(
        question="solid-state-review 这篇文章的结论是什么？",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    assert decision.route == "pdf_qa"
    assert decision.strategy == "metadata_focus_scope"


def test_mixed_file_types_route_to_hybrid():
    decision = resolver.resolve(
        question="请比较前两个文件",
        pdf_context={"all_available_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )
    assert decision.route == "hybrid_qa"
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.source_scope == "pdf+table"
    assert routed.kb_enabled is False


def test_hybrid_pdf_table_with_kb_sets_pdf_table_kb_scope():
    decision = resolver.resolve(
        question="请结合知识库比较前两个文件",
        pdf_context={"all_available_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert decision.route == "hybrid_qa"
    assert decision.turn_mode == "mixed"
    assert routed.source_scope == "pdf+table+kb"
    assert routed.kb_enabled is True


def test_pdf_route_uses_pdf_source_scope():
    decision = resolver.resolve(
        question="请总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "pdf_qa"
    assert routed.source_scope == "pdf"
    assert routed.kb_enabled is False
    assert routed.selected_file_ids == [11]
    assert routed.primary_file_id == 11


def test_tabular_route_uses_table_source_scope():
    decision = resolver.resolve(
        question="请统计这个表格的列分布",
        pdf_context={"selected_ids": [33]},
        available_files=[TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "tabular_qa"
    assert routed.source_scope == "table"
    assert routed.kb_enabled is False
    assert routed.selected_file_ids == [33]
    assert routed.primary_file_id == 33


def test_mixed_table_turn_exposes_table_kb_source_scope():
    decision = resolver.resolve(
        question="请结合知识库分析这个表格",
        pdf_context={"selected_ids": [33]},
        available_files=[TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "table+kb"
    assert routed.kb_enabled is True
    assert routed.selected_file_ids == [33]
    assert routed.primary_file_id == 33


def test_hybrid_file_only_turn_with_pdf_and_table_uses_pdf_table_source_scope():
    decision = resolver.resolve(
        question="请比较前两个文件",
        pdf_context={"all_available_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "pdf+table"
    assert routed.kb_enabled is False
    assert routed.selected_file_ids == [11, 33]
    assert routed.primary_file_id is None


def test_hybrid_mixed_turn_with_pdf_and_table_uses_pdf_table_kb_source_scope():
    decision = resolver.resolve(
        question="请结合知识库比较前两个文件",
        pdf_context={"all_available_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "pdf+table+kb"
    assert routed.kb_enabled is True
    assert routed.selected_file_ids == [11, 33]
    assert routed.primary_file_id is None


def test_mixed_question_with_file_before_knowledge_base_routes_to_pdf_kb():
    decision = resolver.resolve(
        question="结合这篇文献和知识库，讲一下为什么厚电极在高电流密度下存在严重的液相浓差极化",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert decision.turn_mode == "mixed"
    assert decision.allow_kb_verification is True
    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "pdf+kb"
    assert routed.kb_enabled is True
