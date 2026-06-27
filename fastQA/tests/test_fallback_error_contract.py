"""Fallback / upstream error contract tests (examples 9/11/13/14/15)."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.modules.generation_pipeline.stage2_retrieval import run_stage2_targeted_retrieval
from app.modules.qa_kb.orchestrators.generation import GenerationPipelineOrchestrator
from app.routers import qa as qa_router_module
from app.utils.upstream_errors import UpstreamCallError, build_sse_error_event


class _Expert:
    def __init__(self, *, fail: bool = False, empty: bool = False) -> None:
        self.fail = fail
        self.empty = empty
        self.calls: list[dict[str, object]] = []

    def search(self, query: str, **kwargs):
        self.calls.append({"query": query, **kwargs})
        if self.fail:
            raise RuntimeError("embedding service unavailable HTTP 503")
        if self.empty:
            return {"documents": [], "metadatas": [], "distances": [], "rerank": {}}
        return {
            "documents": ["doc"],
            "metadatas": [{"doi": "10.1/example"}],
            "distances": [0.1],
            "rerank": {},
        }


class _FakeClient:
    def __init__(self, *, response_text: str = "query text") -> None:
        self.response_text = response_text
        self.chat = type(
            "Chat",
            (),
            {
                "completions": type(
                    "Completions",
                    (),
                    {"create": self._create},
                )()
            },
        )()

    def _create(self, **kwargs):
        return type(
            "Resp",
            (),
            {"choices": [type("Choice", (), {"message": type("Msg", (), {"content": self.response_text})()})]},
        )()


class _Runtime:
    def __init__(self, *, stage1_payload: dict, stage2_payload: dict, doi_payload: list[str], stage4_payload: list) -> None:
        self.stage1_payload = stage1_payload
        self.stage2_payload = stage2_payload
        self.doi_payload = doi_payload
        self.stage4_payload = stage4_payload
        self.model = "qwen-test"
        self.stage1_prompt = "prompt"

    def _get_vector_db_context_for_prompt(self) -> str:
        return "context"

    def stage1_pre_answer_and_planning(self, user_question: str) -> dict:
        return dict(self.stage1_payload)

    def stage2_targeted_retrieval(self, retrieval_claims, n_results_per_claim=10, user_question=None, should_cancel=None, active_stream_count=None) -> dict:
        return dict(self.stage2_payload)

    def stage25_md_expansion(self, *, retrieval_results: dict, user_question: str, dois: list[str]) -> dict:
        return {"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}}

    def _extract_dois_from_results(self, retrieval_results: dict) -> list[str]:
        return list(self.doi_payload)

    def stage3_load_pdf_chunks(self, dois, max_chunks_per_doi=3, should_cancel=None):
        return {"10.1": [{"text": "evidence"}]}

    def stage4_synthesis_with_pdf_chunks(self, user_question, deep_answer, pdf_chunks, retrieval_results=None, should_cancel=None, conversation_context=None):
        for item in self.stage4_payload:
            yield item


def _logger():
    return logging.getLogger("test.fallback_error_contract")


def test_upstream_error_builds_structured_sse_frame():
    err = UpstreamCallError.llm_unavailable(stage="stage1", status_code=503)
    event = build_sse_error_event(err, trace_id="trace-1")
    assert event["type"] == "error"
    assert event["code"] == "LLM_UNAVAILABLE"
    assert event["status_code"] == 503
    assert event["failure_stage"] == "stage1"
    assert event["component"] == "llm"
    assert "503" in event["message"]


def test_example09_stage1_llm_failure_emits_structured_error():
    upstream = UpstreamCallError.llm_unavailable(stage="stage1", status_code=503)
    runtime = _Runtime(
        stage1_payload={"success": False, "error": "llm down", "upstream_error": upstream.to_dict()},
        stage2_payload={"success": True, "documents": [], "metadatas": [], "distances": []},
        doi_payload=[],
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    error_events = [event for event in events if event.get("type") == "error"]
    done_events = [event for event in events if event.get("type") == "done"]
    content_events = [event for event in events if event.get("type") == "content"]
    assert error_events
    assert error_events[0]["code"] == "LLM_UNAVAILABLE"
    assert error_events[0]["status_code"] == 503
    assert error_events[0]["failure_stage"] == "stage1"
    assert not done_events
    assert not content_events


def test_example11_all_claims_hard_fail_emits_error_no_fallback():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={
            "success": False,
            "hard_failure": True,
            "error": "embedding service unavailable HTTP 503",
            "upstream_error": UpstreamCallError.embedding_unavailable(stage="stage2", status_code=503).to_dict(),
        },
        doi_payload=[],
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    error_events = [event for event in events if event.get("type") == "error"]
    done_events = [event for event in events if event.get("type") == "done"]
    assert error_events
    assert error_events[0]["code"] == "EMBEDDING_UNAVAILABLE"
    assert error_events[0]["failure_stage"] == "stage2"
    assert not done_events
    assert not any(event.get("type") == "content" for event in events)


def test_example10_stage1_json_invalid_emits_error_no_fallback():
    upstream = UpstreamCallError.stage1_json_invalid()
    runtime = _Runtime(
        stage1_payload={"success": False, "error": upstream.error, "upstream_error": upstream.to_dict()},
        stage2_payload={"success": True, "documents": [], "metadatas": [], "distances": []},
        doi_payload=[],
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    error_events = [event for event in events if event.get("type") == "error"]
    assert error_events
    assert error_events[0]["code"] == "STAGE1_JSON_INVALID"
    assert error_events[0]["message"] == "大模型输出 json 不规范，请重试"
    assert not any(event.get("type") == "done" for event in events)


def test_example21_no_retrieval_claims_emits_error_no_fallback():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": []},
        stage2_payload={"success": True, "documents": [], "metadatas": [], "distances": []},
        doi_payload=[],
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    error_events = [event for event in events if event.get("type") == "error"]
    assert error_events
    assert error_events[0]["code"] == "STAGE1_NO_RETRIEVAL_CLAIMS"
    assert error_events[0]["message"] == "大模型未输出检索词，请重试"
    assert not any(event.get("type") == "done" for event in events)


def test_example11_neg_empty_results_without_exception_emits_error():
    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "lfp capacity"}],
        n_results_per_claim=3,
        user_question="lfp capacity",
        literature_expert=_Expert(empty=True),
        logger=_logger(),
        client=_FakeClient(),
        model="qwen-test",
        preprocess_retrieval_query_fn=lambda value: value,
    )
    assert result["success"] is True
    assert result["unique_count"] == 0

    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload=result,
        doi_payload=[],
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    error_events = [event for event in events if event.get("type") == "error"]
    done_events = [event for event in events if event.get("type") == "done"]
    assert error_events
    assert error_events[0]["code"] == "STAGE2_NO_DOI"
    assert error_events[0]["message"] == "metadata 无 doi，请重试"
    assert not done_events
    assert not any(event.get("type") == "content" for event in events)


def test_example13_rerank_fallback_emits_warning_step_with_reason():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={
            "success": True,
            "documents": ["doc"],
            "metadatas": [{"doi": "10.1"}],
            "distances": [0.1],
            "claim_to_results": {
                "x": {
                    "documents": ["doc"],
                    "metadatas": [{"doi": "10.1"}],
                    "distances": [0.1],
                    "rerank": {
                        "enabled": True,
                        "applied": False,
                        "fallback": True,
                        "reason": "request_failed",
                        "status_code": 503,
                    },
                }
            },
        },
        doi_payload=["10.1"],
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "kb_qa", "references": []}],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    warning_steps = [
        event
        for event in events
        if event.get("type") == "step" and event.get("step") == "stage2_rerank_fallback" and event.get("status") == "warning"
    ]
    assert warning_steps
    assert warning_steps[0]["data"]["code"] == "RERANK_DEGRADED"
    assert warning_steps[0]["data"]["reason"] == "request_failed"
    assert warning_steps[0]["data"]["status_code"] == 503
    assert events[-1]["type"] == "done"


def test_example14_stage4_fail_emits_error_no_fallback():
    upstream = UpstreamCallError.llm_unavailable(stage="stage4", status_code=502)
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage4_payload=["partial", {"success": False, "error": "stream failed", "upstream_error": upstream.to_dict()}],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    error_events = [event for event in events if event.get("type") == "error"]
    done_events = [event for event in events if event.get("type") == "done"]
    assert error_events
    assert error_events[0]["code"] == "LLM_UNAVAILABLE"
    assert error_events[0]["failure_stage"] == "stage4"
    assert error_events[0]["status_code"] == 502
    assert not done_events


def test_stage2_all_claims_hard_fail_sets_structured_upstream_error():
    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "lfp"}, {"claim": "nmc"}],
        n_results_per_claim=3,
        user_question="compare lfp and nmc",
        literature_expert=_Expert(fail=True),
        logger=_logger(),
        client=_FakeClient(),
        model="qwen-test",
        preprocess_retrieval_query_fn=lambda value: value,
    )
    assert result["success"] is False
    assert result.get("hard_failure") is True
    assert result.get("upstream_error", {}).get("code") == "EMBEDDING_UNAVAILABLE"


def test_example15_graph_kb_fail_step_detail_with_exc_summary():
    event = qa_router_module._graph_retrieval_step_event(
        status="error",
        mode="error",
        diagnostics={},
        error="neo4j timeout",
    )
    assert event["status"] == "error"
    assert event["error"] == "neo4j timeout"
    assert "neo4j timeout" in event["detail"]


def test_stage1_planning_returns_structured_upstream_error_on_llm_failure():
    from app.modules.generation_pipeline.stage1_planning import run_stage1_pre_answer_and_planning

    class _FailingClient:
        def __init__(self) -> None:
            self.chat = type(
                "Chat",
                (),
                {
                    "completions": type(
                        "Completions",
                        (),
                        {"create": self._create},
                    )()
                },
            )()

        def _create(self, **kwargs):
            exc = httpx.HTTPStatusError(
                "service unavailable",
                request=httpx.Request("POST", "http://llm/v1/chat/completions"),
                response=httpx.Response(503, request=httpx.Request("POST", "http://llm/v1/chat/completions")),
            )
            exc.status_code = 503
            raise exc

    result = run_stage1_pre_answer_and_planning(
        user_question="hello",
        stage1_prompt="prompt",
        vector_db_context="",
        client=_FailingClient(),
        model="qwen-test",
        logger=_logger(),
    )
    assert result["success"] is False
    assert result.get("upstream_error", {}).get("code") == "LLM_UNAVAILABLE"
    assert result.get("upstream_error", {}).get("status_code") == 503
    assert result.get("upstream_error", {}).get("stage") == "stage1"
