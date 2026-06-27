"""Fallback / upstream error contract tests (examples 16/17/18/19/20)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from server.patent.answering import PatentAnswerBuilder
from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.orchestrators.generation import PatentGenerationOrchestrator
from server.patent.retrieval_models import PatentEvidence, PatentRetrievalOutcome
from server.patent.result_builder import PatentResultBuilder
from server.patent.stages.evidence_loading import run_stage3_load_patent_evidence
from server.patent.stages.planning import run_stage1_pre_answer_and_planning
from server.utils.upstream_errors import UpstreamCallError, build_sse_error_event


class _Logger:
    def info(self, *args, **kwargs):
        del args, kwargs

    def warning(self, *args, **kwargs):
        del args, kwargs

    def error(self, *args, **kwargs):
        del args, kwargs


def test_upstream_error_builds_structured_sse_frame():
    err = UpstreamCallError.llm_unavailable(stage="stage1", status_code=503)
    event = build_sse_error_event(err, trace_id="trace-1")
    assert event["type"] == "error"
    assert event["code"] == "LLM_UNAVAILABLE"
    assert event["status_code"] == 503
    assert event["failure_stage"] == "stage1"
    assert event["component"] == "llm"
    assert "503" in event["message"]


def test_example16_stage1_llm_failure_stops_pipeline(monkeypatch):
    class _BrokenClient:
        chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: (_ for _ in ()).throw(
                    httpx.HTTPStatusError(
                        "upstream unavailable",
                        request=httpx.Request("POST", "http://example.invalid/v1/chat/completions"),
                        response=httpx.Response(503, request=httpx.Request("POST", "http://example.invalid/v1/chat/completions")),
                    )
                )
            )
        )

    monkeypatch.setattr("server.patent.stages.planning.intent_detect_enabled", lambda: False)

    with pytest.raises(UpstreamCallError) as exc_info:
        run_stage1_pre_answer_and_planning(
            user_question="比较两款磷酸铁锂正极专利的差异",
            client=_BrokenClient(),
            model="gpt-test",
            logger=_Logger(),
        )

    assert exc_info.value.code == "LLM_UNAVAILABLE"
    assert exc_info.value.status_code == 503
    assert exc_info.value.stage == "stage1"


def test_example16_orchestrator_does_not_enter_stage2_on_stage1_llm_failure():
    class _BrokenRuntime:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            self.calls.append("stage1")
            raise UpstreamCallError.llm_unavailable(stage="stage1", status_code=503)

        def stage2_targeted_retrieval(self, *args, **kwargs):
            self.calls.append("stage2")
            return {}

    runtime = _BrokenRuntime()
    orchestrator = PatentGenerationOrchestrator()

    with pytest.raises(UpstreamCallError):
        orchestrator.run(question="demo question", runtime=runtime)

    assert runtime.calls == ["stage1"]


def test_example17_stage4_llm_failure_raises_without_fallback_answer():
    class _FailingHttpClient:
        def post(self, url, *, headers=None, content=None, json=None, timeout=None):
            del headers, content, json, timeout
            return httpx.Response(
                503,
                request=httpx.Request("POST", str(url)),
                json={"error": {"message": "upstream unavailable"}},
            )

        def close(self):
            return None

    builder = PatentAnswerBuilder(
        api_key="test-key",
        base_url="http://example.invalid",
        model="test-model",
        http_client=_FailingHttpClient(),
    )
    outcome = PatentRetrievalOutcome(
        retrieval_backend="vector_hybrid",
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        references=["CN115132975B"],
        reference_objects=[],
        reference_links=[],
        original_links=[],
        evidences=[
            PatentEvidence(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number=None,
                title="专利标题",
                abstract_text="摘要",
                matched_section_type="claim",
                matched_section_label="Claim 1",
                matched_snippet="证据片段",
            )
        ],
    )

    with pytest.raises(UpstreamCallError) as exc_info:
        builder(question="demo", retrieval_outcome=outcome, context={"allowed_patent_ids": ["CN115132975B"]})

    assert exc_info.value.code == "LLM_UNAVAILABLE"
    assert exc_info.value.stage == "stage4"
    assert exc_info.value.status_code == 503


def test_example18_stage4_empty_llm_response_raises_without_fallback_answer():
    class _EmptyHttpClient:
        def post(self, url, *, headers=None, content=None, json=None, timeout=None):
            del headers, content, json, timeout
            return httpx.Response(
                200,
                request=httpx.Request("POST", str(url)),
                json={"choices": [{"message": {"content": "   "}}]},
            )

        def close(self):
            return None

    builder = PatentAnswerBuilder(
        api_key="test-key",
        base_url="http://example.invalid",
        model="test-model",
        http_client=_EmptyHttpClient(),
    )
    outcome = PatentRetrievalOutcome(
        retrieval_backend="vector_hybrid",
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        references=["CN115132975B"],
        reference_objects=[],
        reference_links=[],
        original_links=[],
        evidences=[
            PatentEvidence(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number=None,
                title="专利标题",
                abstract_text="摘要",
                matched_section_type="claim",
                matched_section_label="Claim 1",
                matched_snippet="证据片段",
            )
        ],
    )

    with pytest.raises(UpstreamCallError) as exc_info:
        builder(question="demo", retrieval_outcome=outcome, context={"allowed_patent_ids": ["CN115132975B"]})

    assert exc_info.value.code == "LLM_UNAVAILABLE"
    assert exc_info.value.stage == "stage4"


def test_example19_rerank_fallback_emits_warning_step():
    class _RerankFallbackRuntime:
        stage25_is_noop = True
        stage25_skip_reason = "patent_mode_no_md_expansion"
        stage3_force_pdf = False

        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            return {
                "success": True,
                "deep_answer": "draft",
                "retrieval_claims": [
                    PatentRetrievalClaim(claim="battery safety", keywords=["battery"], preferred_sections=["claims"], filters={})
                ],
                "retrieval_plan": PatentRetrievalPlan(question_type="comparison"),
            }

        def stage2_targeted_retrieval(self, retrieval_claims, *, user_question: str, should_cancel=None, active_stream_count=None, conversation_context=None):
            return {
                "references": ["CN115132975B"],
                "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "stage2_rerank": {
                        "enabled": True,
                        "applied": False,
                        "fallback": True,
                        "fallback_reason": "request_failed",
                        "status_code": 503,
                    }
                },
            }

        def _extract_patent_ids_from_results(self, retrieval_results: dict[str, object]) -> list[str]:
            return list(retrieval_results.get("references") or [])

        def stage25_patent_evidence_expansion(self, *, retrieval_results, user_question, source_ids):
            return {"skipped": True, "skip_reason": "patent_mode_no_md_expansion", "retrieval_results": retrieval_results, "source_ids": source_ids}

        def stage3_load_patent_evidence(self, *, retrieval_results, source_ids, should_cancel=None):
            return {
                "source_ids": list(source_ids),
                "evidences": [{"canonical_patent_id": "CN115132975B", "matched_evidence": [{"text": "evidence"}]}],
                "metadata": {"force_pdf": False},
            }

        def stage4_synthesis_with_patent_evidence(self, **kwargs):
            return {
                "success": True,
                "final_answer": "final answer",
                "references": ["CN115132975B"],
                "reference_objects": [],
                "reference_links": [],
                "original_links": [],
                "metadata": {},
            }

    progress_steps: list[dict[str, object]] = []
    result = PatentGenerationOrchestrator().run(
        question="demo",
        runtime=_RerankFallbackRuntime(),
        progress_callback=progress_steps.append,
    )

    assert result.success is True
    warning_steps = [step for step in progress_steps if step.get("status") == "warning" and step.get("step") == "stage2_rerank_fallback"]
    assert warning_steps
    assert warning_steps[0]["data"]["code"] == "RERANK_DEGRADED"
    assert warning_steps[0]["data"]["fallback_reason"] == "request_failed"
    assert warning_steps[0]["data"]["status_code"] == 503


def test_example20_partial_stage3_failure_emits_warning_and_keeps_answer():
    def _catalog_loader(patent_id: str):
        if patent_id == "CNFAIL000A":
            raise RuntimeError("catalog unavailable")
        return SimpleNamespace(
            publication_number=patent_id,
            title=patent_id,
            abstract_text="abstract",
        )

    bundle = run_stage3_load_patent_evidence(
        retrieval_results={
            "documents": ["abstract hit", "another abstract"],
            "metadatas": [
                {"patent_id": "CN115132975B", "stage2_source": "abstract", "section_type": "abstract"},
                {"patent_id": "CNFAIL000A", "stage2_source": "abstract", "section_type": "abstract"},
            ],
            "distances": [0.1, 0.2],
            "reference_objects": [
                {"canonical_patent_id": "CN115132975B", "title": "ok patent"},
                {"canonical_patent_id": "CNFAIL000A", "title": "fail patent"},
            ],
        },
        source_ids=["CN115132975B", "CNFAIL000A"],
        catalog_loader=_catalog_loader,
    )

    assert bundle["source_ids"] == ["CN115132975B"]
    assert len(bundle["evidences"]) == 1
    metadata = dict(bundle.get("metadata") or {})
    assert metadata.get("stage3_failed_patent_count") == 1
    assert metadata["stage3_failed_patents"][0]["patent_id"] == "CNFAIL000A"


def test_example20_total_stage3_failure_raises_without_final_answer():
    def _always_fail(_patent_id: str):
        raise RuntimeError("catalog unavailable")

    with pytest.raises(UpstreamCallError) as exc_info:
        run_stage3_load_patent_evidence(
            retrieval_results={
                "documents": ["abstract hit"],
                "metadatas": [{"patent_id": "CNFAIL000A", "stage2_source": "abstract", "section_type": "abstract"}],
                "distances": [0.1],
                "reference_objects": [{"canonical_patent_id": "CNFAIL000A", "title": "fail patent"}],
            },
            source_ids=["CNFAIL000A"],
            catalog_loader=_always_fail,
        )

    assert exc_info.value.code == "RETRIEVAL_FAILED"
    assert exc_info.value.stage == "stage3"


def test_result_builder_maps_upstream_error_to_extended_error_event():
    err = UpstreamCallError.llm_unavailable(stage="stage4", status_code=502)
    api_error = PatentResultBuilder().to_api_error(err)
    event = PatentResultBuilder().build_error_event(trace_id="trace-1", seq=3, error=api_error)

    assert event["code"] == "LLM_UNAVAILABLE"
    assert event["status_code"] == 502
    assert event["failure_stage"] == "stage4"
    assert event["component"] == "llm"
    assert event["retriable"] is True
