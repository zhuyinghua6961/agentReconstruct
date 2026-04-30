from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from server.patent.cache_keys import PatentKeyFactory
from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.orchestrators.generation import PatentGenerationOrchestrator
from server.patent.runtime import PatentRuntime
from server.patent.stages.synthesis import run_stage4_synthesis_with_patent_evidence
from server.services.execution_cache import ExecutionCache


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
        self.calls.append("stage1")
        return {
            "deep_answer": f"draft:{user_question}",
            "retrieval_claims": [
                PatentRetrievalClaim(
                    claim="compare replacement risk",
                    keywords=["battery safety"],
                    preferred_sections=["claims", "description"],
                    filters={},
                )
            ],
            "retrieval_plan": PatentRetrievalPlan(
                question_type="comparison",
                candidate_recall_queries=["battery safety"],
            ),
        }

    def stage2_targeted_retrieval(self, retrieval_plan: PatentRetrievalPlan, *, user_question: str, should_cancel=None, active_stream_count=None) -> dict[str, object]:
        self.calls.append("stage2")
        return {
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "reference_links": [],
            "original_links": [],
            "metadata": {"retrieval_backend": "vector_hybrid"},
        }

    def _extract_patent_ids_from_results(self, retrieval_results: dict[str, object]) -> list[str]:
        self.calls.append("extract")
        return list(retrieval_results.get("references") or [])

    def stage25_patent_evidence_expansion(self, *, retrieval_results: dict[str, object], user_question: str, source_ids: list[str]) -> dict[str, object]:
        self.calls.append("stage25")
        return {
            "skipped": True,
            "skip_reason": "patent_mode_no_md_expansion",
            "retrieval_results": retrieval_results,
        }

    def stage3_load_patent_evidence(self, *, retrieval_results: dict[str, object], source_ids: list[str], should_cancel=None) -> dict[str, object]:
        self.calls.append("stage3")
        return {
            "source_ids": list(source_ids),
            "evidence_by_patent_id": {
                "CN115132975B": [
                    {"kind": "retrieval_chunk", "text": "matched evidence"},
                    {"kind": "table", "text": "table evidence"},
                ]
            },
        }

    def stage4_synthesis_with_patent_evidence(
        self,
        *,
        user_question: str,
        deep_answer: str,
        patent_evidence_bundle: dict[str, object],
        retrieval_results: dict[str, object] | None = None,
        should_cancel=None,
        conversation_context=None,
    ) -> dict[str, object]:
        self.calls.append("stage4")
        return {
            "final_answer": "stage4 synthesized answer",
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "reference_links": [],
            "original_links": [],
            "metadata": {"retrieval_backend": "vector_hybrid"},
        }


def test_orchestrator_runs_patent_stages_in_order_and_marks_stage25_skip():
    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator()

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert runtime.calls == ["stage1", "stage2", "extract", "stage25", "stage3", "stage4"]
    assert result.success is True
    assert result.final_answer == "stage4 synthesized answer"
    assert result.metadata.route == "kb_qa"
    assert result.metadata.source_ids == ["CN115132975B"]
    assert result.metadata.stage25_skipped is True
    assert result.metadata.stage25_skip_reason == "patent_mode_no_md_expansion"
    assert result.raw["stage2"]["references"] == ["CN115132975B"]
    assert result.raw["stage3"]["source_ids"] == ["CN115132975B"]
    assert result.raw["steps"][2] == {
        "step": "stage25",
        "title": "阶段二点五",
        "message": "阶段二点五：已跳过MD原文扩展（patent_mode_no_md_expansion）",
        "status": "skipped",
    }
    assert [step["message"] for step in result.raw["steps"]] == [
        "阶段一：已完成深度预回答与检索规划",
        "阶段二：已完成专利双库检索与归并",
        "阶段二点五：已跳过MD原文扩展（patent_mode_no_md_expansion）",
        "阶段三：已完成专利证据与表格组装",
        "阶段四：已完成答案生成",
    ]


def test_orchestrator_stream_and_final_payloads_do_not_expose_raw_patent_id_citations():
    class _ReadableCitationRuntime(_FakeRuntime):
        class _StreamingBuilder:
            def __call__(self, **kwargs):
                raise AssertionError("stream path should be used")

            def stream(self, *, question, retrieval_outcome, context):
                del question, retrieval_outcome, context
                yield "结论来自专利 (patent_id=CN115132975B)。"
                yield "外部来源 (patent_id=CN000000000A)。"

        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            content_callback=None,
            conversation_context=None,
        ) -> dict[str, object]:
            self.calls.append("stage4")
            del should_cancel
            return run_stage4_synthesis_with_patent_evidence(
                user_question=user_question,
                deep_answer=deep_answer,
                patent_evidence_bundle=patent_evidence_bundle,
                retrieval_results=retrieval_results,
                answer_builder=self._StreamingBuilder(),
                content_callback=content_callback,
                conversation_context=conversation_context,
            )

    streamed_chunks: list[str] = []
    result = PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_ReadableCitationRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
        content_callback=streamed_chunks.append,
    )

    stream_text = "".join(streamed_chunks)
    assert "patent_id=" not in stream_text
    assert "CN115132975B" in stream_text
    assert "patent_id=" not in result.final_answer
    assert "CN115132975B" in result.final_answer


