from app.services.conversation_context_builder import build_conversation_context


def test_builder_merges_authority_and_request_history_with_overlap_and_budget():
    context = build_conversation_context(
        current_question="第二问",
        request_chat_history=[
            {"role": "assistant", "content": "第一答", "trace_id": "trace-1"},
            {"role": "user", "content": "第二问", "trace_id": "trace-2"},
            {"type": "step", "content": "should be ignored"},
        ],
        authority_chat_history=[
            {"role": "user", "content": "第一问", "trace_id": "trace-u1"},
            {"role": "assistant", "content": "第一答", "trace_id": "trace-a1"},
        ],
        authority_summary={},
        authority_conversation_state={},
        source_scope="kb",
        selected_file_ids=[],
        used_files=[],
        execution_files=[],
        max_recent_turns=4,
        max_total_chars=100,
        max_message_chars=50,
    )

    assert context["recent_turns_for_llm"] == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
    ]


def test_builder_passes_summary_state_and_source_selection_through_standard_contract():
    context = build_conversation_context(
        current_question="结合文件回答",
        request_chat_history=[],
        authority_chat_history=[
            {"role": "assistant", "content": "上一轮回答", "trace_id": "trace-prev"},
        ],
        authority_summary={
            "short_summary": "之前讨论了循环寿命",
            "open_threads": ["倍率性能"],
            "memory_facts": ["材料是LFP"],
            "steps": [{"name": "should not leak"}],
            "timings": {"stage1": 12},
        },
        authority_conversation_state={"last_turn_route": "hybrid_qa", "last_focus_file_ids": [11]},
        source_scope="pdf+kb",
        selected_file_ids=[11, 12],
        used_files=[{"file_id": 11, "file_type": "pdf"}],
        execution_files=[{"file_id": 11, "file_type": "pdf", "local_path": "/tmp/a.pdf"}],
        primary_file_id=11,
    )

    assert context["summary_for_llm"] == {
        "short_summary": "之前讨论了循环寿命",
        "open_threads": ["倍率性能"],
        "memory_facts": ["材料是LFP"],
    }
    assert context["conversation_state"] == {"last_turn_route": "hybrid_qa", "last_focus_file_ids": [11]}
    assert context["source_selection"] == {
        "source_scope": "pdf+kb",
        "selected_file_ids": [11, 12],
        "used_files": [{"file_id": 11, "file_type": "pdf"}],
        "execution_files": [{"file_id": 11, "file_type": "pdf", "local_path": "/tmp/a.pdf"}],
        "primary_file_id": 11,
    }


def test_builder_clips_history_by_recent_turn_limit_before_returning_llm_context():
    context = build_conversation_context(
        current_question="第五问",
        request_chat_history=[],
        authority_chat_history=[
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
            {"role": "assistant", "content": "第二答"},
            {"role": "user", "content": "第三问"},
            {"role": "assistant", "content": "第三答"},
            {"role": "user", "content": "第四问"},
            {"role": "assistant", "content": "第四答"},
        ],
        authority_summary={},
        authority_conversation_state={},
        source_scope="kb",
        selected_file_ids=[],
        used_files=[],
        execution_files=[],
        max_recent_turns=3,
        max_total_chars=1000,
        max_message_chars=50,
    )

    assert context["recent_turns_for_llm"] == [
        {"role": "assistant", "content": "第三答"},
        {"role": "user", "content": "第四问"},
        {"role": "assistant", "content": "第四答"},
    ]
