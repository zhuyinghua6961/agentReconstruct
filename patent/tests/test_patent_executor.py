from server.patent.executor import PatentExecutor
from server.schemas.request_models import PatentAskRequest



def _make_request(trace_id: str = "req_123") -> PatentAskRequest:
    return PatentAskRequest(
        question="Explain the novelty.",
        conversation_id=123,
        chat_history=[],
        requested_mode="patent",
        actual_mode="patent",
        route="kb_qa",
        source_scope=None,
        turn_mode="kb_only",
        kb_enabled=True,
        allow_kb_verification=False,
        used_files=[],
        execution_files=[],
        selected_file_ids=[],
        primary_file_id=None,
        file_selection={},
        trace_id=trace_id,
        options={},
    )



def test_stub_executor_returns_deterministic_patent_payload():
    executor = PatentExecutor()
    request = _make_request()
    context = {
        "trace_id": "req_123",
        "chat_history": [{"role": "user", "content": "Earlier turn"}],
        "summary": {"short_summary": "Earlier patent context"},
        "conversation_state": {"last_turn_route": "kb_qa"},
    }

    first = executor.execute(request=request, context=context)
    second = executor.execute(request=request, context=context)

    assert first == second
    assert first["answer_text"] == "Patent Phase 1 stub answer: Explain the novelty."
    assert first["route"] == "kb_qa"
    assert first["references"] == []
    assert first["steps"][0]["title"] == "Patent Stub"
    assert first["timings"]["stub_total_ms"] == 1
