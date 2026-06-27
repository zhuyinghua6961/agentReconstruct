"""Fallback / upstream error contract tests (examples 2/3/5/6/7)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_core.graph import AgentState, run_agent
from server.services.ask_service import stream_ask_events
from server.utils.upstream_errors import UpstreamCallError, build_sse_error_event


def test_upstream_error_builds_structured_sse_frame():
    err = UpstreamCallError.llm_unavailable(stage="decompose", status_code=503)
    event = build_sse_error_event(err, trace_id="trace-1")
    assert event["type"] == "error"
    assert event["code"] == "LLM_UNAVAILABLE"
    assert event["status_code"] == 503
    assert event["failure_stage"] == "decompose"
    assert event["component"] == "llm"
    assert "503" in event["message"]


def test_example03_decompose_llm_failure_emits_structured_error(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")

    def fail_decompose(*args, **kwargs):
        raise UpstreamCallError.llm_unavailable(stage="decompose", status_code=503)

    monkeypatch.setattr("agent_core.graph.decompose_question", fail_decompose)

    state = run_agent("demo question", max_check_loops=0)
    assert state.final_answer == ""
    assert state.upstream_error is not None
    assert state.upstream_error["code"] == "LLM_UNAVAILABLE"
    assert state.upstream_error["status_code"] == 503


def test_example02_direct_answer_timeout_keeps_placeholder_and_warning_step(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[object()]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "final draft")

    def raise_timeout(*args, **kwargs):
        raise TimeoutError("direct answer timed out")

    monkeypatch.setattr("agent_core.graph.direct_answer", raise_timeout)

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=0)

    assert state.error == ""
    assert "直接回答超时，已改用检索结果生成答案。" in state.direct_answer
    warning_events = [
        item for item in progress_events
        if item.get("stage") == "step1" and item.get("status") == "warning"
    ]
    assert warning_events
    assert warning_events[0]["data"].get("failure_stage") == "direct_answer"
    assert warning_events[0]["data"].get("code") == "UPSTREAM_TIMEOUT"


def test_example05_partial_sub_answer_failure_emits_warning_and_keeps_answer(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1", "q2"])
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "synthesized answer")
    monkeypatch.setattr("agent_core.graph.config.RETRIEVAL_PIPELINE_BATCH_SIZE", 1)

    async def fake_iter(sub_questions, async_client=None, **kwargs):
        yield 0, "a1", None
        yield 1, "", RuntimeError("sub answer failed")

    monkeypatch.setattr("agent_core.graph.iter_pre_answers_async", fake_iter)
    monkeypatch.setattr(
        "agent_core.graph.batch_retrieve",
        lambda queries, retrieval_top_k, collection, embedding_client: [[] for _ in queries],
    )

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=0)

    assert state.error == ""
    assert state.final_answer
    warnings = [item for item in progress_events if item.get("status") == "warning" and item.get("stage") == "step2"]
    assert any("Q2" in item.get("message", "") for item in warnings)


def test_example06_embedding_failure_emits_structured_error(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])

    async def fake_iter(sub_questions, async_client=None, **kwargs):
        yield 0, "a1", None

    monkeypatch.setattr("agent_core.graph.iter_pre_answers_async", fake_iter)

    def fail_batch_retrieve(*args, **kwargs):
        raise UpstreamCallError.embedding_unavailable(stage="retrieval", status_code=503)

    monkeypatch.setattr("agent_core.graph.batch_retrieve", fail_batch_retrieve)

    state = run_agent("demo", max_check_loops=0)
    assert state.final_answer == ""
    assert state.upstream_error is not None
    assert state.upstream_error["code"] == "EMBEDDING_UNAVAILABLE"


def test_example07_synthesize_stream_interrupt_emits_structured_error(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])

    async def fake_iter(sub_questions, async_client=None, **kwargs):
        yield 0, "a1", None

    monkeypatch.setattr("agent_core.graph.iter_pre_answers_async", fake_iter)
    monkeypatch.setattr(
        "agent_core.graph.batch_retrieve",
        lambda queries, retrieval_top_k, collection, embedding_client: [[] for _ in queries],
    )

    def interrupt_stream(**kwargs):
        yield "partial"
        raise UpstreamCallError.stream_interrupted(stage="stage4", status_code=502)

    monkeypatch.setattr("agent_core.graph.synthesize_answer_stream", interrupt_stream)

    streamed = []
    state = run_agent("demo", stream_callback=streamed.append, max_check_loops=0)
    assert state.final_answer == ""
    assert state.upstream_error is not None
    assert state.upstream_error["code"] == "UPSTREAM_STREAM_INTERRUPTED"


def test_stream_ask_events_fatal_error_skips_buffered_content(monkeypatch):
    from server.schemas.request_models import AskRequest

    class FakeState(AgentState):
        pass

    fake_state = FakeState()
    fake_state.error = "LLM 服务不可用（HTTP 503）"
    fake_state.upstream_error = UpstreamCallError.llm_unavailable(stage="decompose", status_code=503).to_dict()

    def fake_run_agent_for_profile(*args, **kwargs):
        stream_callback = kwargs.get("stream_callback")
        if stream_callback:
            stream_callback("partial content")
        return fake_state

    monkeypatch.setattr("server.services.ask_service._run_agent_for_profile", fake_run_agent_for_profile)
    monkeypatch.setattr("server.services.ask_service._get_agent_executor", lambda: SimpleNamespace(submit=lambda fn, *a, **k: SimpleNamespace(
        done=lambda: True,
        result=lambda: fn(*a, **k),
        add_done_callback=lambda cb: None,
        cancel=lambda: None,
    )))

    request = AskRequest(
        question="demo",
        mode="thinking",
        requested_mode="thinking",
        actual_mode="thinking",
        route="kb_qa",
        turn_mode="plain",
        conversation_id="c1",
        user_id="u1",
    )

    events = list(stream_ask_events(request=request, timeout_seconds=30, heartbeat_seconds=5, trace_id="t1"))
    error_events = [item for item in events if item.get("type") == "error"]
    done_events = [item for item in events if item.get("type") == "done"]
    assert error_events
    assert error_events[0]["code"] == "LLM_UNAVAILABLE"
    assert error_events[0]["status_code"] == 503
    assert not done_events