def test_orchestrator_logs_stage_progress_with_trace(caplog):
    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator()

    with caplog.at_level("INFO", logger="patent.generation"):
        result = orchestrator.run(
            question="How should we compare replacement risk?",
            runtime=runtime,
            conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
            trace_id="trace-log-1",
        )

    assert result.success is True
    messages = [record.message for record in caplog.records if record.name == "patent.generation"]
    assert any("patent pipeline start" in message and "trace=trace-log-1" in message for message in messages)
    assert any("patent stage1 completed" in message and "retrieval_claims=1" in message for message in messages)
    assert any("patent stage2 completed" in message for message in messages)
    assert any("patent stage2 extracted source_ids" in message and "count=1" in message for message in messages)
    assert any("patent stage25 completed" in message and "skipped=True" in message for message in messages)
    assert any("patent stage3 completed" in message and "evidence_source_count=1" in message for message in messages)
    assert any("patent stage4 starting" in message and "source_id_count=1" in message for message in messages)
    assert any("patent pipeline completed" in message and "success=True" in message for message in messages)


def test_orchestrator_logs_short_circuit_when_stage1_returns_no_claims(caplog):
    class _Stage1OnlyRuntime(_FakeRuntime):
        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            self.calls.append("stage1")
            return {
                "deep_answer": "fallback answer",
                "retrieval_claims": [],
                "retrieval_plan": PatentRetrievalPlan(question_type="comparison"),
                "fallback": "planner_unavailable",
            }

    runtime = _Stage1OnlyRuntime()
    orchestrator = PatentGenerationOrchestrator()

    with caplog.at_level("INFO", logger="patent.generation"):
        result = orchestrator.run(
            question="How should we compare replacement risk?",
            runtime=runtime,
            conversation_context={"recent_turns_for_llm": []},
            trace_id="trace-short-circuit",
        )

    assert result.success is True
    assert result.metadata.stage1_short_circuit is True
    messages = [record.message for record in caplog.records if record.name == "patent.generation"]
    assert any("patent stage1 completed" in message and "fallback=planner_unavailable" in message for message in messages)
    assert any("patent pipeline short-circuit" in message and "trace=trace-short-circuit" in message for message in messages)


def test_orchestrator_preserves_stage2_metadata_when_stage4_omits_shell_metadata():
    class _Stage4WithoutMetadataRuntime(_FakeRuntime):
        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            conversation_context=None,
        ) -> dict[str, object]:
            self.calls.append("stage4")
            return {
                "final_answer": "stage4 synthesized answer",
                "references": ["CN115132975B"],
                "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                "reference_links": [],
                "original_links": [],
            }

    runtime = _Stage4WithoutMetadataRuntime()
    orchestrator = PatentGenerationOrchestrator()

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert result.raw["metadata"]["retrieval_backend"] == "vector_hybrid"


def test_orchestrator_allows_stage4_to_clear_stage2_citations():
    class _Stage4ClearsCitationsRuntime(_FakeRuntime):
        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            conversation_context=None,
        ) -> dict[str, object]:
            self.calls.append("stage4")
            return {
                "final_answer": "stage4 synthesized answer",
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "vector_hybrid"},
            }

    result = PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_Stage4ClearsCitationsRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert result.raw["references"] == []
    assert result.raw["reference_objects"] == []
    assert result.raw["reference_links"] == []
    assert result.raw["original_links"] == []


