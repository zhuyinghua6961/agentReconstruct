import concurrent.futures

from server.services.ask_service import (
    _build_reference_links,
    _extract_references,
    execute_ask,
    stream_ask_events,
    _progress_to_step_event,
    _adapt_answer_for_frontend,
)
from server.schemas.request_models import AskRequest
from server.services.conversation_context_service import ConversationContext, build_conversation_context
from server.services.query_rewrite_service import rewrite_question


class ImmediateFuture:
    def __init__(self, *, result=None, exc=None):
        self._result = result
        self._exc = exc

    def result(self, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._result

    def done(self):
        return True


class PendingFuture:
    def __init__(self):
        self.cancel_called = False

    def result(self, timeout=None):
        raise concurrent.futures.TimeoutError()

    def done(self):
        return False

    def cancel(self):
        self.cancel_called = True
        return True


class DummyExecutor:
    def __init__(self, future):
        self.future = future
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return self.future


class InlineExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return ImmediateFuture(result=fn(*args, **kwargs))


def test_build_conversation_context_uses_chat_persistence_snapshot(monkeypatch):
    authority_calls: list[dict] = []

    def fake_load_conversation_context(**kwargs):
        authority_calls.append(dict(kwargs))
        return {
            "chat_history": [
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回答"},
            ],
            "summary": {"topic": "authority-summary"},
            "conversation_state": {"last_turn_route": "thinking_qa"},
            "snapshot_version": 5,
            "pending_overlay": None,
        }

    monkeypatch.setattr(
        "server.services.conversation_context_service.chat_persistence",
        type("FakeChatPersistence", (), {"load_conversation_context": staticmethod(fake_load_conversation_context)})(),
        raising=False,
    )

    context = build_conversation_context(
        request=AskRequest(
            question="这一轮问题",
            mode="thinking",
            requested_mode="thinking",
            actual_mode="thinking",
            route="thinking_qa",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert authority_calls == [
        {
            "user_id": 7,
            "conversation_id": 11,
            "trace_id": "",
            "route": "thinking_qa",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "payload": None,
        }
    ]
    assert context.summary == {"topic": "authority-summary"}
    assert context.recent_turns == [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
    ]


def test_execute_ask_uses_shared_executor(monkeypatch):
    state = type("State", (), {"final_answer": "alpha [10.1000/demo, Preamble]", "timings": {"total": 0.1}, "error": ""})()
    executor = DummyExecutor(ImmediateFuture(result=state))

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: executor)

    result = execute_ask(
        request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
        timeout_seconds=10,
        trace_id="req_test",
    )

    assert executor.calls
    assert result["final_answer"] == "alpha [DOI: 10.1000/demo]"
    assert result["metadata"]["query_mode"] == "thinking"


def test_stream_ask_events_uses_shared_executor(monkeypatch):
    state = type("State", (), {"final_answer": "alpha [10.1000/demo, Preamble]", "timings": {"total": 0.1}, "error": ""})()
    executor = DummyExecutor(ImmediateFuture(result=state))

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: executor)

    frames = list(
        stream_ask_events(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=10,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert executor.calls
    assert frames[0]["type"] == "metadata"
    assert frames[1]["type"] == "step"
    assert frames[1]["step"] == "context_ready"
    assert frames[2]["type"] == "step"
    assert frames[2]["step"] == "rewrite_ready"
    assert frames[-1]["type"] == "done"
    assert frames[-1]["final_answer"] == "alpha [DOI: 10.1000/demo]"


def test_stream_ask_events_forwards_content_before_done(monkeypatch):
    state = type("State", (), {"final_answer": "final-alpha [10.1000/demo, Preamble]", "timings": {"total": 0.1}, "error": ""})()

    def fake_run_agent(question, profile, **callbacks):
        callbacks["stream_callback"]("draft-alpha")
        return state

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: InlineExecutor())
    monkeypatch.setattr("server.services.ask_service._run_agent_for_profile", fake_run_agent)

    frames = list(
        stream_ask_events(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=10,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert [frame["type"] for frame in frames] == ["metadata", "step", "step", "content", "done"]
    assert frames[3]["content"] == "draft-alpha"
    assert frames[4]["final_answer"] == "final-alpha [DOI: 10.1000/demo]"



def test_stream_ask_events_passes_sanitized_conversation_context_to_agent(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()
    captured = {}

    def fake_run_agent(question, profile, **kwargs):
        captured["conversation_context"] = kwargs["conversation_context"]
        return state

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: InlineExecutor())
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
                    "source_usage": [{"doi": "10.1000/demo"}],
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

    frames = list(
        stream_ask_events(
            request=AskRequest(
                question="那它冬天呢",
                mode="thinking",
                user_id=7,
                conversation_id=11,
                chat_history=[],
                options={},
            ),
            timeout_seconds=10,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert frames[-1]["type"] == "done"
    assert captured["conversation_context"]["recent_turns"] == [
        {"role": "user", "content": "介绍磷酸铁锂"},
        {"role": "assistant", "content": "它低温性能一般"},
    ]
    assert captured["conversation_context"]["summary"]["topic"] == "磷酸铁锂"
    assert "steps" not in captured["conversation_context"]["summary"]
    assert "timings" not in captured["conversation_context"]["summary"]
    assert "file_selection" not in captured["conversation_context"]["summary"]
    assert "source_usage" not in captured["conversation_context"]["summary"]
    assert "trace_id" not in captured["conversation_context"]["summary"]

def test_adapt_answer_for_frontend_converts_bracket_citations():
    assert _adapt_answer_for_frontend("A [10.1000/demo, Preamble] B") == "A [DOI: 10.1000/demo] B"


def test_extract_references_normalizes_polluted_doi_tokens():
    refs = _extract_references(
        "A [10.1007_s11581-021-04073-2, Results] and doi:10.1007/s11581-021-04073-2)."
    )

    assert refs == ["10.1007/s11581-021-04073-2"]


def test_build_reference_links_uses_normalized_doi():
    assert _build_reference_links(["10.1007_s11581-021-04073-2)."]) == [
        {
            "doi": "10.1007/s11581-021-04073-2",
            "pdf_url": "/api/v1/view_pdf/10.1007/s11581-021-04073-2",
        }
    ]


def test_stream_ask_events_adapts_doi_links_before_done(monkeypatch):
    state = type("State", (), {"final_answer": "tail", "timings": {"total": 0.1}, "error": ""})()

    def fake_run_agent(question, profile, **callbacks):
        callbacks["stream_callback"]("A [10.1000/demo")
        callbacks["stream_callback"](", Preamble] B")
        return state

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: InlineExecutor())
    monkeypatch.setattr("server.services.ask_service._run_agent_for_profile", fake_run_agent)

    frames = list(
        stream_ask_events(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=10,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert frames[0]["type"] == "metadata"
    assert frames[-1]["type"] == "done"
    content = "".join(frame["content"] for frame in frames if frame["type"] == "content")
    assert content == "A [DOI: 10.1000/demo] B"
    assert frames[-1]["metadata"]["route"] == "kb_qa"
    assert frames[-1]["doi_locations"] == []


def test_stream_ask_events_forwards_progress_events(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()

    class ProgressExecutor:
        def __init__(self):
            self.calls = []

        def submit(self, fn, *args, **kwargs):
            self.calls.append((fn, args, kwargs))
            kwargs["progress_callback"](
                {
                    "type": "progress",
                    "stage": "step1",
                    "status": "started",
                    "message": "开始执行直接回答与查询分解",
                }
            )
            return ImmediateFuture(result=state)

    executor = ProgressExecutor()
    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: executor)

    frames = list(
        stream_ask_events(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=10,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert [frame["type"] for frame in frames[:4]] == ["metadata", "step", "step", "step"]
    assert frames[3]["step"] == "step1"
    assert frames[3]["status"] == "processing"


def test_progress_is_mapped_to_frontend_compatible_step():
    event = _progress_to_step_event(
        {
            "type": "progress",
            "stage": "step2",
            "status": "running",
            "message": "开始执行子问题预回答与检索流水线",
            "data": {"completed": 2, "total": 5},
        }
    )

    assert event == {
        "type": "step",
        "step": "step2",
        "message": "阶段2：子问题预回答：已完成 2/5",
        "status": "processing",
        "data": {"completed": 2, "total": 5, "count": 2},
    }


def test_stream_ask_events_emits_heartbeat_while_waiting(monkeypatch):
    executor = DummyExecutor(PendingFuture())
    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: executor)

    frames = list(
        stream_ask_events(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=3,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert any(frame["type"] == "heartbeat" for frame in frames)
    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "UPSTREAM_TIMEOUT"


def test_progress_step1_message_is_frontend_friendly():
    event = _progress_to_step_event(
        {
            "type": "progress",
            "stage": "step1",
            "status": "running",
            "message": "查询分解完成，开始组织子问题",
            "data": {"sub_questions": 5},
        }
    )

    assert event["type"] == "step"
    assert event["step"] == "step1"
    assert event["status"] == "processing"
    assert event["message"] == "阶段1：查询分解完成，开始组织子问题"


def test_progress_step1_wait_message_is_frontend_friendly():
    event = _progress_to_step_event(
        {
            "type": "progress",
            "stage": "step1",
            "status": "running",
            "message": "子问题处理完成，等待直接回答收尾",
            "data": {"sub_questions": 2, "pre_answers": 2},
        }
    )

    assert event["type"] == "step"
    assert event["step"] == "step1"
    assert event["status"] == "processing"
    assert event["message"] == "阶段1：子问题处理完成，等待直接回答收尾"


def test_progress_step3_message_is_frontend_friendly():
    event = _progress_to_step_event(
        {
            "type": "progress",
            "stage": "step3",
            "status": "running",
            "message": "文献检索已完成 1/2 批",
            "data": {"completed_batches": 1, "total_batches": 2, "retrieved_chunks_total": 4},
        }
    )

    assert event == {
        "type": "step",
        "step": "step3",
        "message": "阶段3：文献检索：已完成 1/2 批",
        "status": "processing",
        "data": {"completed_batches": 1, "total_batches": 2, "retrieved_chunks_total": 4, "count": 1},
    }


def test_progress_step4_streaming_message_is_frontend_friendly():
    event = _progress_to_step_event(
        {
            "type": "progress",
            "stage": "step4",
            "status": "running",
            "message": "综合草稿开始流式输出",
            "data": {"chunk_index": 1},
        }
    )

    assert event["type"] == "step"
    assert event["step"] == "step4"
    assert event["status"] == "processing"
    assert event["message"] == "阶段4：综合草稿开始流式输出"


def test_progress_step5_check_message_is_frontend_friendly():
    event = _progress_to_step_event(
        {
            "type": "progress",
            "stage": "step5_check",
            "status": "started",
            "message": "开始第 1 轮引用检查",
            "data": {"check_loop": 1, "issues": 0},
        }
    )

    assert event["type"] == "step"
    assert event["step"] == "step5_check"
    assert event["status"] == "processing"
    assert event["message"] == "阶段5A：开始第 1 轮引用检查"
    assert event["data"]["count"] == 1


def test_progress_step5_revise_message_is_frontend_friendly():
    event = _progress_to_step_event(
        {
            "type": "progress",
            "stage": "step5_revise",
            "status": "started",
            "message": "开始第 1 轮问题修订",
            "data": {"check_loop": 1, "issues": 2},
        }
    )

    assert event["type"] == "step"
    assert event["step"] == "step5_revise"
    assert event["status"] == "processing"
    assert event["message"] == "阶段5B：开始第 1 轮问题修订（2 个问题）"
    assert event["data"]["count"] == 1


def test_stream_ask_events_emits_compatibility_step_for_progress(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()

    class ProgressExecutor:
        def submit(self, fn, *args, **kwargs):
            kwargs["progress_callback"](
                {
                    "type": "progress",
                    "stage": "step1",
                    "status": "started",
                    "message": "开始执行直接回答与查询分解",
                }
            )
            return ImmediateFuture(result=state)

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: ProgressExecutor())

    frames = list(
        stream_ask_events(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=10,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert [frame["type"] for frame in frames[:4]] == ["metadata", "step", "step", "step"]
    assert frames[3]["step"] == "step1"
    assert frames[3]["status"] == "processing"


def test_execute_ask_timeout_contract_still_raises(monkeypatch):
    executor = DummyExecutor(ImmediateFuture(exc=concurrent.futures.TimeoutError()))

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: executor)

    try:
        execute_ask(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=10,
            trace_id="req_test",
        )
    except Exception as exc:
        assert exc.__class__.__name__ == "AskTimeoutError"
    else:  # pragma: no cover
        raise AssertionError("expected AskTimeoutError")


def test_execute_ask_timeout_sets_cancel_event(monkeypatch):
    future = PendingFuture()
    executor = DummyExecutor(future)

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: executor)

    try:
        execute_ask(
            request=AskRequest(question="demo", mode="thinking", user_id=None, conversation_id=None, chat_history=[], options={}),
            timeout_seconds=10,
            trace_id="req_test",
        )
    except Exception as exc:
        assert exc.__class__.__name__ == "AskTimeoutError"
    else:  # pragma: no cover
        raise AssertionError("expected AskTimeoutError")

    submitted_kwargs = executor.calls[0][2]
    assert submitted_kwargs["cancel_event"].is_set() is True
    assert future.cancel_called is True


def test_build_conversation_context_uses_server_history_and_deduplicates_current_question(monkeypatch):
    monkeypatch.setattr(
        "server.services.conversation_context_service._load_server_context_snapshot",
        lambda **kwargs: [
            [
                {"role": "user", "content": "介绍磷酸铁锂的优点"},
                {"role": "assistant", "content": "它的优点包括安全性和寿命"},
                {"role": "user", "content": "那它的缺点呢"},
            ],
            {},
        ],
    )

    context = build_conversation_context(
        request=AskRequest(
            question="那它的缺点呢",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert context.user_id == 7
    assert context.conversation_id == 11
    assert context.raw_question == "那它的缺点呢"
    assert context.recent_turns == [
        {"role": "user", "content": "介绍磷酸铁锂的优点"},
        {"role": "assistant", "content": "它的优点包括安全性和寿命"},
    ]


def test_build_conversation_context_deduplicates_only_server_request_overlap(monkeypatch):
    monkeypatch.setattr(
        "server.services.conversation_context_service._load_server_context_snapshot",
        lambda **kwargs: [
            [
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "第一答"},
            ],
            {},
        ],
    )

    context = build_conversation_context(
        request=AskRequest(
            question="第二问",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[
                {"role": "assistant", "content": "第一答"},
                {"role": "user", "content": "第二问"},
            ],
            options={},
        )
    )

    assert context.recent_turns == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
    ]


def test_build_conversation_context_preserves_real_repeated_turns(monkeypatch):
    monkeypatch.setattr(
        "server.services.conversation_context_service._load_server_context_snapshot",
        lambda **kwargs: [
            [
                {"role": "user", "content": "这个材料稳定吗"},
                {"role": "assistant", "content": "常温下较稳定"},
                {"role": "user", "content": "这个材料稳定吗"},
                {"role": "assistant", "content": "在高温下需要区分条件"},
            ],
            {},
        ],
    )

    context = build_conversation_context(
        request=AskRequest(
            question="那高温下呢",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert context.recent_turns == [
        {"role": "user", "content": "这个材料稳定吗"},
        {"role": "assistant", "content": "常温下较稳定"},
        {"role": "user", "content": "这个材料稳定吗"},
        {"role": "assistant", "content": "在高温下需要区分条件"},
    ]


def test_build_conversation_context_loads_server_summary(monkeypatch):
    monkeypatch.setattr(
        "server.services.conversation_context_service._load_server_context_snapshot",
        lambda **kwargs: (
            [],
            {
                "topic": "磷酸铁锂",
                "recent_focus": "低温性能",
                "user_goal": "理解冬季衰减原因",
            },
        ),
    )

    context = build_conversation_context(
        request=AskRequest(
            question="那它冬天为什么衰减更明显",
            mode="thinking",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        )
    )

    assert context.summary["topic"] == "磷酸铁锂"
    assert context.summary["recent_focus"] == "低温性能"


def test_rewrite_question_uses_recent_turns_as_context_anchor():
    rewrite = rewrite_question(
        raw_question="那它的缺点呢",
        recent_turns=[
            {"role": "user", "content": "介绍磷酸铁锂的优点"},
            {"role": "assistant", "content": "它的优点包括安全性和寿命"},
        ],
        summary={},
    )

    assert rewrite.rewrite_applied is True
    assert rewrite.rewrite_reason == "contextual_reference"
    assert rewrite.anchor_text == "介绍磷酸铁锂的优点"
    assert rewrite.effective_question.endswith("回答这个问题：那它的缺点呢")


def test_rewrite_question_can_use_summary_without_recent_turns():
    rewrite = rewrite_question(
        raw_question="那它冬天呢",
        recent_turns=[],
        summary={
            "topic": "磷酸铁锂",
            "recent_focus": "低温性能",
            "user_goal": "分析冬季性能衰减",
        },
    )

    assert rewrite.rewrite_applied is True
    assert rewrite.rewrite_reason == "contextual_reference"
    assert "低温性能" in rewrite.effective_question


def test_rewrite_question_skips_short_but_self_contained_question():
    rewrite = rewrite_question(
        raw_question="解释SEI膜",
        recent_turns=[
            {"role": "user", "content": "介绍磷酸铁锂的优点"},
            {"role": "assistant", "content": "它的优点包括安全性和寿命"},
        ],
        summary={"topic": "磷酸铁锂"},
    )

    assert rewrite.rewrite_applied is False
    assert rewrite.effective_question == "解释SEI膜"
    assert rewrite.rewrite_reason == "self_contained"


def test_rewrite_question_prefers_recent_turns_over_conflicting_summary():
    rewrite = rewrite_question(
        raw_question="那成本呢",
        recent_turns=[
            {"role": "user", "content": "对比一下三元锂和锂硫电池的差异"},
            {"role": "assistant", "content": "它们在能量密度和循环寿命上差异明显"},
        ],
        summary={
            "topic": "磷酸铁锂",
            "recent_focus": "低温性能",
            "updated_at": "2099-03-17T10:00:00+08:00",
        },
    )

    assert rewrite.rewrite_applied is True
    assert rewrite.anchor_text == "对比一下三元锂和锂硫电池的差异"


def test_rewrite_question_ignores_stale_summary_without_recent_turns():
    rewrite = rewrite_question(
        raw_question="那它冬天呢",
        recent_turns=[],
        summary={
            "topic": "磷酸铁锂",
            "recent_focus": "低温性能",
            "updated_at": "2020-01-01T00:00:00+00:00",
        },
    )

    assert rewrite.rewrite_applied is False
    assert rewrite.effective_question == "那它冬天呢"
    assert rewrite.rewrite_reason == "no_context_anchor"


def test_execute_ask_passes_effective_question_to_agent(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()
    captured = {}

    def fake_run_agent(question, profile, **kwargs):
        captured["question"] = question
        captured["raw_question"] = kwargs.get("raw_question")
        captured["conversation_context"] = kwargs.get("conversation_context")
        return state

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: InlineExecutor())
    monkeypatch.setattr("server.services.ask_service._run_agent_for_profile", fake_run_agent)
    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question="那它的缺点呢",
            recent_turns=[
                {"role": "user", "content": "介绍磷酸铁锂的优点"},
                {"role": "assistant", "content": "它的优点包括安全性和寿命"},
            ],
            summary={},
            conversation_id=11,
            user_id=7,
        ),
    )

    result = execute_ask(
        request=AskRequest(question="那它的缺点呢", mode="thinking", user_id=7, conversation_id=11, chat_history=[], options={}),
        timeout_seconds=10,
        trace_id="req_test",
    )

    assert captured["question"].startswith("结合前文关于")
    assert captured["raw_question"] == "那它的缺点呢"
    assert captured["conversation_context"]["conversation_id"] == 11
    assert captured["conversation_context"]["user_id"] == 7
    assert result["metadata"]["raw_question"] == "那它的缺点呢"
    assert result["metadata"]["rewrite_applied"] is True
    assert result["metadata"]["summary_available"] is False


def test_execute_ask_falls_back_to_raw_question_when_rewrite_fails(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()
    captured = {}

    def fake_run_agent(question, profile, **kwargs):
        captured["question"] = question
        return state

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: InlineExecutor())
    monkeypatch.setattr("server.services.ask_service._run_agent_for_profile", fake_run_agent)
    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question="那它的缺点呢",
            recent_turns=[{"role": "user", "content": "介绍磷酸铁锂的优点"}],
            summary={},
            conversation_id=11,
            user_id=7,
        ),
    )
    monkeypatch.setattr(
        "server.services.ask_service.rewrite_question",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = execute_ask(
        request=AskRequest(question="那它的缺点呢", mode="thinking", user_id=7, conversation_id=11, chat_history=[], options={}),
        timeout_seconds=10,
        trace_id="req_test",
    )

    assert captured["question"] == "那它的缺点呢"
    assert result["metadata"]["effective_question"] == "那它的缺点呢"
    assert result["metadata"]["rewrite_applied"] is False
    assert result["metadata"]["rewrite_reason"] == "rewrite_failed"


def test_stream_ask_events_metadata_contains_rewrite_fields(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()
    executor = DummyExecutor(ImmediateFuture(result=state))

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: executor)
    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question="那它的缺点呢",
            recent_turns=[
                {"role": "user", "content": "介绍磷酸铁锂的优点"},
                {"role": "assistant", "content": "它的优点包括安全性和寿命"},
            ],
            summary={},
            conversation_id=11,
            user_id=7,
        ),
    )

    frames = list(
        stream_ask_events(
            request=AskRequest(question="那它的缺点呢", mode="thinking", user_id=7, conversation_id=11, chat_history=[], options={}),
            timeout_seconds=10,
            heartbeat_seconds=1,
            trace_id="req_test",
        )
    )

    assert frames[0]["type"] == "metadata"
    assert frames[0]["raw_question"] == "那它的缺点呢"
    assert frames[0]["rewrite_applied"] is True
    assert frames[0]["rewrite_reason"] == "contextual_reference"
    assert frames[0]["context_turns"] == 2
    assert frames[0]["summary_available"] is False
    assert frames[1]["step"] == "context_ready"
    assert frames[2]["step"] == "rewrite_ready"
    assert "介绍磷酸铁锂的优点" in frames[0]["effective_question"]


def test_execute_ask_metadata_marks_summary_available(monkeypatch):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: InlineExecutor())
    monkeypatch.setattr("server.services.ask_service._run_agent_for_profile", lambda question, profile, **kwargs: state)
    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question="那它冬天呢",
            recent_turns=[],
            summary={"topic": "磷酸铁锂", "updated_at": "2026-03-17T10:00:00+08:00"},
            conversation_id=11,
            user_id=7,
        ),
    )

    result = execute_ask(
        request=AskRequest(question="那它冬天呢", mode="thinking", user_id=7, conversation_id=11, chat_history=[], options={}),
        timeout_seconds=10,
        trace_id="req_test",
    )

    assert result["metadata"]["summary_available"] is True
    assert result["metadata"]["summary_updated_at"] == "2026-03-17T10:00:00+08:00"


def test_execute_ask_logs_runtime_diagnostics(monkeypatch, caplog):
    state = type("State", (), {"final_answer": "alpha", "timings": {"total": 0.1}, "error": ""})()

    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: InlineExecutor())
    monkeypatch.setattr(
        "server.services.ask_service.build_conversation_context",
        lambda request: ConversationContext(
            raw_question=request.question,
            recent_turns=[{"role": "user", "content": "上一轮问题"}],
            summary={"topic": "lfp"},
            conversation_id=11,
            user_id=7,
        ),
    )
    monkeypatch.setattr(
        "server.services.ask_service.rewrite_question",
        lambda question, conversation_context=None: type(
            "RewriteResult",
            (),
            {
                "effective_question": "rewrite-alpha",
                "rewritten": True,
                "summary_used": True,
                "reason": "summary_disambiguation",
            },
        )(),
    )
    monkeypatch.setattr(
        "server.services.ask_service._run_agent_for_profile",
        lambda question, profile, **kwargs: state,
    )
    monkeypatch.setattr(
        "server.services.ask_service._log_runtime_resource_snapshot",
        lambda **kwargs: None,
        raising=False,
    )

    caplog.set_level("INFO")
    result = execute_ask(
        request=AskRequest(
            question="demo",
            mode="thinking",
            requested_mode="thinking",
            actual_mode="thinking",
            route="thinking_qa",
            user_id=7,
            conversation_id=11,
            chat_history=[],
            options={},
        ),
        timeout_seconds=10,
        trace_id="req_test",
    )

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "execute_ask start" in joined
    assert "conversation_context ready" in joined
    assert "question_rewrite ready" in joined
    assert "execute_ask done" in joined
    assert result["final_answer"] == "alpha"
