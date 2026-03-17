from __future__ import annotations

import asyncio
import threading

from agent_core.graph import _run_pre_answer_retrieval_pipeline, run_agent


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


def test_run_agent_emits_step3_retrieval_progress(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1", "q2"])
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft")
    monkeypatch.setattr("agent_core.graph.config.RETRIEVAL_PIPELINE_BATCH_SIZE", 1)

    async def fake_iter_pre_answers_async(sub_questions, async_client=None):
        for index, _question in enumerate(sub_questions):
            yield index, f"a{index + 1}"
            await asyncio.sleep(0)

    monkeypatch.setattr("agent_core.graph.iter_pre_answers_async", fake_iter_pre_answers_async)
    monkeypatch.setattr(
        "agent_core.graph.batch_retrieve",
        lambda queries, retrieval_top_k, collection, embedding_client: [[object()] for _ in queries],
    )

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=0)

    assert state.error == ""
    step3_events = [item for item in progress_events if item["stage"] == "step3"]
    assert step3_events
    assert any(item["data"]["submitted_batches"] == 1 for item in step3_events if "submitted_batches" in item.get("data", {}))
    assert any(item["data"]["completed_batches"] == 2 for item in step3_events if "completed_batches" in item.get("data", {}))


def test_pre_answer_pipeline_flushes_partial_batch_after_wait(monkeypatch):
    monkeypatch.setattr("agent_core.graph._PARTIAL_RETRIEVAL_FLUSH_WAIT_SECONDS", 0.01)

    async def fake_iter_pre_answers_async(sub_questions, async_client=None):
        yield 0, "a1"
        await asyncio.sleep(0)
        yield 1, "a2"
        await asyncio.sleep(0)
        yield 2, "a3"
        await asyncio.sleep(0.03)
        yield 3, "a4"

    retrieval_calls = []

    def fake_batch_retrieve(queries, retrieval_top_k, collection, embedding_client):
        retrieval_calls.append(list(queries))
        return [[] for _ in queries]

    monkeypatch.setattr("agent_core.graph.iter_pre_answers_async", fake_iter_pre_answers_async)
    monkeypatch.setattr("agent_core.graph.batch_retrieve", fake_batch_retrieve)

    progress_events = []
    sub_answers, retrieved_chunks, metrics = _run_pre_answer_retrieval_pipeline(
        sub_questions=["q1", "q2", "q3", "q4"],
        retrieval_top_k=3,
        async_llm_client=object(),
        collection=object(),
        embedding_client=object(),
        batch_size=2,
        progress_callback=progress_events.append,
        trace_id="req_test",
    )

    assert sub_answers == ["a1", "a2", "a3", "a4"]
    assert len(retrieved_chunks) == 4
    assert [len(batch) for batch in retrieval_calls] == [2, 1, 1]
    assert int(metrics["retrieval_total_batches"]) == 3

    step3_submit_events = [
        item for item in progress_events
        if item["stage"] == "step3" and "submitted_batches" in item.get("data", {})
    ]
    assert [item["data"]["submitted_batches"] for item in step3_submit_events] == [1, 2, 3]
