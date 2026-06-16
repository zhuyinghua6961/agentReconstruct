from app.models.files import ConversationFileRow
from app.models.routing import FileContextDecision
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


def test_plain_question_without_selection_keeps_requested_mode():
    decision = resolver.resolve(question="磷酸铁锂电压范围是多少？")
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.actual_mode == "thinking"
    assert routed.route == "kb_qa"
    assert routed.turn_mode == "kb_only"
    assert routed.source_scope == "kb"


def test_selected_pdf_file_scope_forces_fast_mode_for_plain_question():
    decision = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert decision.selected_file_ids == [11]
    assert decision.strategy == "explicit_selection"
    assert routed.actual_mode == "fast"
    assert routed.route == "pdf_qa"
    assert routed.source_scope == "pdf"
    assert routed.turn_mode == "file_only"
    assert routed.strategy == "explicit_selection"
    assert routed.selected_file_ids == [11]
    assert routed.execution_files == decision.execution_files
    assert "EXPLICIT_SELECTED_FILES" in routed.route_reasons


def test_generic_literature_topic_does_not_force_file_route():
    decision = resolver.resolve(question="文献综述一般怎么写？", pdf_context={"selected_ids": [11]})
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.actual_mode == "thinking"
    assert decision.route == "kb_qa"


def test_doi_lookup_with_selected_file_routes_to_file_scope():
    decision = resolver.resolve(
        question="10.1021/jp1005692 这篇文献是什么？",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="fast", file_context=decision)

    assert decision.route == "pdf_qa"
    assert decision.needs_clarification is False
    assert routed.route == "pdf_qa"
    assert routed.turn_mode == "file_only"
    assert routed.source_scope == "pdf"
    assert routed.selected_file_ids == [11]
    assert "EXPLICIT_SELECTED_FILES" in routed.route_reasons


def test_file_question_forces_fast_mode():
    decision = resolver.resolve(
        question="请总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.actual_mode == "fast"
    assert routed.route == "pdf_qa"


def test_singular_reference_with_multiple_selected_files_uses_full_selection():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={"selected_ids": [11, 22]},
        available_files=[PDF, PDF_2],
    )
    assert decision.needs_clarification is False
    assert decision.strategy == "explicit_selection"
    assert decision.selected_file_ids == [11, 22]


def test_last_focus_without_selection_stays_kb():
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
    assert decision.selected_file_ids == []
    assert decision.strategy == "none"
    assert decision.route == "kb_qa"


def test_last_focus_hybrid_route_without_selection_stays_kb():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_ids": [22],
            "last_turn_route": "hybrid_qa",
        },
        available_files=[PDF, PDF_2],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert decision.route == "kb_qa"
    assert routed.route == "kb_qa"
    assert routed.turn_mode == "kb_only"
    assert routed.source_scope == "kb"


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
    assert routed.execution_files == decision.execution_files
    assert routed.strategy == "explicit_selection"
    assert routed.primary_file_id == 11
    assert routed.route_confidence == 1.0
    assert routed.classifier_used is False
    assert routed.file_selection == {
        "strategy": "explicit_selection",
        "selected_file_ids": [11],
        "turn_mode": "mixed",
        "source_scope": "pdf+kb",
        "kb_enabled": True,
    }
    assert "EXPLICIT_SELECTED_FILES" in routed.route_reasons
    assert "EXPLICIT_MIXED_INTENT" in routed.route_reasons


