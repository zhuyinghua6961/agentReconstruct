from __future__ import annotations

from server.services.conversation.conversation_summary_service import build_conversation_summary


def test_summary_keeps_previous_topic_for_ambiguous_followup():
    summary = build_conversation_summary(
        messages=[
            {"role": "user", "content": "介绍一下磷酸铁锂"},
            {"role": "assistant", "content": "它的优点包括安全性和寿命"},
            {"role": "user", "content": "那它的缺点呢"},
        ],
        previous_summary={
            "topic": "磷酸铁锂",
            "recent_focus": "优点",
            "user_goal": "理解优缺点",
        },
    )

    assert summary["topic"] == "磷酸铁锂"
    assert summary["open_questions"] == ["那它的缺点呢"]
    assert summary["recent_focus"] == "那它的缺点呢"


def test_summary_resets_topic_when_user_switches_to_new_self_contained_question():
    summary = build_conversation_summary(
        messages=[
            {"role": "user", "content": "介绍一下磷酸铁锂"},
            {"role": "assistant", "content": "它的优点包括安全性和寿命"},
            {"role": "user", "content": "对比一下三元锂和锂硫电池的差异"},
        ],
        previous_summary={
            "topic": "磷酸铁锂",
            "recent_focus": "优点",
            "user_goal": "理解优缺点",
            "known_facts": ["磷酸铁锂安全性较高"],
            "entities": ["磷酸铁锂"],
        },
    )

    assert summary["topic"] == "对比一下三元锂和锂硫电池的差异"
    assert summary["user_goal"] == "对比一下三元锂和锂硫电池的差异"
    assert summary["open_questions"] == ["对比一下三元锂和锂硫电池的差异"]
    assert summary["known_facts"] == []
    assert summary["entities"] == []