def test_orchestrator_reads_cached_stage_results_before_recomputing():
    class _CacheStub:
        def __init__(self) -> None:
            self.stage_payloads = {
                ("stage1",): {
                    "deep_answer": "cached deep answer",
                    "retrieval_claims": [
                        PatentRetrievalClaim(
                            claim="compare replacement risk",
                            keywords=["battery safety"],
                            preferred_sections=["claims"],
                            filters={},
                        )
                    ],
                    "retrieval_plan": PatentRetrievalPlan(
                        question_type="comparison",
                        candidate_recall_queries=["battery safety"],
                    ),
                },
                ("stage2",): {
                    "references": ["CN115132975B"],
                    "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                    "reference_links": [],
                    "original_links": [],
                    "metadata": {"retrieval_backend": "vector_hybrid"},
                },
                ("stage25",): {
                    "skipped": True,
                    "skip_reason": "patent_mode_no_md_expansion",
                    "retrieval_results": {
                        "references": ["CN115132975B"],
                        "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                        "reference_links": [],
                        "original_links": [],
                        "metadata": {"retrieval_backend": "vector_hybrid"},
                    },
                },
                ("stage3",): {
                    "source_ids": ["CN115132975B"],
                    "evidence_by_patent_id": {"CN115132975B": [{"kind": "table", "text": "cached evidence"}]},
                },
                ("stage4",): {
                    "final_answer": "cached final answer",
                    "references": ["CN115132975B"],
                    "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                    "reference_links": [{"type": "reference_link", "canonical_patent_id": "CN115132975B"}],
                    "original_links": [{"type": "original_view", "canonical_patent_id": "CN115132975B"}],
                    "metadata": {
                        "retrieval_backend": "vector_hybrid",
                        "references": ["CN115132975B"],
                        "original_links": [{"type": "original_view", "canonical_patent_id": "CN115132975B"}],
                    },
                },
            }
            self.claims: list[str] = []

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return self.stage_payloads.get((stage,))

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            self.stage_payloads[(stage,)] = payload
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            self.claims.append(stage)
            return f"token-{stage}"

        def get_stage_singleflight_owner(self, *, stage: str, fingerprint: str):
            return ""

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator(execution_cache=_CacheStub())

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert runtime.calls == ["extract"]
    assert result.final_answer == "cached final answer"
    assert result.raw["reference_links"] == [{"type": "reference_link", "canonical_patent_id": "CN115132975B"}]
    assert result.raw["original_links"] == [{"type": "original_view", "canonical_patent_id": "CN115132975B"}]
    assert result.raw["metadata"]["references"] == ["CN115132975B"]


def test_orchestrator_establishes_singleflight_boundaries_for_stage1_to_stage4():
    class _CacheRecorder:
        def __init__(self) -> None:
            self.claims: list[str] = []

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            self.claims.append(stage)
            return f"token-{stage}"

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    cache = _CacheRecorder()
    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator(execution_cache=cache)

    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert cache.claims == ["stage1", "stage2", "stage25", "stage3", "stage4"]


def test_orchestrator_stage4_fingerprint_changes_when_answer_runtime_signature_changes():
    class _FingerprintCache:
        def __init__(self) -> None:
            self.fingerprints: dict[str, list[str]] = {}

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            self.fingerprints.setdefault(stage, []).append(fingerprint)
            return f"token-{stage}-{len(self.fingerprints[stage])}"

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    class _AnswerBuilderRuntime(_FakeRuntime):
        def __init__(self, model: str) -> None:
            super().__init__()
            self.answer_builder = SimpleNamespace(
                model=model,
                base_url="https://llm.example.com/v1",
                timeout_seconds=30.0,
            )

    cache = _FingerprintCache()
    orchestrator = PatentGenerationOrchestrator(execution_cache=cache)

    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=_AnswerBuilderRuntime("deepseek-v3.1"),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )
    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=_AnswerBuilderRuntime("deepseek-v3.2"),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert cache.fingerprints["stage3"][0] == cache.fingerprints["stage3"][1]
    assert cache.fingerprints["stage4"][0] != cache.fingerprints["stage4"][1]


def test_orchestrator_stage4_fingerprint_changes_when_stage1_deep_answer_changes():
    class _FingerprintCache:
        def __init__(self) -> None:
            self.fingerprints: dict[str, list[str]] = {}

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            self.fingerprints.setdefault(stage, []).append(fingerprint)
            return f"token-{stage}-{len(self.fingerprints[stage])}"

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    class _DeepAnswerRuntime(_FakeRuntime):
        def __init__(self, deep_answer: str) -> None:
            super().__init__()
            self._deep_answer = deep_answer

        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            payload = super().stage1_pre_answer_and_planning(
                user_question=user_question,
                conversation_context=conversation_context,
            )
            payload["deep_answer"] = self._deep_answer
            return payload

    cache = _FingerprintCache()
    orchestrator = PatentGenerationOrchestrator(execution_cache=cache)

    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=_DeepAnswerRuntime("draft answer v1"),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )
    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=_DeepAnswerRuntime("draft answer v2"),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert cache.fingerprints["stage3"][0] == cache.fingerprints["stage3"][1]
    assert cache.fingerprints["stage4"][0] != cache.fingerprints["stage4"][1]


