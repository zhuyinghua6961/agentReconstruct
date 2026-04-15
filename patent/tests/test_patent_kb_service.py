from __future__ import annotations

import pytest

from server.errors import codes
from server.errors.core import APIError
from server.patent.graph_kb.models import PatentGraphKbExecutionResult
from server.patent.kb_service import PatentKbService
from server.patent.models import PatentQaExecutionMetadata, PatentQaExecutionResult
from server.patent.retrieval_models import (
    PatentCatalogRecord,
    PatentClaim,
    PatentDescriptionSnippet,
    PatentEvidence,
    PatentRetrievalOutcome,
    PatentTableSupplement,
)
from server.patent.retrieval_service import PatentRetrievalService
from server.schemas.request_models import PatentAskRequest


class _FakeOrchestrator:
    def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
        assert question == "Explain the novelty"
        assert conversation_context == {"recent_turns_for_llm": [{"role": "user", "content": "Earlier turn"}]}
        return PatentQaExecutionResult(
            success=True,
            final_answer="staged patent answer",
            metadata=PatentQaExecutionMetadata(
                route="kb_qa",
                query_mode="patent staged qa",
                source_ids=["CN115132975B"],
                stage_timings_ms={"stage1": 5.0, "stage2": 8.0},
                stage25_skipped=True,
                stage25_skip_reason="patent_mode_no_md_expansion",
            ),
            raw={
                "references": ["CN115132975B"],
                "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "vector_hybrid"},
                "steps": [
                    {"step": "stage1", "title": "阶段一", "message": "阶段一：已完成深度预回答与检索规划", "status": "success"},
                    {"step": "stage2", "title": "阶段二", "message": "阶段二：已完成专利双库检索与归并", "status": "success"},
                    {"step": "stage25", "title": "阶段二点五", "message": "阶段二点五：已跳过MD原文扩展（patent_mode_no_md_expansion）", "status": "skipped"},
                    {"step": "stage3", "title": "阶段三", "message": "阶段三：已完成专利证据与表格组装", "status": "success"},
                    {"step": "stage4", "title": "阶段四", "message": "阶段四：已完成答案生成", "status": "success"},
                ],
            },
        )


class _FakeStagedRuntime:
    def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
        return {}

    def stage2_targeted_retrieval(self, retrieval_plan, *, user_question: str, should_cancel=None, active_stream_count=None) -> dict[str, object]:
        return {}

    def _extract_patent_ids_from_results(self, retrieval_results: dict[str, object]) -> list[str]:
        return []

    def stage25_patent_evidence_expansion(self, *, retrieval_results: dict[str, object], user_question: str, source_ids: list[str]) -> dict[str, object]:
        return {}

    def stage3_load_patent_evidence(self, *, retrieval_results: dict[str, object], source_ids: list[str], should_cancel=None) -> dict[str, object]:
        return {}

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
        return {}


def _make_request(question: str = "Explain the novelty") -> PatentAskRequest:
    return PatentAskRequest(
        question=question,
        conversation_id=123,
        chat_history=[],
        requested_mode="patent",
        actual_mode="patent",
        route="kb_qa",
        source_scope="kb",
        turn_mode="kb_only",
        kb_enabled=True,
        allow_kb_verification=False,
        used_files=[],
        execution_files=[],
        selected_file_ids=[],
        primary_file_id=None,
        file_selection={},
        trace_id="req_kb",
        options={},
    )


def test_kb_service_returns_shell_compatible_execution_result_from_orchestrator():
    service = PatentKbService(orchestrator=_FakeOrchestrator())

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier turn"}]},
    )

    assert execution_result["answer_text"] == "staged patent answer"
    assert execution_result["route"] == "kb_qa"
    assert execution_result["references"] == ["CN115132975B"]
    assert execution_result["reference_objects"] == [{"canonical_patent_id": "CN115132975B"}]
    assert execution_result["reference_links"] == []
    assert execution_result["original_links"] == []
    assert execution_result["metadata"]["retrieval_backend"] == "vector_hybrid"
    assert execution_result["metadata"]["stage25_skipped"] is True
    assert execution_result["timings"] == {"stage1": 5.0, "stage2": 8.0}
    assert execution_result["steps"] == [
        {"step": "stage1", "title": "阶段一", "message": "阶段一：已完成深度预回答与检索规划", "status": "success"},
        {"step": "stage2", "title": "阶段二", "message": "阶段二：已完成专利双库检索与归并", "status": "success"},
        {"step": "stage25", "title": "阶段二点五", "message": "阶段二点五：已跳过MD原文扩展（patent_mode_no_md_expansion）", "status": "skipped"},
        {"step": "stage3", "title": "阶段三", "message": "阶段三：已完成专利证据与表格组装", "status": "success"},
        {"step": "stage4", "title": "阶段四", "message": "阶段四：已完成答案生成", "status": "success"},
    ]


