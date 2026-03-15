import concurrent.futures

from server.services.ask_service import (
    execute_ask,
    stream_ask_events,
    _progress_to_step_event,
    _adapt_answer_for_frontend,
)
from server.schemas.request_models import AskRequest


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

    assert [frame["type"] for frame in frames] == ["metadata", "content", "done"]
    assert frames[1]["content"] == "draft-alpha"
    assert frames[2]["final_answer"] == "final-alpha [DOI: 10.1000/demo]"


def test_adapt_answer_for_frontend_converts_bracket_citations():
    assert _adapt_answer_for_frontend("A [10.1000/demo, Preamble] B") == "A [DOI: 10.1000/demo] B"


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

    assert [frame["type"] for frame in frames[:2]] == ["metadata", "progress"]


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

    assert [frame["type"] for frame in frames[:3]] == ["metadata", "progress", "step"]
    assert frames[2]["step"] == "step1"
    assert frames[2]["status"] == "processing"


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