def test_orchestrator_stage4_fingerprint_changes_when_conversation_context_changes():
    class _FingerprintCache:
        def __init__(self) -> None:
            self.fingerprints: dict[str, list[str]] = {}

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            self.fingerprints.setdefault(stage, []).append(fingerprint)
            return f"token-{stage}-{len(self.fingerprints[stage])}"

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    cache = _FingerprintCache()
    orchestrator = PatentGenerationOrchestrator(execution_cache=cache)

    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=_FakeRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )
    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=_FakeRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Updated context"}]},
    )

    assert cache.fingerprints["stage3"][0] == cache.fingerprints["stage3"][1]
    assert cache.fingerprints["stage4"][0] != cache.fingerprints["stage4"][1]


def test_orchestrator_runtime_retrieval_signature_excludes_parallel_worker_counts(monkeypatch):
    captured: dict[str, dict[str, object]] = {}

    class _SignatureRuntime(_FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.retrieval_service = SimpleNamespace(
                retrieval_version="retrieval-v2",
                catalog_index_version="catalog-v2",
            )
            self.planning_model = "planner-model"
            self.stage2_parallel_workers = 4
            self.stage3_parallel_workers = 3

    def _capture_stage2(*, question, retrieval_claims, retrieval_plan, runtime_signature, conversation_context=None):
        del question, retrieval_claims, retrieval_plan, conversation_context
        captured["stage2"] = dict(runtime_signature or {})
        return "stage2-fingerprint"

    def _capture_stage3(*, retrieval_results, source_ids, force_pdf, runtime_signature):
        del retrieval_results, source_ids, force_pdf
        captured["stage3"] = dict(runtime_signature or {})
        return "stage3-fingerprint"

    monkeypatch.setattr("server.patent.orchestrators.generation.build_stage2_cache_fingerprint", _capture_stage2)
    monkeypatch.setattr("server.patent.orchestrators.generation.build_stage3_cache_fingerprint", _capture_stage3)

    PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_SignatureRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert "stage2_parallel_workers" not in captured["stage2"]
    assert "stage3_parallel_workers" not in captured["stage2"]
    assert "stage2_parallel_workers" not in captured["stage3"]
    assert "stage3_parallel_workers" not in captured["stage3"]


def test_orchestrator_passes_graph_context_to_stage2_cache_fingerprint(monkeypatch):
    captured: dict[str, object] = {}
    graph_context = {
        "graph_kb": {
            "stage2_patent_candidates": ["CN100355122C"],
            "stage2_constraints": [{"field": "patent.id", "operator": "eq", "value": "CN100355122C"}],
            "diagnostics": {"latency_ms": 12},
        }
    }

    def _capture_stage2(*, question, retrieval_claims, retrieval_plan, runtime_signature, conversation_context=None):
        del question, retrieval_claims, retrieval_plan, runtime_signature
        captured["conversation_context"] = conversation_context
        return "stage2-fingerprint"

    monkeypatch.setattr("server.patent.orchestrators.generation.build_stage2_cache_fingerprint", _capture_stage2)

    PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_FakeRuntime(),
        conversation_context=graph_context,
    )

    assert captured["conversation_context"] == graph_context


def test_orchestrator_passes_graph_context_to_stage2_runtime():
    captured: dict[str, object] = {}
    graph_context = {"graph_kb": {"stage2_patent_candidates": ["CN100355122C"]}}

    class _ContextRuntime(_FakeRuntime):
        def stage2_targeted_retrieval(
            self,
            retrieval_plan: PatentRetrievalPlan,
            *,
            user_question: str,
            should_cancel=None,
            active_stream_count=None,
            conversation_context=None,
        ) -> dict[str, object]:
            captured["conversation_context"] = conversation_context
            return super().stage2_targeted_retrieval(
                retrieval_plan,
                user_question=user_question,
                should_cancel=should_cancel,
                active_stream_count=active_stream_count,
            )

    PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_ContextRuntime(),
        conversation_context=graph_context,
    )

    assert captured["conversation_context"] == graph_context