def test_kb_service_returns_graph_result_before_staged_runtime():
    class _FailingOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            raise AssertionError("staged orchestrator should not run after graph hit")

    captured = {}

    def _graph_kb_service(*, question, conversation_context, neo4j_client, max_rows, timeout_ms, generation_runtime=None):
        captured.update(
            {
                "question": question,
                "conversation_context": conversation_context,
                "neo4j_client": neo4j_client,
                "max_rows": max_rows,
                "timeout_ms": timeout_ms,
                "generation_runtime": generation_runtime,
            }
        )
        return PatentGraphKbExecutionResult(
            handled=True,
            answer="graph answer",
            references=("CN100355122C",),
            reference_objects=(
                {
                    "canonical_patent_id": "CN100355122C",
                    "patent_id": "CN100355122C",
                    "title": "一种提高磷酸铁锂大电流放电性能的方法",
                    "source": "patent_graph",
                },
            ),
            query_mode="patent_graph_kb",
            template_id="lookup_patent_by_id",
            result_count=1,
            latency_ms=12.5,
            metadata={"stub_filtered_count": 0},
        )

    graph_client = object()
    service = PatentKbService(
        orchestrator=_FailingOrchestrator(),
        graph_kb_service=_graph_kb_service,
        graph_kb_client=graph_client,
        graph_kb_enabled=True,
        graph_kb_max_rows=15,
        graph_kb_timeout_ms=2500,
    )

    execution_result = service.run(
        request=_make_request(question="CN100355122C 这件专利是什么？"),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert execution_result["answer_text"] == "graph answer"
    assert execution_result["query_mode"] == "patent_graph_kb"
    assert execution_result["references"] == ["CN100355122C"]
    assert execution_result["reference_objects"][0]["patent_id"] == "CN100355122C"
    assert execution_result["steps"] == [
        {
            "step": "patent_graph_kb",
            "title": "专利图谱",
            "message": "专利图谱：已完成结构化图谱查询",
            "status": "success",
        }
    ]
    assert execution_result["timings"] == {"patent_graph_kb": 12.5}
    assert execution_result["metadata"]["query_mode"] == "patent_graph_kb"
    assert execution_result["metadata"]["template_id"] == "lookup_patent_by_id"
    assert captured["neo4j_client"] is graph_client
    assert captured["max_rows"] == 15
    assert captured["timeout_ms"] == 2500
    assert captured["generation_runtime"] is not None


def test_kb_service_falls_back_to_staged_runtime_when_graph_service_does_not_handle():
    class _RecordingOrchestrator(_FakeOrchestrator):
        def __init__(self) -> None:
            self.calls = 0

        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            self.calls += 1
            return super().run(question=question, runtime=runtime, conversation_context=conversation_context)

    orchestrator = _RecordingOrchestrator()
    service = PatentKbService(
        orchestrator=orchestrator,
        graph_kb_service=lambda **kwargs: PatentGraphKbExecutionResult(handled=False, fallback_reason="no_plan"),
        graph_kb_client=object(),
        graph_kb_enabled=True,
    )

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier turn"}]},
    )

    assert orchestrator.calls == 1
    assert execution_result["answer_text"] == "staged patent answer"
    assert execution_result["query_mode"] == "patent_kb_qa"


def test_kb_service_skips_graph_preflight_when_graph_disabled():
    class _RecordingOrchestrator(_FakeOrchestrator):
        def __init__(self) -> None:
            self.calls = 0

        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            self.calls += 1
            return super().run(question=question, runtime=runtime, conversation_context=conversation_context)

    graph_calls = {"count": 0}

    def _graph_kb_service(**kwargs):
        graph_calls["count"] += 1
        return PatentGraphKbExecutionResult(handled=True, answer="should not be used")

    orchestrator = _RecordingOrchestrator()
    service = PatentKbService(
        orchestrator=orchestrator,
        graph_kb_service=_graph_kb_service,
        graph_kb_client=object(),
        graph_kb_enabled=False,
    )

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier turn"}]},
    )

    assert graph_calls["count"] == 0
    assert orchestrator.calls == 1
    assert execution_result["answer_text"] == "staged patent answer"


def test_kb_service_falls_back_to_staged_runtime_when_graph_service_raises():
    class _RecordingOrchestrator(_FakeOrchestrator):
        def __init__(self) -> None:
            self.calls = 0

        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            self.calls += 1
            return super().run(question=question, runtime=runtime, conversation_context=conversation_context)

    orchestrator = _RecordingOrchestrator()
    service = PatentKbService(
        orchestrator=orchestrator,
        graph_kb_service=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("graph failure")),
        graph_kb_client=object(),
        graph_kb_enabled=True,
    )

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "Earlier turn"}]},
    )

    assert orchestrator.calls == 1
    assert execution_result["answer_text"] == "staged patent answer"


