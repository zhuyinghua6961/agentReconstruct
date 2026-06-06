from __future__ import annotations

import asyncio
import threading
import time

from agent_core.graph import _call_with_wall_clock_timeout, _run_pre_answer_retrieval_pipeline, run_agent
from retriever.vector_retriever import RetrievedChunk


def test_run_agent_starts_pipeline_after_decompose_without_waiting_for_direct(monkeypatch):
    pipeline_started = threading.Event()

    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
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


def test_run_agent_falls_back_when_direct_answer_times_out(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[object()]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft-answer")

    def raise_timeout(*args, **kwargs):
        raise TimeoutError("direct answer timed out")

    monkeypatch.setattr("agent_core.graph.direct_answer", raise_timeout)

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=0)

    assert state.error == ""
    assert state.final_answer == "draft-answer"
    assert state.direct_answer
    fallback_event = next(item for item in progress_events if item["stage"] == "step1" and item["status"] == "warning")
    assert "直接回答" in fallback_event["message"]
    assert fallback_event["data"]["fallback"] is True


def test_run_agent_uses_stage_specific_thinking_flags(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
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
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
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




def test_run_agent_streaming_synthesis_allows_stage4_thinking_request(monkeypatch):
    monkeypatch.setattr("agent_core.graph.config.LLM_THINKING_ENABLED", True, raising=False)
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )

    captured = {}

    def fake_synthesize_answer_stream(**kwargs):
        captured["enable_thinking"] = kwargs.get("enable_thinking")
        return iter(["draft"])

    monkeypatch.setattr("agent_core.graph.synthesize_answer_stream", fake_synthesize_answer_stream)

    streamed_chunks = []
    state = run_agent(
        "demo",
        stream_callback=streamed_chunks.append,
        max_check_loops=0,
        enable_thinking=True,
    )

    assert state.error == ""
    assert streamed_chunks == ["draft"]
    assert captured["enable_thinking"] is True


def test_run_agent_emits_step5_check_and_revise_progress(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
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
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1", "q2"])
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft")
    monkeypatch.setattr("agent_core.graph.config.RETRIEVAL_PIPELINE_BATCH_SIZE", 1)

    async def fake_iter_pre_answers_async(sub_questions, async_client=None, **kwargs):
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


def test_run_agent_emits_detailed_stage_diagnostic_logs(monkeypatch, caplog):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct answer")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1", "q2"])
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft answer")
    monkeypatch.setattr("agent_core.graph.check_answer", lambda **kwargs: (True, []))

    def fake_pipeline(**kwargs):
        return (
            ["a1", "a2"],
            [
                [
                    RetrievedChunk(
                        text="first retrieved text",
                        doi="10.1000/one",
                        title="Title One",
                        section_name="Intro",
                        chunk_index=7,
                        distance=0.11,
                    )
                ],
                [
                    RetrievedChunk(
                        text="second retrieved text",
                        doi="10.1000/two",
                        title="Title Two",
                        section_name="Methods",
                        chunk_index=8,
                        distance=0.22,
                    )
                ],
            ],
            {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2, "retrieval_total_batches": 1},
        )

    monkeypatch.setattr("agent_core.graph._run_pre_answer_retrieval_pipeline", fake_pipeline)
    caplog.set_level("INFO", logger="agent_core.graph")

    state = run_agent("demo", max_check_loops=1, trace_id="diag-trace")

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert state.error == ""
    assert "run_agent config" in joined
    assert "step1 sub_question detail index=1" in joined
    assert "step2 pre_answer detail index=1" in joined
    assert "step3 retrieval_query detail index=1" in joined
    assert "step3 chunk detail query_index=1 chunk_index=1" in joined
    assert "step4 synthesis input" in joined
    assert "step5 checker input loop=1" in joined
    assert "run_agent timing summary" in joined


def test_pre_answer_pipeline_flushes_partial_batch_after_wait(monkeypatch):
    monkeypatch.setattr("agent_core.graph._PARTIAL_RETRIEVAL_FLUSH_WAIT_SECONDS", 0.01)

    async def fake_iter_pre_answers_async(sub_questions, async_client=None, **kwargs):
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





def test_run_agent_uses_no_retry_client_for_checker_and_reviser(monkeypatch):
    clients = []

    def fake_get_llm_client(*, max_retries=None):
        client = {"max_retries": max_retries, "index": len(clients)}
        clients.append(client)
        return client

    monkeypatch.setattr("agent_core.graph.get_llm_client", fake_get_llm_client)
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[object()]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft-answer")

    seen = {}

    def fake_check_answer(**kwargs):
        seen["check"] = kwargs["client"]
        return False, [{"problem": "bad citation"}]

    def fake_revise_answer(**kwargs):
        seen["revise"] = kwargs["client"]
        return "revised-answer"

    monkeypatch.setattr("agent_core.graph.check_answer", fake_check_answer)
    monkeypatch.setattr("agent_core.graph.revise_answer", fake_revise_answer)

    state = run_agent("demo", max_check_loops=1)

    assert state.error == ""
    assert state.final_answer == "revised-answer"
    assert seen["check"]["max_retries"] == 0
    assert seen["revise"]["max_retries"] == 0


def test_run_agent_enforces_wall_clock_timeout_for_checker(monkeypatch):
    monkeypatch.setattr("agent_core.graph._CHECKER_WALL_CLOCK_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[object()]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft-answer")

    def slow_check_answer(**kwargs):
        time.sleep(0.05)
        return True, []

    monkeypatch.setattr("agent_core.graph.check_answer", slow_check_answer)

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=1)

    assert state.error == ""
    assert state.final_answer == "draft-answer"
    check_error = next(item for item in progress_events if item["stage"] == "step5_check" and item["status"] == "error")
    assert "超时" in check_error["message"]
    assert check_error["data"]["elapsed_seconds"] < 0.2