def test_orchestrator_continues_passing_none_for_should_cancel():
    captured: dict[str, object] = {}

    class _CancelCaptureRuntime(_FakeRuntime):
        def stage2_targeted_retrieval(self, retrieval_plan: PatentRetrievalPlan, *, user_question: str, should_cancel=None, active_stream_count=None) -> dict[str, object]:
            captured["stage2_should_cancel"] = should_cancel
            captured["stage2_active_stream_count"] = active_stream_count
            return super().stage2_targeted_retrieval(
                retrieval_plan,
                user_question=user_question,
                should_cancel=should_cancel,
                active_stream_count=active_stream_count,
            )

        def stage3_load_patent_evidence(self, *, retrieval_results: dict[str, object], source_ids: list[str], should_cancel=None) -> dict[str, object]:
            captured["stage3_should_cancel"] = should_cancel
            return super().stage3_load_patent_evidence(
                retrieval_results=retrieval_results,
                source_ids=source_ids,
                should_cancel=should_cancel,
            )

    PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_CancelCaptureRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert captured["stage2_should_cancel"] is None
    assert captured["stage2_active_stream_count"] is None
    assert captured["stage3_should_cancel"] is None


def test_orchestrator_waits_for_existing_singleflight_owner_to_fill_stage_cache():
    class _WaitingCache:
        def __init__(self) -> None:
            self.stage1_reads = 0

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            if stage != "stage1":
                return None
            self.stage1_reads += 1
            if self.stage1_reads < 3:
                return None
            return {
                "deep_answer": "cached deep answer",
                "retrieval_claims": [
                    PatentRetrievalClaim(
                        claim="compare replacement risk",
                        keywords=["battery safety"],
                        preferred_sections=["claims"],
                        filters={},
                    )
                ],
                "retrieval_plan": PatentRetrievalPlan(
                    question_type="comparison",
                    candidate_recall_queries=["battery safety"],
                ),
            }

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            if stage == "stage1":
                return ""
            return f"token-{stage}"

        def get_stage_singleflight_owner(self, *, stage: str, fingerprint: str):
            return "other-owner" if stage == "stage1" and self.stage1_reads < 3 else ""

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=_WaitingCache(),
        singleflight_poll_interval_seconds=0.0,
        singleflight_wait_timeout_seconds=0.01,
    )

    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert "stage1" not in runtime.calls


def test_orchestrator_does_not_recompute_while_another_singleflight_owner_is_still_active():
    class _StuckOwnerCache:
        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            return ""

        def get_stage_singleflight_owner(self, *, stage: str, fingerprint: str):
            return "other-owner"

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=_StuckOwnerCache(),
        singleflight_poll_interval_seconds=0.0,
        singleflight_wait_timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError, match="singleflight wait timed out"):
        orchestrator.run(
            question="How should we compare replacement risk?",
            runtime=runtime,
            conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
        )

    assert "stage1" not in runtime.calls


