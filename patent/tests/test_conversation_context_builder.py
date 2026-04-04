from __future__ import annotations

from server.schemas.request_models import PatentAskRequest
from server.services.conversation_context_builder import build_patent_conversation_context, merge_recent_turns, normalize_patent_conversation_context


def _make_request(*, chat_history: list[dict[str, object]] | None = None) -> PatentAskRequest:
    return PatentAskRequest(
        question="Explain the patent novelty",
        conversation_id=123,
        chat_history=list(chat_history or []),
        requested_mode="patent",
        actual_mode="patent",
        route="kb_qa",
        source_scope="kb",
        turn_mode="kb_only",
        kb_enabled=True,
        allow_kb_verification=False,
        used_files=[],
        execution_files=[],
        selected_file_ids=[],
        primary_file_id=None,
        file_selection={},
        trace_id="req_ctx",
        options={},
    )


def test_build_patent_conversation_context_normalizes_raw_persistence_payload():
    request = _make_request(
        chat_history=[
            {"role": "user", "content": "request-side duplicate turn"},
            {"role": "assistant", "content": "request-side answer"},
        ]
    )
    raw_context = {
        "chat_history": [
            {"role": "user", "content": "  Earlier   patent   question  "},
            {"role": "assistant", "content": "  Earlier   patent   answer  "},
            {"role": "system", "content": "ignored"},
            "ignored",
        ],
        "summary": {
            "short_summary": "  Prior patent comparison  ",
            "open_threads": ["  thermal safety  ", "", None],
            "memory_facts": ["  tables matter  ", ""],
        },
        "conversation_state": {
            "last_turn_route": "  kb_qa  ",
            "last_focus_file_ids": ["1", "1", "bad", 2],
            "ignored": "value",
        },
        "pending_overlay": {
            "trace_id": "req_overlay",
            "assistant_content": "already merged assistant content",
        },
        "pending_overlays": [
            {
                "trace_id": "req_overlay",
                "assistant_content": "already merged assistant content",
            }
        ],
        "snapshot": {"raw": True},
    }

    context = build_patent_conversation_context(request=request, raw_context=raw_context)

    assert context == {
        "recent_turns_for_llm": [
            {"role": "user", "content": "Earlier patent question"},
            {"role": "assistant", "content": "Earlier patent answer"},
            {"role": "user", "content": "request-side duplicate turn"},
            {"role": "assistant", "content": "request-side answer"},
        ],
        "summary_for_llm": {
            "short_summary": "Prior patent comparison",
            "open_threads": ["thermal safety"],
            "memory_facts": ["tables matter"],
        },
        "conversation_state": {
            "last_turn_route": "kb_qa",
            "last_focus_file_ids": [1, 2],
        },
        "source_selection": {
            "source_scope": "kb",
            "selected_file_ids": [],
        },
    }


def test_build_patent_conversation_context_excludes_raw_overlay_and_snapshot_bookkeeping():
    request = _make_request()
    raw_context = {
        "chat_history": [
            {"role": "user", "content": "Earlier turn"},
            {"role": "assistant", "content": "Merged assistant turn"},
        ],
        "summary": {},
        "conversation_state": {},
        "pending_overlay": {"trace_id": "req_overlay", "assistant_content": "Merged assistant turn"},
        "snapshot": {"recent_turns": [{"role": "assistant", "content": "Should not leak"}]},
    }

    context = build_patent_conversation_context(request=request, raw_context=raw_context)

    assert "pending_overlay" not in context
    assert "pending_overlays" not in context
    assert "snapshot" not in context
    assert context["recent_turns_for_llm"] == [
        {"role": "user", "content": "Earlier turn"},
        {"role": "assistant", "content": "Merged assistant turn"},
    ]


def test_merge_recent_turns_removes_overlapping_request_history_prefix():
    merged = merge_recent_turns(
        current_question="Current question",
        authority_chat_history=[
            {"role": "user", "content": "Earlier turn"},
            {"role": "assistant", "content": "Earlier answer"},
        ],
        request_chat_history=[
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Follow-up"},
        ],
    )

    assert merged == [
        {"role": "user", "content": "Earlier turn"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Follow-up"},
    ]


def test_normalize_patent_conversation_context_preserves_canonical_file_aware_source_scope():
    context = normalize_patent_conversation_context(
        recent_turns_for_llm=[],
        summary_for_llm={},
        conversation_state={},
        source_selection={"source_scope": "pdf+kb", "selected_file_ids": ["1", "bad", 1]},
    )

    assert context["source_selection"] == {
        "source_scope": "pdf+kb",
        "selected_file_ids": [1],
    }