def test_run_agent_forces_output_when_checker_times_out(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[object()]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft-answer")

    def raise_timeout(**kwargs):
        raise TimeoutError("checker timed out")

    monkeypatch.setattr("agent_core.graph.check_answer", raise_timeout)

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=1)

    assert state.error == ""
    assert state.final_answer == "draft-answer"
    assert state.check_passed is False
    assert state.check_loops == 1
    check_error = next(item for item in progress_events if item["stage"] == "step5_check" and item["status"] == "error")
    assert "超时" in check_error["message"]



def test_run_agent_forces_output_when_reviser_times_out(monkeypatch):
    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph.direct_answer", lambda *args, **kwargs: "direct")
    monkeypatch.setattr("agent_core.graph.decompose_question", lambda *args, **kwargs: ["q1"])
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[object()]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft-answer")
    monkeypatch.setattr("agent_core.graph.check_answer", lambda **kwargs: (False, [{"problem": "bad citation"}]))

    def raise_timeout(**kwargs):
        raise TimeoutError("reviser timed out")

    monkeypatch.setattr("agent_core.graph.revise_answer", raise_timeout)

    progress_events = []
    state = run_agent("demo", progress_callback=progress_events.append, max_check_loops=1)

    assert state.error == ""
    assert state.final_answer == "draft-answer"
    assert state.check_passed is False
    revise_error = next(item for item in progress_events if item["stage"] == "step5_revise" and item["status"] == "error")
    assert "超时" in revise_error["message"]


def test_run_agent_cancel_while_waiting_for_collection_does_not_wait_for_threadpool_shutdown(monkeypatch):
    cancel_event = threading.Event()
    collection_started = threading.Event()
    direct_started = threading.Event()
    decompose_started = threading.Event()

    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())

    def slow_collection():
        collection_started.set()
        time.sleep(1.0)
        return object()

    def slow_direct(*args, **kwargs):
        direct_started.set()
        time.sleep(1.0)
        return "direct"

    def fast_decompose(*args, **kwargs):
        decompose_started.set()
        return ["q1"]

    monkeypatch.setattr("agent_core.graph.get_or_create_collection", slow_collection)
    monkeypatch.setattr("agent_core.graph.direct_answer", slow_direct)
    monkeypatch.setattr("agent_core.graph.decompose_question", fast_decompose)
    monkeypatch.setattr(
        "agent_core.graph._run_pre_answer_retrieval_pipeline",
        lambda **kwargs: (["a1"], [[]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}),
    )

    def trigger_cancel():
        collection_started.wait(timeout=0.5)
        decompose_started.wait(timeout=0.5)
        cancel_event.set()

    cancel_thread = threading.Thread(target=trigger_cancel, daemon=True)
    cancel_thread.start()
    started_at = time.monotonic()
    state = run_agent("demo", cancel_event=cancel_event, max_check_loops=0)
    elapsed = time.monotonic() - started_at
    cancel_thread.join(timeout=0.5)

    assert direct_started.is_set()
    assert state.error == "cancelled"
    assert elapsed < 0.5


def test_wall_clock_timeout_helper_observes_cancel_without_waiting_for_worker_exit():
    cancel_event = threading.Event()
    worker_started = threading.Event()

    def slow_call():
        worker_started.set()
        time.sleep(1.0)
        return "late"

    def trigger_cancel():
        worker_started.wait(timeout=0.5)
        time.sleep(0.05)
        cancel_event.set()

    cancel_thread = threading.Thread(target=trigger_cancel, daemon=True)
    cancel_thread.start()
    started_at = time.monotonic()
    try:
        _call_with_wall_clock_timeout(
            func=slow_call,
            timeout_seconds=5.0,
            timeout_error=TimeoutError("timed out"),
            cancel_event=cancel_event,
            cancel_error=RuntimeError("cancelled"),
        )
    except RuntimeError as exc:
        assert str(exc) == "cancelled"
    else:  # pragma: no cover
        raise AssertionError("expected cancellation")
    elapsed = time.monotonic() - started_at
    cancel_thread.join(timeout=0.5)

    assert elapsed < 0.5


def test_pre_answer_retrieval_pipeline_observes_cancel_while_retrieval_is_blocked(monkeypatch):
    cancel_event = threading.Event()
    retrieval_started = threading.Event()

    async def fake_iter_pre_answers_async(sub_questions, async_client=None, **kwargs):
        yield 0, "a1"

    def slow_batch_retrieve(*args, **kwargs):
        retrieval_started.set()
        time.sleep(1.0)
        return [[object()]]

    monkeypatch.setattr("agent_core.graph.iter_pre_answers_async", fake_iter_pre_answers_async)
    monkeypatch.setattr("agent_core.graph.batch_retrieve", slow_batch_retrieve)

    def trigger_cancel():
        retrieval_started.wait(timeout=0.5)
        time.sleep(0.05)
        cancel_event.set()

    cancel_thread = threading.Thread(target=trigger_cancel, daemon=True)
    cancel_thread.start()
    started_at = time.monotonic()
    try:
        _run_pre_answer_retrieval_pipeline(
            sub_questions=["q1"],
            retrieval_top_k=3,
            async_llm_client=object(),
            collection=object(),
            embedding_client=object(),
            batch_size=1,
            cancel_event=cancel_event,
            trace_id="req_cancel",
        )
    except RuntimeError as exc:
        assert str(exc) == "cancelled"
    else:  # pragma: no cover
        raise AssertionError("expected cancellation")
    elapsed = time.monotonic() - started_at
    cancel_thread.join(timeout=0.5)

    assert elapsed < 0.5