def test_orchestrator_keeps_waiting_while_singleflight_owner_remains_active_past_initial_ttl(monkeypatch):
    class _LongRunningOwnerCache:
        def __init__(self) -> None:
            self.stage1_reads = 0

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            if stage != "stage1":
                return None
            self.stage1_reads += 1
            if self.stage1_reads < 4:
                return None
            return {
                "deep_answer": "cached deep answer",
                "retrieval_claims": [
                    PatentRetrievalClaim(
                        claim="compare replacement risk",
                        keywords=["battery safety"],
                        preferred_sections=["claims"],
                        filters={},
                    )
                ],
                "retrieval_plan": PatentRetrievalPlan(
                    question_type="comparison",
                    candidate_recall_queries=["battery safety"],
                ),
            }

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            if stage == "stage1":
                return ""
            return f"token-{stage}"

        def get_stage_singleflight_owner(self, *, stage: str, fingerprint: str):
            if stage == "stage1" and self.stage1_reads < 4:
                return "active-owner"
            return ""

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    runtime = _FakeRuntime()
    tick = {"value": 0.0}

    def _fake_monotonic() -> float:
        tick["value"] += 0.6
        return tick["value"]

    monkeypatch.setattr("server.patent.orchestrators.generation.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("server.patent.orchestrators.generation.time.sleep", lambda _seconds: None)

    orchestrator = PatentGenerationOrchestrator(
        execution_cache=_LongRunningOwnerCache(),
        singleflight_ttl_seconds=1,
        singleflight_poll_interval_seconds=0.0,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert "stage1" not in runtime.calls
    assert result.final_answer == "stage4 synthesized answer"


def test_orchestrator_renews_stage_singleflight_while_owner_is_computing():
    class _RenewingCache:
        def __init__(self) -> None:
            self.renew_calls: list[tuple[str, str, int]] = []

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            return f"token-{stage}"

        def renew_stage_singleflight(self, *, stage: str, fingerprint: str, token: str, ttl_seconds: int):
            self.renew_calls.append((stage, token, ttl_seconds))
            return True

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    class _SlowStage1Runtime(_FakeRuntime):
        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            time.sleep(0.02)
            return super().stage1_pre_answer_and_planning(user_question, conversation_context=conversation_context)

    cache = _RenewingCache()
    runtime = _SlowStage1Runtime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=cache,
        singleflight_ttl_seconds=1,
        singleflight_renew_interval_seconds=0.001,
    )

    orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert any(stage == "stage1" and token == "token-stage1" and ttl == 1 for stage, token, ttl in cache.renew_calls)


def test_orchestrator_keeps_waiting_when_original_owner_releases_and_another_contender_claims_first():
    class _RecontendedCache:
        def __init__(self) -> None:
            self.stage1_reads = 0
            self.stage1_claims = 0

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            if stage != "stage1":
                return None
            self.stage1_reads += 1
            if self.stage1_reads < 4:
                return None
            return {
                "deep_answer": "cached deep answer",
                "retrieval_claims": [
                    PatentRetrievalClaim(
                        claim="compare replacement risk",
                        keywords=["battery safety"],
                        preferred_sections=["claims"],
                        filters={},
                    )
                ],
                "retrieval_plan": PatentRetrievalPlan(
                    question_type="comparison",
                    candidate_recall_queries=["battery safety"],
                ),
            }

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            if stage != "stage1":
                return f"token-{stage}"
            self.stage1_claims += 1
            return ""

        def get_stage_singleflight_owner(self, *, stage: str, fingerprint: str):
            if stage != "stage1":
                return ""
            if self.stage1_claims <= 1 and self.stage1_reads < 2:
                return "owner-a"
            if self.stage1_claims <= 1:
                return ""
            if self.stage1_reads < 4:
                return "owner-b"
            return ""

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=_RecontendedCache(),
        singleflight_poll_interval_seconds=0.0,
        singleflight_wait_timeout_seconds=0.01,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert "stage1" not in runtime.calls
    assert result.final_answer == "stage4 synthesized answer"


def test_orchestrator_rolls_default_owner_wait_deadline_forward_after_owner_handoff(monkeypatch):
    class _DefaultHandoffCache:
        def __init__(self) -> None:
            self.stage1_reads = 0
            self.stage1_claims = 0

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            if stage != "stage1":
                return None
            self.stage1_reads += 1
            if self.stage1_reads < 4:
                return None
            return {
                "deep_answer": "cached deep answer",
                "retrieval_claims": [
                    PatentRetrievalClaim(
                        claim="compare replacement risk",
                        keywords=["battery safety"],
                        preferred_sections=["claims"],
                        filters={},
                    )
                ],
                "retrieval_plan": PatentRetrievalPlan(
                    question_type="comparison",
                    candidate_recall_queries=["battery safety"],
                ),
            }

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            if stage != "stage1":
                return f"token-{stage}"
            self.stage1_claims += 1
            return ""

        def get_stage_singleflight_owner(self, *, stage: str, fingerprint: str):
            if stage != "stage1":
                return ""
            if self.stage1_claims <= 1 and self.stage1_reads < 2:
                return "owner-a"
            if self.stage1_claims <= 1:
                return ""
            if self.stage1_reads < 4:
                return "owner-b"
            return ""

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    tick = {"value": 0.0}

    def _fake_monotonic() -> float:
        tick["value"] += 0.6
        return tick["value"]

    monkeypatch.setattr("server.patent.orchestrators.generation.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("server.patent.orchestrators.generation.time.sleep", lambda _seconds: None)

    runtime = _FakeRuntime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=_DefaultHandoffCache(),
        singleflight_ttl_seconds=1,
        singleflight_poll_interval_seconds=0.0,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert "stage1" not in runtime.calls
    assert result.final_answer == "stage4 synthesized answer"


def test_orchestrator_marks_empty_stage4_payload_as_unsuccessful():
    class _EmptyStage4Runtime(_FakeRuntime):
        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            conversation_context=None,
        ) -> dict[str, object]:
            self.calls.append("stage4")
            return {}

    runtime = _EmptyStage4Runtime()
    orchestrator = PatentGenerationOrchestrator()

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert result.success is False
    assert result.final_answer == ""
    assert result.raw["steps"][-1] == {
        "step": "stage4",
        "title": "阶段四",
        "message": "阶段四：答案生成失败",
        "status": "failed",
    }


def test_orchestrator_reports_stage25_completion_when_not_skipped():
    class _Stage25Runtime(_FakeRuntime):
        def stage25_patent_evidence_expansion(self, *, retrieval_results: dict[str, object], user_question: str, source_ids: list[str]) -> dict[str, object]:
            self.calls.append("stage25")
            return {
                "retrieval_results": retrieval_results,
                "expanded": True,
            }

    result = PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_Stage25Runtime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert result.raw["steps"][2] == {
        "step": "stage25",
        "title": "阶段二点五",
        "message": "阶段二点五：已完成MD原文扩展检索",
        "status": "success",
    }


def test_orchestrator_threads_stage25_retrieval_results_into_stage3_and_stage4():
    class _Stage25Runtime(_FakeRuntime):
        def stage25_patent_evidence_expansion(self, *, retrieval_results: dict[str, object], user_question: str, source_ids: list[str]) -> dict[str, object]:
            self.calls.append("stage25")
            return {
                "retrieval_results": {
                    "references": ["CN999999999A"],
                    "reference_objects": [{"canonical_patent_id": "CN999999999A"}],
                    "reference_links": [{"type": "original_view", "canonical_patent_id": "CN999999999A"}],
                    "original_links": [{"type": "original_view", "canonical_patent_id": "CN999999999A"}],
                    "metadata": {"retrieval_backend": "stage25-expanded"},
                },
                "source_ids": ["CN999999999A"],
                "expanded": True,
            }

        def stage3_load_patent_evidence(self, *, retrieval_results: dict[str, object], source_ids: list[str], should_cancel=None) -> dict[str, object]:
            self.calls.append("stage3")
            assert retrieval_results["references"] == ["CN999999999A"]
            assert source_ids == ["CN999999999A"]
            return {
                "source_ids": ["CN999999999A"],
                "evidence_by_patent_id": {"CN999999999A": [{"kind": "retrieval_chunk", "text": "matched evidence"}]},
            }

        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            conversation_context=None,
        ) -> dict[str, object]:
            self.calls.append("stage4")
            assert retrieval_results["references"] == ["CN999999999A"]
            return {
                "final_answer": "stage4 synthesized answer",
                "references": ["CN999999999A"],
                "reference_objects": [{"canonical_patent_id": "CN999999999A"}],
                "reference_links": [{"type": "original_view", "canonical_patent_id": "CN999999999A"}],
                "original_links": [{"type": "original_view", "canonical_patent_id": "CN999999999A"}],
                "metadata": {"retrieval_backend": "stage25-expanded"},
            }

    result = PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=_Stage25Runtime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert result.metadata.source_ids == ["CN999999999A"]
    assert result.raw["references"] == ["CN999999999A"]
    assert result.raw["metadata"]["retrieval_backend"] == "stage25-expanded"


