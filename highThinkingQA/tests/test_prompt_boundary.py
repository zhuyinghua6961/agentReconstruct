from server.schemas.request_models import AskRequest
from server.services.ask_service import _prepare_execution, execute_ask
from server.services.conversation_context_service import ConversationContext


class _ImmediateFuture:
    def __init__(self, *, result):
        self._result = result

    def result(self, timeout=None):
        return self._result

    def done(self):
        return True


class _InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        return _ImmediateFuture(result=fn(*args, **kwargs))



def test_prepare_execution_filters_prompt_context_before_rewrite(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question="那它冬天呢",
            recent_turns=[
                {
                    "role": "user",
                    "content": "介绍磷酸铁锂",
                    "trace_id": "trace-u1",
                    "timings": {"total_ms": 5},
                },
                {
                    "role": "assistant",
                    "content": "它低温性能一般",
                    "source_usage": [{"doi": "10.1000/demo"}],
                },
            ],
            summary={
                "topic": "磷酸铁锂",
                "recent_focus": "低温性能",
                "user_goal": "分析冬季衰减原因",
                "updated_at": "2026-03-17T10:00:00+08:00",
                "steps": [{"name": "retrieve"}],
                "timings": {"total_ms": 123},
                "file_selection": {"picked": ["paper-a"]},
                "source_usage": [{"doi": "10.1000/demo"}],
                "trace_id": "trace-1",
                "short_summary": "最近在讨论低温性能",
                "open_threads": ["冬天衰减原因"],
                "memory_facts": ["体系是LFP"],
            },
            conversation_id=11,
            user_id=7,
        ),
    )
    monkeypatch.setattr(
        "server.services.ask_service.rewrite_question",
        lambda **kwargs: captured.update(kwargs)
        or type(
            "RewriteResult",
            (),
            {
                "raw_question": kwargs["raw_question"],
                "effective_question": kwargs["raw_question"],
                "rewrite_applied": False,
                "rewrite_reason": "self_contained",
            },
        )(),
    )

    _prepare_execution(
        AskRequest(
            question="那它冬天呢",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert captured["recent_turns"] == [
        {"role": "user", "content": "介绍磷酸铁锂"},
        {"role": "assistant", "content": "它低温性能一般"},
    ]
    assert captured["summary"] == {
        "short_summary": "最近在讨论低温性能",
        "recent_focus": "最近在讨论低温性能",
        "open_threads": ["冬天衰减原因"],
        "memory_facts": ["体系是LFP"],
    }



def test_execute_ask_passes_sanitized_prompt_context_to_agent(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()
    captured = {}

    def fake_run_agent(question, profile, **kwargs):
        captured["conversation_context"] = kwargs["conversation_context"]
        return state

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: _InlineExecutor())
    monkeypatch.setattr("server.services.ask_service._run_agent_for_profile", fake_run_agent)
    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question="那它冬天呢",
            recent_turns=[
                {
                    "role": "user",
                    "content": "介绍磷酸铁锂",
                    "trace_id": "trace-u1",
                    "steps": [{"name": "retrieve"}],
                },
                {
                    "role": "assistant",
                    "content": "它低温性能一般",
                    "timings": {"total_ms": 12},
                },
            ],
            summary={
                "topic": "磷酸铁锂",
                "recent_focus": "低温性能",
                "updated_at": "2026-03-17T10:00:00+08:00",
                "steps": [{"name": "retrieve"}],
                "timings": {"total_ms": 123},
                "file_selection": {"picked": ["paper-a"]},
                "source_usage": [{"doi": "10.1000/demo"}],
                "trace_id": "trace-1",
                "short_summary": "最近在讨论低温性能",
                "open_threads": ["冬天衰减原因"],
                "memory_facts": ["体系是LFP"],
            },
            conversation_id=11,
            user_id=7,
        ),
    )
    monkeypatch.setattr(
        "server.services.ask_service.rewrite_question",
        lambda **kwargs: type(
            "RewriteResult",
            (),
            {
                "raw_question": kwargs["raw_question"],
                "effective_question": kwargs["raw_question"],
                "rewrite_applied": False,
                "rewrite_reason": "self_contained",
            },
        )(),
    )

    execute_ask(
        request=AskRequest(
            question="那它冬天呢",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        ),
        timeout_seconds=10,
        trace_id="req_test",
    )

    assert captured["conversation_context"]["recent_turns"] == [
        {"role": "user", "content": "介绍磷酸铁锂"},
        {"role": "assistant", "content": "它低温性能一般"},
    ]
    assert captured["conversation_context"]["summary"] == {
        "short_summary": "最近在讨论低温性能",
        "recent_focus": "最近在讨论低温性能",
        "open_threads": ["冬天衰减原因"],
        "memory_facts": ["体系是LFP"],
    }



def test_prepare_execution_maps_public_service_short_summary_for_rewrite(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question="那为什么高倍率更严重？",
            recent_turns=[],
            summary={
                "short_summary": "最近在讨论厚电极的液相浓差极化。",
                "memory_facts": [],
                "open_threads": ["高倍率下为什么更严重"],
            },
            conversation_id=11,
            user_id=7,
        ),
    )
    monkeypatch.setattr(
        "server.services.ask_service.rewrite_question",
        lambda **kwargs: captured.update(kwargs)
        or type(
            "RewriteResult",
            (),
            {
                "raw_question": kwargs["raw_question"],
                "effective_question": kwargs["raw_question"],
                "rewrite_applied": False,
                "rewrite_reason": "self_contained",
            },
        )(),
    )

    _prepare_execution(
        AskRequest(
            question="那为什么高倍率更严重？",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert captured["summary"]["short_summary"] == "最近在讨论厚电极的液相浓差极化。"
    assert captured["summary"]["recent_focus"] == "最近在讨论厚电极的液相浓差极化。"

from agent_core.answer_summary import (
    apply_answer_summary_experiment as apply_thinking_answer_summary_experiment,
    build_summary_instruction as build_thinking_summary_instruction,
)
from agent_core.synthesizer import _build_synthesis_prompt


def test_highthinking_answer_summary_experiment_appends_summary_block_when_enabled():
    answer, meta = apply_thinking_answer_summary_experiment(
        "## 分析\n\n厚电极在高倍率下首先暴露的是液相传输受限问题，因为离子需要跨越更长的孔道并维持更陡的浓度梯度 [10.1/demo, Results]。\n\n当孔隙率、润湿性和曲折度没有同步优化时，电解液中的盐浓度会在极片厚度方向形成明显梯度，导致浓差极化快速累积 [10.1/demo, Discussion]。\n\n这类极化会进一步压缩可用反应区域，并使末端电压更早触底，因此表现为容量释放不足与倍率性能下滑。",
        enabled=True,
    )

    assert "\n\n## 总结\n\n- " in answer
    assert meta["generated"] is True
    assert meta["format"] == "bullet_fallback"


def test_highthinking_synthesis_prompt_includes_summary_instruction_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "agent_core.synthesizer.load_prompt_template",
        lambda _name: "Question: {question}\nDirect: {direct_answer}\nPassages: {retrieved_passages}",
    )

    prompt = _build_synthesis_prompt(
        question="为什么厚电极在高倍率下极化严重？",
        direct_answer="因为传质受限。",
        all_retrieved_chunks=[],
        sub_questions=[],
        summary_enabled=True,
    )

    assert "## 总结" in prompt
    assert build_thinking_summary_instruction(enabled=True).strip() in prompt