def test_patent_requested_mode_keeps_patent_backend_for_file_routes():
    decision = resolver.resolve(
        question="请总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="patent", file_context=decision)

    assert routed.route == "pdf_qa"
    assert routed.actual_mode == "patent"
    assert routed.requested_mode == "patent"
    assert routed.source_scope == "pdf"
    assert "EXPLICIT_SELECTED_FILES" in routed.route_reasons


def test_patent_requested_mode_keeps_patent_backend_for_hybrid_routes():
    decision = resolver.resolve(
        question="请结合知识库总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    routed = router.decide(requested_mode="patent", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.actual_mode == "patent"
    assert routed.requested_mode == "patent"
    assert routed.turn_mode == "mixed"
    assert routed.source_scope == "pdf+kb"
    assert routed.kb_enabled is True
    assert "EXPLICIT_MIXED_INTENT" in routed.route_reasons


def test_pdf_table_file_turn_requires_selection():
    decision = resolver.resolve(
        question="请比较前两个文件",
        pdf_context={"all_available_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert decision.route == "kb_qa"
    assert routed.route == "kb_qa"
    assert routed.turn_mode == "kb_only"


def test_canonical_explicit_selection_strategy_is_preserved():
    decision = FileContextDecision(
        route="pdf_qa",
        turn_mode="file_only",
        selected_file_ids=[11],
        execution_files=[{"file_id": 11, "file_type": "pdf"}],
        strategy="explicit_selection",
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.strategy == "explicit_selection"
    assert routed.file_selection["strategy"] == "explicit_selection"
    assert "EXPLICIT_SELECTED_FILES" in routed.route_reasons


def test_canonical_latest_upload_strategy_is_preserved():
    decision = FileContextDecision(
        route="pdf_qa",
        turn_mode="file_only",
        selected_file_ids=[11],
        execution_files=[{"file_id": 11, "file_type": "pdf"}],
        strategy="latest_upload",
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.strategy == "latest_upload"
    assert routed.file_selection["strategy"] == "latest_upload"
    assert "LATEST_UPLOAD_REUSE" in routed.route_reasons


def test_metadata_focus_scope_keeps_pdf_specific_reason_code():
    decision = FileContextDecision(
        route="pdf_qa",
        turn_mode="file_only",
        selected_file_ids=[11],
        execution_files=[{"file_id": 11, "file_type": "pdf"}],
        strategy="metadata_focus_scope",
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.strategy == "explicit_selection"
    assert "EXPLICIT_PDF_REF" in routed.route_reasons


def test_metadata_focus_scope_keeps_table_specific_reason_code():
    decision = FileContextDecision(
        route="tabular_qa",
        turn_mode="file_only",
        selected_file_ids=[33],
        execution_files=[{"file_id": 33, "file_type": "excel"}],
        strategy="metadata_focus_scope",
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.strategy == "explicit_selection"
    assert "EXPLICIT_TABLE_REF" in routed.route_reasons


def test_metadata_focus_scope_keeps_pdf_specific_reason_code_in_mixed_turn():
    decision = FileContextDecision(
        route="pdf_qa",
        turn_mode="mixed",
        allow_kb_verification=True,
        selected_file_ids=[11],
        execution_files=[{"file_id": 11, "file_type": "pdf"}],
        strategy="metadata_focus_scope",
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "pdf+kb"
    assert "EXPLICIT_PDF_REF" in routed.route_reasons
    assert "EXPLICIT_MIXED_INTENT" in routed.route_reasons


def test_metadata_focus_scope_keeps_table_specific_reason_code_in_mixed_turn():
    decision = FileContextDecision(
        route="tabular_qa",
        turn_mode="mixed",
        allow_kb_verification=True,
        selected_file_ids=[33],
        execution_files=[{"file_id": 33, "file_type": "excel"}],
        strategy="metadata_focus_scope",
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "table+kb"
    assert "EXPLICIT_TABLE_REF" in routed.route_reasons
    assert "EXPLICIT_MIXED_INTENT" in routed.route_reasons


def test_latest_upload_without_selection_stays_kb():
    decision = resolver.resolve(
        question="请总结最新上传的文献",
        pdf_context={
            "selected_ids": [11],
            "newly_uploaded_ids": [11, 33],
            "all_available_ids": [11, 22, 33],
        },
        available_files=[PDF, PDF_2, TABLE],
    )
    assert decision.route == "pdf_qa"
    assert decision.selected_file_ids == [11]
    assert decision.strategy == "explicit_selection"


def test_ordinal_reference_without_selection_stays_kb():
    decision = resolver.resolve(
        question="请总结第一个文件",
        pdf_context={"all_available_ids": [101, 102, 103]},
    )
    assert decision.selected_file_ids == []
    assert decision.needs_clarification is False
    assert decision.strategy == "none"


def test_front_ordinal_without_selection_stays_kb():
    decision = resolver.resolve(
        question="请比较前两个文件",
        pdf_context={"all_available_ids": [101, 102, 103]},
    )
    assert decision.selected_file_ids == []
    assert decision.needs_clarification is False
    assert decision.strategy == "none"


def test_deictic_count_without_selection_stays_kb():
    decision = resolver.resolve(
        question="对比一下这三篇文献",
        pdf_context={"all_available_ids": [11, 22, 33]},
        available_files=[PDF, PDF_2, ConversationFileRow(file_id=33, file_type="pdf", file_name="third-paper.pdf")],
    )
    assert decision.route == "kb_qa"
    assert decision.selected_file_ids == []
    assert decision.strategy == "none"


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
    assert decision.strategy == "explicit_selection"


def test_table_singular_reference_uses_all_selected_files():
    decision = resolver.resolve(
        question="请总结这个表格",
        pdf_context={
            "selected_ids": [11, 22, 33],
            "newly_uploaded_ids": [11, 22, 33],
            "all_available_ids": [11, 22, 33],
        },
        available_files=[PDF, PDF_2, TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert decision.route == "hybrid_qa"
    assert decision.selected_file_ids == [11, 22, 33]
    assert decision.strategy == "explicit_selection"
    assert routed.source_scope == "pdf+table"
    assert routed.kb_enabled is False


def test_table_selection_with_kb_routes_hybrid_scope():
    decision = resolver.resolve(
        question="请结合知识库分析这个表格",
        pdf_context={
            "selected_ids": [11, 22, 33],
            "newly_uploaded_ids": [11, 22, 33],
            "all_available_ids": [11, 22, 33],
        },
        available_files=[PDF, PDF_2, TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert decision.route == "hybrid_qa"
    assert decision.selected_file_ids == [11, 22, 33]
    assert decision.strategy == "explicit_selection"
    assert decision.turn_mode == "mixed"
    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "pdf+table+kb"
    assert routed.kb_enabled is True


def test_filename_focus_routes_selected_pdf_to_pdf_qa():
    decision = resolver.resolve(
        question="solid-state-review 这篇文章的结论是什么？",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    assert decision.route == "pdf_qa"
    assert decision.strategy == "explicit_selection"


def test_mixed_file_types_require_selection():
    decision = resolver.resolve(
        question="请比较前两个文件",
        pdf_context={"all_available_ids": [11, 33], "selected_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )
    assert decision.route == "hybrid_qa"
    routed = router.decide(requested_mode="thinking", file_context=decision)
    assert routed.source_scope == "pdf+table"
    assert routed.kb_enabled is False


def test_hybrid_pdf_table_with_kb_requires_selection():
    decision = resolver.resolve(
        question="请结合知识库比较前两个文件",
        pdf_context={"all_available_ids": [11, 33], "selected_ids": [11, 33]},
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


def test_hybrid_file_only_turn_with_pdf_and_table_requires_selection():
    decision = resolver.resolve(
        question="请比较前两个文件",
        pdf_context={"all_available_ids": [11, 33], "selected_ids": [11, 33]},
        available_files=[PDF, TABLE],
    )
    routed = router.decide(requested_mode="thinking", file_context=decision)

    assert routed.route == "hybrid_qa"
    assert routed.source_scope == "pdf+table"
    assert routed.kb_enabled is False
    assert routed.selected_file_ids == [11, 33]
    assert routed.primary_file_id is None


def test_hybrid_mixed_turn_with_pdf_and_table_requires_selection():
    decision = resolver.resolve(
        question="请结合知识库比较前两个文件",
        pdf_context={"all_available_ids": [11, 33], "selected_ids": [11, 33]},
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

def test_last_focus_ids_explicit_empty_without_selection_stays_kb():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_ids": [],
            "last_focus_file_ids": [22],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )
    assert decision.needs_clarification is False
    assert decision.strategy == "none"


def test_last_focus_file_ids_alias_without_selection_stays_kb():
    decision = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_file_ids": [22],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )
    assert decision.needs_clarification is False
    assert decision.selected_file_ids == []
    assert decision.strategy == "none"
    assert decision.route == "kb_qa"