def test_patent_runtime_stage25_noop_preserves_retrieval_payload_and_skip_metadata():
    runtime = PatentRuntime(
        retrieval_service=object(),  # type: ignore[arg-type]
        resources=[],
    )
    retrieval_results = {
        "references": ["CN115132975B"],
        "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
    }

    result = runtime.stage25_patent_evidence_expansion(
        retrieval_results=retrieval_results,
        user_question="How should we compare replacement risk?",
        source_ids=["CN115132975B"],
    )

    assert result["skipped"] is True
    assert result["skip_reason"] == "patent_mode_no_md_expansion"
    assert result["retrieval_results"] == retrieval_results


def test_orchestrator_falls_back_to_uncached_compute_when_stage_cache_backend_is_unavailable():
    runtime = _FakeRuntime()
    cache = ExecutionCache(None, PatentKeyFactory(env="test"))
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=cache,
        singleflight_wait_timeout_seconds=0.01,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert runtime.calls == ["stage1", "stage2", "extract", "stage25", "stage3", "stage4"]
    assert result.final_answer == "stage4 synthesized answer"


def test_orchestrator_falls_back_to_uncached_compute_when_cache_backend_raises_runtime_errors():
    class _FailingRedis:
        def get(self, key):
            raise RuntimeError("redis down")

        def set(self, key, value, ex=None, nx=False):
            raise RuntimeError("redis down")

        def compare_delete(self, key, token):
            raise RuntimeError("redis down")

        def compare_expire(self, key, token, ttl):
            raise RuntimeError("redis down")

    runtime = _FakeRuntime()
    cache = ExecutionCache(_FailingRedis(), PatentKeyFactory(env="test"))
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=cache,
        singleflight_wait_timeout_seconds=0.01,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert runtime.calls == ["stage1", "stage2", "extract", "stage25", "stage3", "stage4"]
    assert result.final_answer == "stage4 synthesized answer"


def test_orchestrator_returns_computed_result_when_singleflight_renew_fails_mid_stage():
    class _RenewFailureCache:
        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            raise AssertionError("set_stage_cache should be skipped after renew failure")

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            return f"token-{stage}"

        def renew_stage_singleflight(self, *, stage: str, fingerprint: str, token: str, ttl_seconds: int):
            raise RuntimeError("redis down")

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    class _SlowStage1Runtime(_FakeRuntime):
        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            time.sleep(0.02)
            return super().stage1_pre_answer_and_planning(user_question, conversation_context=conversation_context)

    runtime = _SlowStage1Runtime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=_RenewFailureCache(),
        singleflight_ttl_seconds=1,
        singleflight_renew_interval_seconds=0.001,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert runtime.calls == ["stage1", "stage2", "extract", "stage25", "stage3", "stage4"]
    assert result.final_answer == "stage4 synthesized answer"


