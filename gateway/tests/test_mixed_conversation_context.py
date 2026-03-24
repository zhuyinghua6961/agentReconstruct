from app.models.files import ConversationFileRow
from app.services.file_context_resolver import FileContextResolver
from app.services.route_decision import RouteDecisionService


resolver = FileContextResolver()
router = RouteDecisionService()

PDF = ConversationFileRow(file_id=11, file_type="pdf", file_name="solid-state-review.pdf")
PDF_2 = ConversationFileRow(file_id=22, file_type="pdf", file_name="battery-paper.pdf")


def test_mixed_conversation_context_preserves_kb_turn_and_reuses_last_focus_alias():
    kb_turn = resolver.resolve(
        question="磷酸铁锂电压范围是多少？",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    kb_routed = router.decide(requested_mode="thinking", file_context=kb_turn)

    mixed_turn = resolver.resolve(
        question="请结合知识库总结这篇文献",
        pdf_context={"selected_ids": [11]},
        available_files=[PDF],
    )
    mixed_routed = router.decide(requested_mode="thinking", file_context=mixed_turn)

    follow_up_turn = resolver.resolve(
        question="请继续总结这篇文献",
        pdf_context={
            "all_available_ids": [11, 22],
            "last_focus_file_ids": [11],
            "last_turn_route": "pdf_qa",
        },
        available_files=[PDF, PDF_2],
    )
    follow_up_routed = router.decide(requested_mode="thinking", file_context=follow_up_turn)

    assert kb_turn.route == "kb_qa"
    assert kb_routed.actual_mode == "thinking"

    assert mixed_turn.turn_mode == "mixed"
    assert mixed_routed.route == "hybrid_qa"
    assert mixed_routed.actual_mode == "fast"
    assert mixed_routed.source_scope == "pdf+kb"

    assert follow_up_turn.strategy == "last_focus"
    assert follow_up_turn.selected_file_ids == [11]
    assert follow_up_turn.route == "pdf_qa"
    assert follow_up_routed.actual_mode == "fast"
