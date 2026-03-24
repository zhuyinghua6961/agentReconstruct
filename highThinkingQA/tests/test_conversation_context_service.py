from server.schemas.request_models import AskRequest
from server.services.conversation_context_service import build_conversation_context


def test_build_conversation_context_recent_turns_keep_only_role_and_content(monkeypatch):
    monkeypatch.setattr(
        "server.services.conversation_context_service._load_server_context_snapshot",
        lambda **kwargs: (
            [
                {
                    "role": "user",
                    "content": " 第一问 ",
                    "trace_id": "trace-u1",
                    "timings": {"total": 1.2},
                },
                {
                    "role": "assistant",
                    "content": " 第一答 ",
                    "steps": [{"name": "draft"}],
                    "source_usage": [{"doi": "10.1000/demo"}],
                },
                {
                    "role": "system",
                    "content": "should-drop",
                    "trace_id": "trace-sys",
                },
            ],
            {},
        ),
    )

    context = build_conversation_context(
        request=AskRequest(
            question="第三问",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[
                {
                    "role": "assistant",
                    "content": " 第二答 ",
                    "file_selection": {"picked": ["paper-a"]},
                    "trace_id": "trace-a2",
                }
            ],
            options={},
        )
    )

    assert context.recent_turns == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "assistant", "content": "第二答"},
    ]
    assert all(set(turn.keys()) == {"role", "content"} for turn in context.recent_turns)



def test_build_conversation_context_filters_summary_for_prompt_facing_use(monkeypatch):
    monkeypatch.setattr(
        "server.services.conversation_context_service._load_server_context_snapshot",
        lambda **kwargs: (
            [],
            {
                "topic": "磷酸铁锂",
                "recent_focus": "低温性能",
                "user_goal": "分析冬季衰减原因",
                "open_questions": ["那它冬天呢"],
                "updated_at": "2026-03-17T10:00:00+08:00",
                "steps": [{"name": "retrieve"}],
                "timings": {"total_ms": 123},
                "file_selection": {"picked": ["paper-a"]},
                "source_usage": [{"doi": "10.1000/demo"}],
                "trace_id": "trace-1",
            },
        ),
    )

    context = build_conversation_context(
        request=AskRequest(
            question="那它冬天呢",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert context.summary["topic"] == "磷酸铁锂"
    assert context.summary["recent_focus"] == "低温性能"
    assert context.summary["user_goal"] == "分析冬季衰减原因"
    assert context.summary["open_questions"] == ["那它冬天呢"]
    assert context.summary["updated_at"] == "2026-03-17T10:00:00+08:00"
    assert "steps" not in context.summary
    assert "timings" not in context.summary
    assert "file_selection" not in context.summary
    assert "source_usage" not in context.summary
    assert "trace_id" not in context.summary


def test_build_conversation_context_maps_public_service_short_summary_to_recent_focus(monkeypatch):
    monkeypatch.setattr(
        "server.services.conversation_context_service._load_server_context_snapshot",
        lambda **kwargs: (
            [],
            {
                "short_summary": "最近在讨论厚电极的液相浓差极化。",
                "memory_facts": [],
                "open_threads": ["高倍率下为什么更严重"],
            },
        ),
    )

    context = build_conversation_context(
        request=AskRequest(
            question="那为什么高倍率更严重？",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert context.summary["short_summary"] == "最近在讨论厚电极的液相浓差极化。"
    assert context.summary["recent_focus"] == "最近在讨论厚电极的液相浓差极化。"
    assert context.summary["open_threads"] == ["高倍率下为什么更严重"]