def test_orchestrator_does_not_publish_stage_cache_when_renew_failure_arrives_after_compute():
    class _DelayedRenewFailureCache:
        def __init__(self) -> None:
            self.set_calls: list[str] = []

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            self.set_calls.append(stage)
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            return f"token-{stage}"

        def renew_stage_singleflight(self, *, stage: str, fingerprint: str, token: str, ttl_seconds: int):
            time.sleep(0.01)
            raise RuntimeError("redis down")

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    class _FastStage1Runtime(_FakeRuntime):
        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            time.sleep(0.005)
            return super().stage1_pre_answer_and_planning(user_question, conversation_context=conversation_context)

    cache = _DelayedRenewFailureCache()
    runtime = _FastStage1Runtime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=cache,
        singleflight_ttl_seconds=1,
        singleflight_renew_interval_seconds=0.001,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert result.final_answer == "stage4 synthesized answer"
    assert "stage1" not in cache.set_calls


def test_orchestrator_does_not_publish_stage_cache_when_renew_completion_exceeds_join_window():
    class _SlowRenewFailureCache:
        def __init__(self) -> None:
            self.set_calls: list[str] = []

        def get_stage_cache(self, *, stage: str, fingerprint: str):
            return None

        def set_stage_cache(self, *, stage: str, fingerprint: str, payload, ttl_seconds: int):
            self.set_calls.append(stage)
            return True

        def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int):
            return f"token-{stage}"

        def renew_stage_singleflight(self, *, stage: str, fingerprint: str, token: str, ttl_seconds: int):
            time.sleep(0.10)
            raise RuntimeError("redis down")

        def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str):
            return True

    class _FastStage1Runtime(_FakeRuntime):
        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            time.sleep(0.005)
            return super().stage1_pre_answer_and_planning(user_question, conversation_context=conversation_context)

    cache = _SlowRenewFailureCache()
    runtime = _FastStage1Runtime()
    orchestrator = PatentGenerationOrchestrator(
        execution_cache=cache,
        singleflight_ttl_seconds=1,
        singleflight_renew_interval_seconds=0.001,
    )

    result = orchestrator.run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier context"}]},
    )

    assert result.final_answer == "stage4 synthesized answer"
    assert "stage1" not in cache.set_calls


def test_orchestrator_short_circuits_to_stage1_answer_when_no_retrieval_claims_are_available():
    class _Stage1OnlyRuntime(_FakeRuntime):
        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
            self.calls.append("stage1")
            return {
                "deep_answer": "stage1 only answer",
                "retrieval_claims": [],
                "retrieval_plan": PatentRetrievalPlan(),
                "fallback": "json_parse_failed",
            }

    runtime = _Stage1OnlyRuntime()

    result = PatentGenerationOrchestrator().run(
        question="How should we compare replacement risk?",
        runtime=runtime,
        conversation_context={"recent_turns_for_llm": []},
    )

    assert runtime.calls == ["stage1"]
    assert result.success is True
    assert result.final_answer == "stage1 only answer"
    assert result.raw["references"] == []
    assert result.raw["steps"] == [
        {
            "step": "stage1",
            "title": "阶段一",
            "message": "阶段一：已完成深度预回答与检索规划",
            "status": "success",
        }
    ]


def test_orchestrator_stage2_fingerprint_includes_stage2_runtime_signature(monkeypatch):
    captured = {}

    class _Runtime(_FakeRuntime):
        def stage2_runtime_signature(self):
            return {
                "stage2_convergence_enabled": True,
                "stage2_guardrail_version": "guardrail-v1",
                "stage2_max_global_patents": 12,
            }

    def _capture_stage2(**kwargs):
        captured["runtime_signature"] = dict(kwargs.get("runtime_signature") or {})
        return "stage2-fingerprint"

    monkeypatch.setattr("server.patent.orchestrators.generation.build_stage2_cache_fingerprint", _capture_stage2)

    PatentGenerationOrchestrator().run(question="q", runtime=_Runtime(), conversation_context={})

    assert captured["runtime_signature"]["stage2_convergence_enabled"] is True
    assert captured["runtime_signature"]["stage2_guardrail_version"] == "guardrail-v1"
    assert captured["runtime_signature"]["stage2_max_global_patents"] == 12