def test_kb_service_falls_back_to_runtime_retrieval_service_when_runtime_is_not_staged():
    class _FailingOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            raise AssertionError("orchestrator should not be used for a partial runtime")

    class _PartialRuntime:
        def __init__(self, retrieval_service: PatentRetrievalService) -> None:
            self.retrieval_service = retrieval_service

    retrieval_service = PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN123456789A",
                publication_number="CN123456789A",
                application_number="CN202410001234X",
                title="Battery thermal management system for electric vehicles",
                abstract_text="A thermal control system for electric vehicle battery packs.",
                applicant_names=["Example Battery Co"],
                inventor_names=["Alice Inventor"],
                ipc_codes=["H01M10/613"],
                cpc_codes=["H01M10/613"],
                claims=[PatentClaim(claim_number=1, text="A battery thermal management system configured for electric vehicles.")],
                description_snippets=[PatentDescriptionSnippet(paragraph_id="p-001", text="Battery temperature control.")],
                country="CN",
                kind_code="A",
                publication_date="2024-01-01",
                provider="patent_source_x",
                original_available=True,
            )
        ],
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
    )
    service = PatentKbService(orchestrator=_FailingOrchestrator())

    execution_result = service.run(
        request=_make_request(question="Please summarize CN123456789A"),
        runtime=_PartialRuntime(retrieval_service),
        conversation_context={"trace_id": "req_kb"},
    )

    assert execution_result["answer_text"].startswith("Patent retrieval answer:")
    assert execution_result["references"] == ["CN123456789A"]
    assert execution_result["metadata"]["retrieval_backend"] == "exact_id"
    assert "CN123456789A" in execution_result["metadata"]["kb_evidence_context"]
    assert "Battery thermal management system for electric vehicles" in execution_result["metadata"]["kb_evidence_context"]
    assert execution_result["metadata"]["kb_reference_instruction"] == "引用知识库结论时仅可使用这些专利号：CN123456789A"


def test_kb_service_uses_stage3_evidence_context_instead_of_final_answer_excerpt():
    class _EvidenceOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            return PatentQaExecutionResult(
                success=True,
                final_answer="最终答案：这是阶段四合成后的总结，不应直接拿来当证据上下文。",
                metadata=PatentQaExecutionMetadata(
                    route="kb_qa",
                    query_mode="patent staged qa",
                    source_ids=["CN115132975B"],
                    stage_timings_ms={"stage1": 5.0, "stage2": 8.0, "stage3": 12.0, "stage4": 20.0},
                ),
                raw={
                    "stage3": {
                        "source_ids": ["CN115132975B"],
                        "evidences": [
                            {
                                "canonical_patent_id": "CN115132975B",
                                "title": "一种锂离子电池及动力车辆",
                                "abstract_text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                                "matched_evidence": [
                                    {
                                        "section_type": "claim",
                                        "section_label": "Claim 1",
                                        "text": "一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                                    }
                                ],
                                "table_supplements": [
                                    {
                                        "table_title": "性能表",
                                        "columns": ["指标", "数值"],
                                        "rows": [{"指标": "容量", "数值": "120mAh"}],
                                    }
                                ],
                            }
                        ],
                    },
                    "references": ["CN115132975B"],
                    "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                    "reference_links": [],
                    "original_links": [],
                    "metadata": {"retrieval_backend": "vector_hybrid"},
                    "steps": [],
                },
            )

    service = PatentKbService(orchestrator=_EvidenceOrchestrator())

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    evidence_context = execution_result["metadata"]["kb_evidence_context"]
    assert "Claim 1" in evidence_context
    assert "LMFP、LFP 与三元材料" in evidence_context
    assert "性能表" in evidence_context
    assert "最终答案：这是阶段四合成后的总结" not in evidence_context
    assert execution_result["metadata"]["kb_reference_instruction"] == "引用知识库结论时仅可使用这些专利号：CN115132975B"


def test_kb_service_rejects_stub_fallback_for_file_kb_routes_without_live_kb_backend():
    graph_calls = {"count": 0}

    def _graph_kb_service(**kwargs):
        graph_calls["count"] += 1
        return PatentGraphKbExecutionResult(handled=True, answer="should not be used")

    service = PatentKbService(
        graph_kb_service=_graph_kb_service,
        graph_kb_client=object(),
        graph_kb_enabled=True,
    )

    request = PatentAskRequest(
        question="请结合 PDF 和知识库总结结论",
        conversation_id=123,
        chat_history=[],
        requested_mode="patent",
        actual_mode="patent",
        route="hybrid_qa",
        source_scope="pdf+kb",
        turn_mode="mixed",
        kb_enabled=True,
        allow_kb_verification=True,
        used_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
        execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
        selected_file_ids=[11],
        primary_file_id=11,
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+kb", "selected_file_ids": [11]},
        trace_id="req_file_kb",
        options={},
    )

    with pytest.raises(APIError) as exc_info:
        service.run(
            request=request,
            runtime=None,
            conversation_context={"recent_turns_for_llm": []},
        )

    assert graph_calls["count"] == 0
    assert exc_info.value.code == codes.SERVICE_NOT_READY
    assert exc_info.value.error == "service_not_ready"


