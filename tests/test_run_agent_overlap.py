from __future__ import annotations

import threading

from agent_core.graph import run_agent


def test_run_agent_starts_pipeline_after_decompose_without_waiting_for_direct(monkeypatch):
    pipeline_started = threading.Event()

    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())

    def fake_direct_answer(*args, **kwargs):
        if not pipeline_started.wait(timeout=0.5):
            raise AssertionError("pipeline did not start before direct answer completed")
        return "direct"

    monkeypatch.setattr("agent_core.graph.direct_answer", fake_direct_answer)
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1", "q2"])

    def fake_pipeline(**kwargs):
        pipeline_started.set()
        return ["a1", "a2"], [[], []], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}

    monkeypatch.setattr("agent_core.graph._run_pre_answer_retrieval_pipeline", fake_pipeline)
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft")

    state = run_agent("demo", max_check_loops=0)

    assert state.error == ""
    assert state.direct_answer == "direct"
    assert state.sub_questions == ["q1", "q2"]
    assert state.final_answer == "draft"
    assert state.timings["step5_check_total"] == 0
    assert state.timings["step5_revise_total"] == 0


def test_run_agent_uses_stage_specific_thinking_flags(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())

    calls = {}

    def fake_direct_answer(question, client=None, enable_thinking=None):
        calls["direct"] = enable_thinking
        return "direct"

    def fake_decompose_question(question, client=None, num_sub_questions=None, enable_thinking=None):
        calls["decompose"] = enable_thinking
        return ["q1"]

    monkeypatch.setattr("agent_core.graph.direct_answer", fake_direct_answer)
    monkeypatch.setattr("agent_core.graph.decompose_question", fake_decompose_question)
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft")

    state = run_agent("demo", max_check_loops=0)

    assert state.error == ""
    assert calls["direct"] is False
    assert calls["decompose"] is False


def test_run_agent_streams_draft_chunks_and_keeps_final_answer(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr(
        "agent_core.graph.synthesize_answer_stream",
        lambda **kwargs: iter(["draft-", "chunk"]),
    )

    streamed_chunks = []
    state = run_agent("demo", stream_callback=streamed_chunks.append, max_check_loops=0)

    assert state.error == ""
    assert streamed_chunks == ["draft-", "chunk"]
    assert state.draft_answer == "draft-chunk"
    assert state.final_answer == "draft-chunk"


def test_run_agent_emits_step5_check_and_revise_progress(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft")
    monkeypatch.setattr("agent_core.graph.check_answer", lambda **kwargs: (False, [{"problem": "bad citation"}]))
    monkeypatch.setattr("agent_core.graph.revise_answer", lambda **kwargs: "revised")

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=1)

    stages = [item["stage"] for item in progress_events]

    assert state.error == ""
    assert state.final_answer == "revised"
    assert "step5_check" in stages
    assert "step5_revise" in stages
    assert state.timings["step5_check_total"] >= 0
    assert state.timings["step5_revise_total"] >= 0
    assert state.timings["step5_issue_total"] == 1
    assert state.timings["step5_revise_rounds"] == 1
    assert "step5_check_loop_1" in state.timings
    assert "step5_revise_loop_1" in state.timings

    check_success = next(
        item for item in progress_events
        if item["stage"] == "step5_check" and item["status"] == "success"
    )
    revise_success = next(
        item for item in progress_events
        if item["stage"] == "step5_revise" and item["status"] == "success"
    )

    assert check_success["data"]["elapsed_seconds"] >= 0
    assert revise_success["data"]["elapsed_seconds"] >= 0