def test_kb_service_raises_api_error_when_staged_execution_fails():
    class _FailingOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            return PatentQaExecutionResult(
                success=False,
                final_answer="",
                metadata=PatentQaExecutionMetadata(
                    route="kb_qa",
                    query_mode="patent staged qa",
                    source_ids=[],
                    stage_timings_ms={"stage1": 5.0, "stage4": 0.0},
                ),
                raw={"metadata": {}, "steps": []},
            )

    service = PatentKbService(orchestrator=_FailingOrchestrator())

    with pytest.raises(APIError) as exc_info:
        service.run(
            request=_make_request(),
            runtime=_FakeStagedRuntime(),
            conversation_context={"recent_turns_for_llm": []},
        )

    assert exc_info.value.code == codes.INTERNAL_ERROR
    assert exc_info.value.error == "internal_error"
    assert "stage4" in exc_info.value.message.lower()


def test_kb_service_builds_semantic_stage_messages_when_raw_steps_missing():
    class _NoStepsOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            return PatentQaExecutionResult(
                success=True,
                final_answer="staged patent answer",
                metadata=PatentQaExecutionMetadata(
                    route="kb_qa",
                    query_mode="patent staged qa",
                    source_ids=["CN115132975B"],
                    stage_timings_ms={"stage1": 5.0, "stage2": 8.0, "stage3": 13.0, "stage4": 21.0},
                    stage25_skipped=True,
                    stage25_skip_reason="patent_mode_no_md_expansion",
                ),
                raw={"metadata": {"retrieval_backend": "vector_hybrid"}, "steps": []},
            )

    service = PatentKbService(orchestrator=_NoStepsOrchestrator())

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert [step["message"] for step in execution_result["steps"]] == [
        "阶段一：已完成深度预回答与检索规划",
        "阶段二：已完成专利双库检索与归并",
        "阶段二点五：已跳过MD原文扩展（patent_mode_no_md_expansion）",
        "阶段三：已完成专利证据与表格组装",
        "阶段四：已完成答案生成",
    ]


def test_kb_service_builds_stage25_completion_message_when_not_skipped():
    class _NoStepsOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            return PatentQaExecutionResult(
                success=True,
                final_answer="staged patent answer",
                metadata=PatentQaExecutionMetadata(
                    route="kb_qa",
                    query_mode="patent staged qa",
                    source_ids=["CN115132975B"],
                    stage_timings_ms={"stage1": 5.0, "stage2": 8.0, "stage25": 10.0, "stage3": 13.0, "stage4": 21.0},
                    stage25_skipped=False,
                    stage25_skip_reason="",
                ),
                raw={"metadata": {"retrieval_backend": "vector_hybrid"}, "steps": []},
            )

    service = PatentKbService(orchestrator=_NoStepsOrchestrator())

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert execution_result["steps"][2] == {
        "step": "stage25",
        "title": "阶段二点五",
        "message": "阶段二点五：已完成MD原文扩展检索",
        "status": "success",
    }


def test_kb_service_preserves_stage1_short_circuit_answer_without_later_stage_steps():
    class _Stage1OnlyOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            return PatentQaExecutionResult(
                success=True,
                final_answer="stage1 only answer",
                metadata=PatentQaExecutionMetadata(
                    route="kb_qa",
                    query_mode="patent staged qa",
                    source_ids=[],
                    stage_timings_ms={"stage1": 5.0},
                ),
                raw={
                    "references": [],
                    "reference_objects": [],
                    "reference_links": [],
                    "original_links": [],
                    "metadata": {"stage1_short_circuit": True},
                    "steps": [
                        {"step": "stage1", "title": "阶段一", "message": "阶段一：已完成深度预回答与检索规划", "status": "success"}
                    ],
                },
            )

    service = PatentKbService(orchestrator=_Stage1OnlyOrchestrator())

    execution_result = service.run(
        request=_make_request(),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert execution_result["answer_text"] == "stage1 only answer"
    assert execution_result["steps"] == [
        {"step": "stage1", "title": "阶段一", "message": "阶段一：已完成深度预回答与检索规划", "status": "success"}
    ]
    assert execution_result["metadata"]["stage1_short_circuit"] is True
