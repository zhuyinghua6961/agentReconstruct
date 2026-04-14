import pytest

from server.errors import codes
from server.errors.core import APIError
from server.patent.executor import PatentExecutor
from server.patent.kb_service import PatentKbService
from server.patent.pdf_service import PatentPdfService
from server.patent.stream_events import PatentContentStreamState
from server.patent.stages.synthesis import run_stage4_synthesis_with_patent_evidence
from server.patent.tabular_service import PatentTabularService
from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.retrieval_models import PatentCatalogRecord, PatentClaim, PatentDescriptionSnippet
from server.patent.retrieval_service import PatentRetrievalService
from server.schemas.request_models import PatentAskRequest


def _section_body(markdown: str, heading: str) -> str:
    text = str(markdown or "")
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    next_heading = text.find("\n## ", start)
    if next_heading < 0:
        return text[start:].strip()
    return text[start:next_heading].strip()


def _build_valid_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：围绕方案 {index} 展开研究，并给出明确的中文结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：采用表征测试与性能验证结合的方法，重点分析方案 {index}。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：面向应用方向 {index} 的性能优化场景。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 所有文献都提供了可比较的实验结论。",
            "",
            "## 总结",
            "- 这些文献展示了不同技术路线下的差异化优化方向。",
        ]
    )
    return "\n".join(lines)


def _make_request(trace_id: str = "req_123", question: str = "Explain the novelty.") -> PatentAskRequest:
    return PatentAskRequest(
        question=question,
        conversation_id=123,
        chat_history=[],
        requested_mode="patent",
        actual_mode="patent",
        route="kb_qa",
        source_scope=None,
        turn_mode="kb_only",
        kb_enabled=True,
        allow_kb_verification=False,
        used_files=[],
        execution_files=[],
        selected_file_ids=[],
        primary_file_id=None,
        file_selection={},
        trace_id=trace_id,
        options={},
    )


def _make_file_request(
    *,
    route: str,
    source_scope: str,
    turn_mode: str,
    execution_files: list[dict[str, object]],
    selected_file_ids: list[int],
    trace_id: str = "req_file",
    question: str = "Use the selected files.",
    options: dict[str, object] | None = None,
) -> PatentAskRequest:
    return PatentAskRequest(
        question=question,
        conversation_id=123,
        chat_history=[],
        requested_mode="patent",
        actual_mode="patent",
        route=route,
        source_scope=source_scope,
        turn_mode=turn_mode,
        kb_enabled="kb" in source_scope.split("+"),
        allow_kb_verification="kb" in source_scope.split("+"),
        used_files=list(execution_files),
        execution_files=list(execution_files),
        selected_file_ids=list(selected_file_ids),
        primary_file_id=selected_file_ids[0],
        file_selection={
            "strategy": "explicit_selection",
            "selected_file_ids": list(selected_file_ids),
            "source_scope": source_scope,
        },
        trace_id=trace_id,
        options=dict(options or {}),
    )


class _RecordingPdfService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, *, contract, include_kb: bool, progress_callback=None):
        self.calls.append({"contract": contract, "include_kb": include_kb, "progress_callback": progress_callback})
        return {
            "answer_text": "pdf route answer",
            "route": contract.route,
            "source_scope": contract.source_scope,
            "used_files": [item.as_payload() for item in contract.selected_execution_files],
            "file_selection": dict(contract.file_selection),
            "timings": {},
        }


class _RecordingTabularService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, *, contract, include_kb: bool, progress_callback=None):
        self.calls.append({"contract": contract, "include_kb": include_kb, "progress_callback": progress_callback})
        return {
            "answer_text": "tabular route answer",
            "route": contract.route,
            "source_scope": contract.source_scope,
            "used_files": [item.as_payload() for item in contract.selected_execution_files],
            "file_selection": dict(contract.file_selection),
            "timings": {},
        }


class _RecordingStagedRuntime:
    def __init__(self) -> None:
        self.stage1_contexts: list[dict[str, object] | None] = []
        self.calls: list[str] = []

    def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
        self.calls.append("stage1")
        self.stage1_contexts.append(conversation_context)
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

    def stage2_targeted_retrieval(self, retrieval_plan, *, user_question: str, should_cancel=None, active_stream_count=None) -> dict[str, object]:
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
                    {
                        "kind": "patent_metadata",
                        "title": "一种锂离子电池及动力车辆",
                        "abstract_text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                        "publication_number": "CN115132975B",
                    },
                    {
                        "kind": "matched_snippet",
                        "section_type": "claim",
                        "section_label": "Claim 1",
                        "text": "一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                        "anchor": {"claim_number": 1, "paragraph_id": None},
                        "scores": {"chunk_score": 0.91},
                    },
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
            "success": True,
            "final_answer": "staged executor answer",
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "reference_links": [],
            "original_links": [],
            "metadata": {"retrieval_backend": "vector_hybrid"},
        }


def _write_csv(path) -> None:
    path.write_text(
        "material,capacity_mAh,note\n"
        "LMFP,120,stable\n"
        "LFP,115,safe\n"
        "NCM,140,higher energy\n",
        encoding="utf-8",
    )



def test_stub_executor_returns_deterministic_patent_payload():
    executor = PatentExecutor()
    request = _make_request()
    context = {
        "trace_id": "req_123",
        "chat_history": [{"role": "user", "content": "Earlier turn"}],
        "summary": {"short_summary": "Earlier patent context"},
        "conversation_state": {"last_turn_route": "kb_qa"},
    }

    first = executor.execute(request=request, context=context)
    second = executor.execute(request=request, context=context)

    assert first == second
    assert first["answer_text"] == "Patent Phase 1 stub answer: Explain the novelty."
    assert first["route"] == "kb_qa"
    assert first["references"] == []
    assert first["steps"][0]["title"] == "Patent Stub"
    assert first["timings"]["stub_total_ms"] == 1


def test_executor_uses_retrieval_service_when_provided():
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
    executor = PatentExecutor(retrieval_service=retrieval_service)

    result = executor.execute(request=_make_request(question="Please summarize CN123456789A"), context={"trace_id": "req_123"})

    assert result["answer_text"].startswith("Patent retrieval answer:")
    assert result["references"] == ["CN123456789A"]
    assert result["reference_objects"][0]["canonical_patent_id"] == "CN123456789A"
    assert result["original_links"][0]["viewer_uri"] == "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html"
    assert result["metadata"]["retrieval_backend"] == "exact_id"
    assert result["metadata"]["retrieval_version"] == "retrieval-v1"
    assert result["metadata"]["catalog_index_version"] == "catalog-v1"


def test_executor_delegates_to_kb_service_before_direct_retrieval():
    class _FailingRetrievalService:
        def retrieve(self, *, question, context=None):
            raise AssertionError("direct retrieval path should not be used")

    class _FakeKbService(PatentKbService):
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object] | None]] = []

        def run(self, *, request, runtime=None, conversation_context=None):
            self.calls.append((request.question, conversation_context))
            return {
                "answer_text": "kb service answer",
                "route": "kb_qa",
                "query_mode": "patent staged qa",
                "steps": [],
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "original_links": [],
                "metadata": {},
                "timings": {},
            }

    kb_service = _FakeKbService()
    executor = PatentExecutor(
        retrieval_service=_FailingRetrievalService(),
        kb_service=kb_service,
    )

    result = executor.execute(request=_make_request(question="staged execution"), context={"recent_turns_for_llm": []})

    assert kb_service.calls == [
        (
            "staged execution",
            {
                "recent_turns_for_llm": [],
                "summary_for_llm": {},
                "conversation_state": {},
                "source_selection": {"source_scope": "kb", "selected_file_ids": []},
            },
        )
    ]
    assert result["answer_text"] == "kb service answer"


def test_executor_forwards_runtime_to_kb_service_boundary():
    class _FakeKbService(PatentKbService):
        def __init__(self) -> None:
            self.calls: list[tuple[str, object | None]] = []

        def run(self, *, request, runtime=None, conversation_context=None):
            self.calls.append((request.question, runtime))
            return {
                "answer_text": "kb service answer",
                "route": "kb_qa",
                "query_mode": "patent staged qa",
                "steps": [],
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "original_links": [],
                "metadata": {},
                "timings": {},
            }

    runtime = object()
    kb_service = _FakeKbService()
    executor = PatentExecutor(kb_service=kb_service, runtime=runtime)

    result = executor.execute(request=_make_request(question="staged execution"), context={"recent_turns_for_llm": []})

    assert kb_service.calls == [("staged execution", runtime)]
    assert result["answer_text"] == "kb service answer"


def test_executor_returns_not_found_payload_for_retrieval_miss():
    retrieval_service = PatentRetrievalService(
        identity_registry={},
        catalog_records=[],
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
    )
    executor = PatentExecutor(retrieval_service=retrieval_service)
    request = PatentAskRequest(
        question="utterly unmatched patent query",
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
        trace_id="req_404",
        options={},
    )

    result = executor.execute(request=request, context={"trace_id": "req_404"})

    assert result["answer_text"] == "Patent retrieval found no matching results."
    assert result["references"] == []
    assert result["metadata"]["retrieval_backend"] == "metadata_lexical"
    assert result["metadata"]["not_found"] is True


def test_executor_uses_retrieval_answer_text_when_available():
    retrieval_service = PatentRetrievalService(
        identity_registry={"CN115132975B": "CN115132975B"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN115132975B",
                publication_number="CN115132975B",
                application_number="CN202110320984.1",
                title="一种锂离子电池及动力车辆",
                abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                claims=[PatentClaim(claim_number=1, text="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。")],
                description_snippets=[PatentDescriptionSnippet(paragraph_id="p-007", text="实施例表明该复配方案在高 SOC 充电时不易析锂。")],
                country="CN",
                kind_code="B",
                publication_date="2024-09-10",
                provider="patent_source_x",
                original_available=True,
            )
        ],
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        answer_builder=lambda *, question, retrieval_outcome, context: "结合说明书与表格数据，这件专利表明 LMFP/LFP/三元复配可在改善高 SOC 充电安全的同时保持低 SOC 放电功率，但比例窗口较窄，量产风险主要在材料一致性与倍率稳定性。",
    )
    executor = PatentExecutor(retrieval_service=retrieval_service)

    result = executor.execute(
        request=_make_request(question="钠离子电池在储能领域可能替代 LFP，从专利角度如何评估这种技术替代的时间窗口和风险？"),
        context={"trace_id": "req_answer"},
    )

    assert "比例窗口较窄" in result["answer_text"]
    assert result["answer_text"] != "Patent retrieval answer: 一种锂离子电池及动力车辆"


def test_executor_normalizes_raw_persistence_context_before_staged_runtime():
    runtime = _RecordingStagedRuntime()
    executor = PatentExecutor(runtime=runtime)

    result = executor.execute(
        request=_make_request(question="staged execution"),
        context={
            "trace_id": "req_ctx",
            "chat_history": [{"role": "assistant", "content": "Earlier turn"}],
            "summary": {"short_summary": "Earlier patent context"},
            "conversation_state": {"last_turn_route": "kb_qa"},
            "snapshot": {"raw": "persistence"},
            "pending_overlay": {"raw": "overlay"},
        },
    )

    assert result["answer_text"] == "staged executor answer"
    assert runtime.calls == ["stage1", "stage2", "extract", "stage25", "stage3", "stage4"]
    assert runtime.stage1_contexts == [
        {
            "recent_turns_for_llm": [{"role": "assistant", "content": "Earlier turn"}],
            "summary_for_llm": {"short_summary": "Earlier patent context"},
            "conversation_state": {"last_turn_route": "kb_qa"},
            "source_selection": {"source_scope": "kb", "selected_file_ids": []},
        }
    ]


def test_executor_staged_runtime_emits_readable_patent_citations_for_stream_and_final_payload():
    class _ReadableCitationRuntime(_RecordingStagedRuntime):
        class _StreamingBuilder:
            def __call__(self, **kwargs):
                raise AssertionError("stream path should be used")

            def stream(self, *, question, retrieval_outcome, context):
                del question, retrieval_outcome, context
                yield "结论来自专利 (patent_id=CN115132975B)。"
                yield "另有外部专利 (patent_id=CN000000000A)。"

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

    runtime = _ReadableCitationRuntime()
    executor = PatentExecutor(runtime=runtime)
    streamed_chunks: list[str] = []

    result = executor.execute_with_progress(
        request=_make_request(question="staged execution"),
        context={
            "trace_id": "req_ctx",
            "chat_history": [{"role": "assistant", "content": "Earlier turn"}],
            "summary": {"short_summary": "Earlier patent context"},
            "conversation_state": {"last_turn_route": "kb_qa"},
        },
        content_callback=streamed_chunks.append,
    )

    combined_stream = "".join(streamed_chunks)
    assert "patent_id=" not in combined_stream
    assert "CN115132975B" in combined_stream
    assert "patent_id=" not in str(result["answer_text"] or "")
    assert "CN115132975B" in str(result["answer_text"] or "")


def test_executor_dispatches_pdf_route_to_patent_pdf_service():
    class _FailingKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            raise AssertionError("kb path should not be used for pdf_qa")

    pdf_service = _RecordingPdfService()
    executor = PatentExecutor(
        kb_service=_FailingKbService(),
        pdf_service=pdf_service,
    )

    result = executor.execute(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf",
        ),
        context={"trace_id": "req_pdf"},
    )

    assert result["answer_text"] == "pdf route answer"
    assert result["route"] == "pdf_qa"
    assert result["source_scope"] == "pdf"
    assert pdf_service.calls[0]["include_kb"] is False
    assert pdf_service.calls[0]["contract"].selected_file_ids == [11]


def test_executor_real_pdf_route_returns_shared_ask_payload_shape():
    executor = PatentExecutor()

    result = executor.execute(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_real",
        ),
        context={"trace_id": "req_pdf_real"},
    )

    assert result["route"] == "pdf_qa"
    assert result["source_scope"] == "pdf"
    assert result["query_mode"] == "patent_pdf_qa"
    assert result["answer_text"]
    assert result["used_files"] == [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}]
    assert result["file_selection"]["selected_file_ids"] == [11]


def test_executor_pdf_route_uses_pdf_text_summary_when_local_path_is_available(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries and reports better cycle life.",
        answer_question_fn=lambda **kwargs: "真实总结：本文研究硅负极包覆方案，并报告循环寿命改善。",
    )
    executor = PatentExecutor(pdf_service=pdf_service)

    result = executor.execute(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_real_pdf_summary",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_summary"
    assert "真实总结" in result["answer_text"]
    assert "Patent PDF route answered" not in result["answer_text"]


def test_executor_file_routes_emit_progress_steps_before_returning_result(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=lambda **kwargs: "真实总结：本文研究硅负极包覆方案。",
        )
    )
    progress_steps: list[dict[str, object]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_progress_pdf",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=progress_steps.append,
        content_callback=None,
    )

    assert [step["step"] for step in progress_steps] == [
        "dispatch",
        "pdf_extract",
        "pdf_extract",
        "pdf_answer",
        "pdf_answer",
    ]
    assert progress_steps[0]["message"] == "进入 PDF 问答分支"
    assert progress_steps[-1]["status"] == "success"
    assert result["steps"][0]["step"] == "dispatch"
    assert result["steps"][-1]["step"] == "pdf_answer"


def test_executor_file_routes_stream_pdf_answer_content_before_returning_result(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    answer_text = "真实总结：本文研究硅负极包覆方案，并报告循环寿命改善与倍率性能提升。"
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=lambda **kwargs: answer_text,
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    streamed_chunks = [item[1] for item in events if item[0] == "content"]
    assert len(streamed_chunks) >= 2
    assert "".join(streamed_chunks) == result["answer_text"]
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    final_success_index = max(
        index for index, item in enumerate(events) if item[0] == "step" and item[1] == "pdf_answer" and item[2] == "success"
    )
    assert final_success_index < first_content_index


def test_executor_pdf_unreadable_fallback_emits_final_steps_before_failure_body(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "",
            answer_question_fn=lambda **kwargs: "不应该进入成功生成",
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_unreadable",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_unavailable"
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    final_step_index = max(
        index for index, item in enumerate(events) if item[0] == "step" and item[1] in {"pdf_extract", "pdf_answer"}
    )
    assert final_step_index < first_content_index


def test_executor_capability_enabled_file_route_does_not_emit_final_end_on_stream_failure(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")

    class _BrokenPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            if callable(content_callback):
                content_callback("partial final body ")
            raise RuntimeError("pdf stream interrupted")

    executor = PatentExecutor(pdf_service=_BrokenPdfService())
    streamed_payloads: list[object] = []

    with pytest.raises(RuntimeError, match="pdf stream interrupted"):
        executor.execute_with_progress(
            request=_make_file_request(
                question="请总结这篇文献的研究内容",
                route="pdf_qa",
                source_scope="pdf",
                turn_mode="file_only",
                execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
                selected_file_ids=[11],
                trace_id="req_stream_pdf_failure_typed",
                options={"patent_stream_capability": "preview_v1"},
            ),
            context={"recent_turns_for_llm": []},
            content_callback=streamed_payloads.append,
        )

    typed_events = [payload for payload in streamed_payloads if isinstance(payload, dict)]
    assert typed_events
    assert typed_events[0]["content_role"] == "final"
    assert typed_events[0]["content_source"] == "pdf"
    assert typed_events[0]["content_phase"] == "start"
    assert all(event.get("content_phase") != "end" for event in typed_events)


def test_executor_pdf_streaming_generator_emits_content_before_final_success(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")

    def _streaming_answer(**kwargs):
        yield "真实总结：本文研究硅负极"
        yield "包覆方案，并报告循环寿命改善。"

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=_streaming_answer,
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_generator",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_summary"
    streamed_answer = "".join(item[1] for item in events if item[0] == "content")
    assert streamed_answer == result["answer_text"]
    assert "## 研究目的和背景" in streamed_answer
    assert "## 研究方法/实验设计" in streamed_answer
    assert "## 主要发现和结果" in streamed_answer
    assert "## 结论和意义" in streamed_answer
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    running_index = min(
        index for index, item in enumerate(events) if item[0] == "step" and item[1] == "pdf_answer" and item[2] == "running"
    )
    final_success_index = max(
        index for index, item in enumerate(events) if item[0] == "step" and item[1] == "pdf_answer" and item[2] == "success"
    )
    last_content_index = max(index for index, item in enumerate(events) if item[0] == "content")
    assert running_index < first_content_index
    assert first_content_index < final_success_index
    assert last_content_index < final_success_index


def test_executor_pdf_streaming_generator_emits_incremental_content_before_second_yield(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    timeline: list[tuple[str, str]] = []

    def _streaming_answer(**kwargs):
        timeline.append(("producer", "before-first"))
        yield "真实总结：本文研究硅负极"
        timeline.append(("producer", "between-yields"))
        yield "包覆方案，并报告循环寿命改善。"
        timeline.append(("producer", "after-second"))

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=_streaming_answer,
        )
    )

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_incremental_generator",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=lambda chunk: timeline.append(("content", str(chunk or ""))),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_summary"
    first_content_index = next(index for index, item in enumerate(timeline) if item[0] == "content")
    between_yields_index = timeline.index(("producer", "between-yields"))
    assert between_yields_index < first_content_index


def test_executor_pdf_streaming_generator_partial_heading_opening_keeps_stream_final_parity(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    streamed_chunks: list[str] = []

    def _streaming_answer(**kwargs):
        yield "结论：本文研究硅负极"
        yield "包覆方案，并报告循环寿命改善。"

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=_streaming_answer,
        )
    )

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_partial_heading",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_chunks.append,
    )

    streamed_answer = "".join(streamed_chunks)
    assert streamed_answer == result["answer_text"]
    assert "## 研究目的和背景" in streamed_answer
    assert "## 研究方法/实验设计" in streamed_answer
    assert "## 主要发现和结果" in streamed_answer
    assert "## 结论和意义" in streamed_answer
    assert "注*" in streamed_answer


def test_executor_pdf_streaming_generator_leading_whitespace_keeps_stream_final_parity(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    streamed_chunks: list[str] = []

    def _streaming_answer(**kwargs):
        yield "\n结论：本文研究硅负极"
        yield "包覆方案，并报告循环寿命改善。"

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=_streaming_answer,
        )
    )

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_leading_whitespace",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_chunks.append,
    )

    streamed_answer = "".join(streamed_chunks)
    assert streamed_answer == result["answer_text"]
    assert not streamed_answer.startswith("\n")


def test_executor_pdf_streaming_generator_trailing_whitespace_keeps_stream_final_parity(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    streamed_chunks: list[str] = []

    def _streaming_answer(**kwargs):
        yield "真实总结：本文研究硅负极"
        yield "包覆方案，并报告循环寿命改善。\n"

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=_streaming_answer,
        )
    )

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_trailing_whitespace",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_chunks.append,
    )

    streamed_answer = "".join(streamed_chunks)
    assert streamed_answer == result["answer_text"]
    assert not streamed_answer.endswith("\n")


def test_executor_pdf_streaming_generator_whitespace_only_first_chunk_keeps_stream_final_parity(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    streamed_chunks: list[str] = []

    def _streaming_answer(**kwargs):
        yield "\n\n"
        yield "## 结论\n真实总结：本文研究硅负极包覆方案，并报告循环寿命改善。\n\n## 证据\n- 证据 1\n\n## 对比\n- 对比 1\n\n## 限制\n- 限制 1"

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=_streaming_answer,
        )
    )

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_whitespace_first_chunk",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_chunks.append,
    )

    streamed_answer = "".join(streamed_chunks)
    assert streamed_answer == result["answer_text"]
    assert streamed_answer.startswith("## 研究目的和背景")


def test_executor_pdf_streaming_structured_answer_missing_limitations_keeps_stream_final_parity(tmp_path):
    pdf_path = tmp_path / "structured-missing-limitations.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    streamed_chunks: list[str] = []

    def _streaming_answer(**kwargs):
        yield "\n".join(
            [
                "## 研究目的和背景",
                "- 原文说明高倍率充电场景的安全性挑战。",
                "",
                "## 研究方法/实验设计",
                "- 对 LMFP/LFP 复配体系进行对比测试。",
                "",
                "## 主要发现和结果",
                "- 复配体系改善了高倍率充电表现。",
                "",
                "## 结论和意义",
                "- 该路线有助于兼顾性能与安全性。",
                "",
                "注*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。",
            ]
        )

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "This paper studies LMFP/LFP blending for safer charging. "
                "Method setup compares blended and baseline electrodes. "
                "Results show safer high-rate charging. "
                "The conclusion notes that long-cycle validation is still limited."
            ),
            answer_question_fn=_streaming_answer,
        )
    )

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这篇文献的研究内容",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "structured-missing-limitations.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_structured_missing_limitations",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_chunks.append,
    )

    streamed_answer = "".join(streamed_chunks)
    assert streamed_answer == result["answer_text"]
    assert streamed_answer.count("## 局限性") == 1
    limitation_section = _section_body(streamed_answer, "局限性")
    assert limitation_section.startswith("- ")
    assert "long-cycle validation is still limited" in limitation_section


def test_executor_pdf_compare_route_records_compare_steps_and_metadata_parity(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A.\n\nResults A observed.\n\nConclusion A final."
                if path == str(pdf_path_a)
                else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
            ),
            answer_question_fn=lambda **kwargs: _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        )
    )
    progress_steps: list[dict[str, object]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            question="对比一下这两篇文献的内容",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
                {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
            ],
            selected_file_ids=[11, 12],
            trace_id="req_compare_pdf",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=progress_steps.append,
        content_callback=None,
    )

    assert [step["step"] for step in progress_steps] == [
        "dispatch",
        "pdf_extract",
        "pdf_extract",
        "multi_pdf_compare",
        "multi_pdf_compare",
        "pdf_answer",
        "pdf_answer",
    ]
    assert result["metadata"]["steps"] == result["steps"]
    assert [step["step"] for step in result["steps"]] == [
        "dispatch",
        "pdf_extract",
        "multi_pdf_compare",
        "pdf_answer",
    ]
    assert result["steps"][2]["status"] == "success"
    assert result["steps"][3]["status"] == "success"


def test_executor_pdf_compare_success_emits_final_step_before_first_content(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A.\n\nResults A observed.\n\nConclusion A final."
                if path == str(pdf_path_a)
                else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
            ),
            answer_question_fn=lambda **kwargs: _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            question="对比一下这两篇文献的内容",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
                {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
            ],
            selected_file_ids=[11, 12],
            trace_id="req_compare_pdf_step_before_content",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "## 具体内容对比" in result["answer_text"]
    assert "## 研究方法差异" in result["answer_text"]
    assert "## 应用领域差异" in result["answer_text"]
    assert "## 相同点" in result["answer_text"]
    assert "## 总结" in result["answer_text"]
    assert result["answer_text"].index("## 具体内容对比") < result["answer_text"].index("## 研究方法差异") < result["answer_text"].index("## 应用领域差异") < result["answer_text"].index("## 相同点") < result["answer_text"].index("## 总结")
    assert "### 文献 #1 核心内容（根据PDF原文）" in result["answer_text"]
    assert "### 文献 #2 核心内容（根据PDF原文）" in result["answer_text"]
    assert "### 文献 #1 采用的研究方法" in result["answer_text"]
    assert "### 文献 #2 采用的研究方法" in result["answer_text"]
    assert "### 文献 #1 关注的应用领域" in result["answer_text"]
    assert "### 文献 #2 关注的应用领域" in result["answer_text"]
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    final_success_index = max(
        index
        for index, item in enumerate(events)
        if item[0] == "step" and item[1] == "pdf_answer" and item[2] == "success"
    )
    assert final_success_index < first_content_index


def test_executor_pdf_compare_streaming_generator_emits_content_before_final_success(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")

    def _streaming_compare(**kwargs):
        answer = _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"])
        midpoint = len(answer) // 2
        yield answer[:midpoint]
        yield answer[midpoint:]

    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A.\n\nResults A observed.\n\nConclusion A final."
                if path == str(pdf_path_a)
                else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
            ),
            answer_question_fn=_streaming_compare,
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            question="对比一下这两篇文献的内容",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
                {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
            ],
            selected_file_ids=[11, 12],
            trace_id="req_compare_pdf_streaming",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "## 具体内容对比" in result["answer_text"]
    assert "## 研究方法差异" in result["answer_text"]
    assert "## 应用领域差异" in result["answer_text"]
    assert "## 相同点" in result["answer_text"]
    assert "## 总结" in result["answer_text"]
    assert result["answer_text"].index("## 具体内容对比") < result["answer_text"].index("## 研究方法差异") < result["answer_text"].index("## 应用领域差异") < result["answer_text"].index("## 相同点") < result["answer_text"].index("## 总结")
    assert "### 文献 #1 核心内容（根据PDF原文）" in result["answer_text"]
    assert "### 文献 #2 核心内容（根据PDF原文）" in result["answer_text"]
    assert "### 文献 #1 采用的研究方法" in result["answer_text"]
    assert "### 文献 #2 采用的研究方法" in result["answer_text"]
    assert "### 文献 #1 关注的应用领域" in result["answer_text"]
    assert "### 文献 #2 关注的应用领域" in result["answer_text"]
    final_success_index = max(
        index
        for index, item in enumerate(events)
        if item[0] == "step" and item[1] == "pdf_answer" and item[2] == "success"
    )
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    assert final_success_index < first_content_index


def test_executor_pdf_compare_failure_emits_error_steps_before_failure_body(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A.\n\nResults A observed.\n\nConclusion A final."
                if path == str(pdf_path_a)
                else ""
            ),
            answer_question_fn=lambda **kwargs: "不应该进入成功比较生成",
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            question="对比一下这两篇文献的内容",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
                {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
            ],
            selected_file_ids=[11, 12],
            trace_id="req_compare_pdf_fail",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert ("step", "multi_pdf_compare", "error") in events
    assert ("step", "pdf_answer", "error") in events
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    last_error_index = max(
        index
        for index, item in enumerate(events)
        if item[0] == "step" and item[1] in {"multi_pdf_compare", "pdf_answer"} and item[2] == "error"
    )
    assert last_error_index < first_content_index


def test_executor_pdf_compare_all_unreadable_emits_compare_error_steps_before_failure_body(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "",
            answer_question_fn=lambda **kwargs: "不应该进入成功比较生成",
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            question="对比一下这两篇文献的内容",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
                {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
            ],
            selected_file_ids=[11, 12],
            trace_id="req_compare_pdf_all_unreadable",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert ("step", "multi_pdf_compare", "error") in events
    assert ("step", "pdf_answer", "error") in events
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    last_error_index = max(
        index
        for index, item in enumerate(events)
        if item[0] == "step" and item[1] in {"multi_pdf_compare", "pdf_answer"} and item[2] == "error"
    )
    assert last_error_index < first_content_index


def test_executor_pdf_compare_empty_model_answer_returns_failure_not_exception(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A.\n\nResults A observed.\n\nConclusion A final."
                if path == str(pdf_path_a)
                else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
            ),
            answer_question_fn=lambda **kwargs: "",
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            question="对比一下这两篇文献的内容",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
                {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
            ],
            selected_file_ids=[11, 12],
            trace_id="req_compare_pdf_empty_answer",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert ("step", "pdf_answer", "error") in events
    assert "无法完成完整比较" in result["answer_text"]


def test_executor_pdf_compare_too_many_selected_documents_returns_failure_not_success(tmp_path):
    execution_files = []
    selected_ids = []
    for index in range(5):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        execution_files.append(
            {"file_id": 600 + index, "file_type": "pdf", "file_name": f"paper-{index + 1}.pdf", "local_path": str(pdf_path)}
        )
        selected_ids.append(600 + index)
    calls: list[dict[str, object]] = []
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "Abstract.\n\nResults observed.\n\nConclusion final.",
            answer_question_fn=lambda **kwargs: calls.append(dict(kwargs)) or (_ for _ in ()).throw(AssertionError("compare generation should not run")),
        )
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            question="对比一下这五篇文献",
            execution_files=execution_files,
            selected_file_ids=selected_ids,
            trace_id="req_compare_pdf_too_many_docs",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert ("step", "multi_pdf_compare", "error") in events
    assert "超过 4 篇文献" in result["answer_text"]
    assert "缩小比较范围" in result["answer_text"]
    assert calls == []


def test_executor_dispatches_tabular_route_to_patent_tabular_service():
    class _FailingKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            raise AssertionError("kb path should not be used for tabular_qa")

    tabular_service = _RecordingTabularService()
    executor = PatentExecutor(
        kb_service=_FailingKbService(),
        tabular_service=tabular_service,
    )

    result = executor.execute(
        request=_make_file_request(
            route="tabular_qa",
            source_scope="table",
            turn_mode="file_only",
            execution_files=[{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}],
            selected_file_ids=[33],
            trace_id="req_table",
        ),
        context={"trace_id": "req_table"},
    )

    assert result["answer_text"] == "tabular route answer"
    assert result["route"] == "tabular_qa"
    assert result["source_scope"] == "table"
    assert tabular_service.calls[0]["include_kb"] is False
    assert tabular_service.calls[0]["contract"].selected_file_ids == [33]


def test_executor_real_tabular_route_returns_shared_ask_payload_shape():
    executor = PatentExecutor()

    result = executor.execute(
        request=_make_file_request(
            route="tabular_qa",
            source_scope="table",
            turn_mode="file_only",
            execution_files=[{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}],
            selected_file_ids=[33],
            trace_id="req_table_real",
        ),
        context={"trace_id": "req_table_real"},
    )

    assert result["route"] == "tabular_qa"
    assert result["source_scope"] == "table"
    assert result["query_mode"] == "patent_tabular_qa"
    assert result["answer_text"]
    assert result["used_files"] == [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}]
    assert result["file_selection"]["selected_file_ids"] == [33]


def test_executor_tabular_route_uses_real_table_content_when_local_path_is_available(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)
    tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 更安全，NCM 能量更高。",
    )
    executor = PatentExecutor(tabular_service=tabular_service)

    result = executor.execute(
        request=_make_file_request(
            route="tabular_qa",
            source_scope="table",
            turn_mode="file_only",
            execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)}],
            selected_file_ids=[33],
            trace_id="req_real_table_summary",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert result["metadata"]["answer_mode"] == "table_execution_summary"
    assert "匹配工作表" in result["metadata"]["table_evidence_context"]
    assert "真实表格总结" in result["answer_text"]
    assert "Patent tabular route answered" not in result["answer_text"]


def test_executor_tabular_route_streams_fastqa_structured_answer(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)
    tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 更安全，NCM 能量更高。",
    )
    executor = PatentExecutor(tabular_service=tabular_service)
    streamed_chunks: list[str] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这个表格的重点",
            route="tabular_qa",
            source_scope="table",
            turn_mode="file_only",
            execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)}],
            selected_file_ids=[33],
            trace_id="req_stream_real_table_summary",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_chunks.append,
    )

    streamed_answer = "".join(streamed_chunks)
    assert streamed_answer == result["answer_text"]
    assert "## 研究目的和背景" in streamed_answer
    assert "## 研究方法/实验设计" in streamed_answer
    assert "## 主要发现和结果" in streamed_answer
    assert "## 结论和意义" in streamed_answer
    assert "注*" in streamed_answer
    result_section = _section_body(streamed_answer, "主要发现和结果")
    assert "列:" not in result_section
    assert "工作表:" not in result_section


def test_executor_tabular_fallback_streams_fastqa_structured_answer(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)
    tabular_service = PatentTabularService(answer_question_fn=None)
    executor = PatentExecutor(tabular_service=tabular_service)
    streamed_chunks: list[str] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请总结这个表格的重点",
            route="tabular_qa",
            source_scope="table",
            turn_mode="file_only",
            execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)}],
            selected_file_ids=[33],
            trace_id="req_stream_table_fallback",
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_chunks.append,
    )

    streamed_answer = "".join(streamed_chunks)
    assert streamed_answer == result["answer_text"]
    assert "## 研究目的和背景" in streamed_answer
    assert "## 研究方法/实验设计" in streamed_answer
    assert "## 主要发现和结果" in streamed_answer
    assert "## 结论和意义" in streamed_answer
    assert "注*" in streamed_answer
    result_section = _section_body(streamed_answer, "主要发现和结果")
    assert "列:" not in result_section
    assert "工作表:" not in result_section


@pytest.mark.parametrize(
    ("source_scope", "execution_files", "selected_file_ids", "expected_handler", "expected_used_files"),
    [
        (
            "pdf+table",
            [
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
            ],
            [11, 33],
            "hybrid",
            [
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
            ],
        ),
    ],
)
def test_executor_real_hybrid_routes_return_shared_ask_payload_shape(
    source_scope,
    execution_files,
    selected_file_ids,
    expected_handler,
    expected_used_files,
):
    executor = PatentExecutor()

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope=source_scope,
            turn_mode="mixed" if "kb" in source_scope else "file_only",
            execution_files=execution_files,
            selected_file_ids=selected_file_ids,
            trace_id=f"req_{source_scope.replace('+', '_')}",
        ),
        context={"trace_id": "req_hybrid_real"},
    )

    assert result["handler"] == expected_handler
    assert result["route"] == "hybrid_qa"
    assert result["source_scope"] == source_scope
    assert result["query_mode"] == "patent_hybrid_qa"
    assert result["answer_text"]
    assert result["used_files"] == expected_used_files


@pytest.mark.parametrize(
    ("source_scope", "execution_files", "selected_file_ids"),
    [
        ("pdf+kb", [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}], [11]),
        ("table+kb", [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}], [33]),
        (
            "pdf+table+kb",
            [
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
            ],
            [11, 33],
        ),
    ],
)
def test_executor_requires_live_kb_backend_for_kb_enabled_file_routes(source_scope, execution_files, selected_file_ids):
    executor = PatentExecutor()

    with pytest.raises(APIError) as exc_info:
        executor.execute(
            request=_make_file_request(
                route="hybrid_qa",
                source_scope=source_scope,
                turn_mode="mixed",
                execution_files=execution_files,
                selected_file_ids=selected_file_ids,
                trace_id=f"req_{source_scope.replace('+', '_')}",
            ),
            context={"trace_id": "req_hybrid_real"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY
    assert exc_info.value.error == "service_not_ready"


@pytest.mark.parametrize(
    ("source_scope", "execution_files", "selected_file_ids"),
    [
        ("pdf+kb", [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}], [11]),
        ("table+kb", [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}], [33]),
        (
            "pdf+table+kb",
            [
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
            ],
            [11, 33],
        ),
    ],
)
def test_executor_kb_enabled_hybrid_routes_invoke_patent_kb_participation(
    source_scope,
    execution_files,
    selected_file_ids,
):
    class _RecordingKbService(PatentKbService):
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def run(self, *, request, runtime=None, conversation_context=None):
            self.calls.append(
                {
                    "route": request.route,
                    "source_scope": request.source_scope,
                    "conversation_context": conversation_context,
                }
            )
            return {
                "answer_text": "kb contribution",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"title": "Patent KB", "message": "KB participated."}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "patent-local-kb"},
                "timings": {"kb_ms": 7},
            }

    kb_service = _RecordingKbService()
    executor = PatentExecutor(kb_service=kb_service)

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope=source_scope,
            turn_mode="mixed",
            execution_files=execution_files,
            selected_file_ids=selected_file_ids,
            trace_id=f"req_{source_scope.replace('+', '_')}_kb",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert kb_service.calls == [
        {
            "route": "hybrid_qa",
            "source_scope": source_scope,
            "conversation_context": {
                "recent_turns_for_llm": [],
                "summary_for_llm": {},
                "conversation_state": {},
                "source_selection": {"source_scope": source_scope, "selected_file_ids": selected_file_ids},
            },
        }
    ]
    assert "kb contribution" in result["answer_text"]
    assert "Patent KB participation:" not in result["answer_text"]
    assert result["references"] == ["CN123456789A"]
    assert result["metadata"]["retrieval_backend"] == "patent-local-kb"
    assert result["timings"]["kb_ms"] == 7


def test_executor_hybrid_merge_uses_kb_answer_when_file_answer_is_empty():
    class _EmptyPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None):
            return {
                "answer_text": "",
                "route": contract.route,
                "source_scope": contract.source_scope,
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
                "timings": {"pdf_ms": 3},
            }

    class _RecordingKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            return {
                "answer_text": "kb contribution",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"title": "Patent KB", "message": "KB participated."}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "patent-local-kb"},
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_RecordingKbService(),
        pdf_service=_EmptyPdfService(),
    )

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_empty",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert "kb contribution" in result["answer_text"]
    assert "Patent KB participation:" not in result["answer_text"]
    assert "文件证据不足" in result["answer_text"]
    assert result["metadata"]["kb_participated"] is True


def test_executor_kb_enabled_hybrid_routes_merge_file_and_kb_evidence():
    class _EvidencePdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None):
            return {
                "answer_text": "file contribution",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"title": "Patent PDF", "message": "PDF participated."}],
                "references": ["FILE-REF-1"],
                "reference_objects": [{"source_type": "pdf", "file_id": 11}],
                "reference_links": [{"type": "pdf_view", "file_id": 11}],
                "original_links": [{"type": "pdf_original", "file_id": 11}],
                "metadata": {"file_backend": "patent-pdf"},
                "timings": {"pdf_ms": 3},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
            }

    class _EvidenceKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            return {
                "answer_text": "kb contribution",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"title": "Patent KB", "message": "KB participated."}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [{"type": "original_view", "canonical_patent_id": "CN123456789A"}],
                "original_links": [{"type": "original_view", "canonical_patent_id": "CN123456789A"}],
                "metadata": {"retrieval_backend": "patent-local-kb"},
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_EvidenceKbService(),
        pdf_service=_EvidencePdfService(),
    )

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_merge_evidence",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert "file contribution" in result["answer_text"]
    assert "kb contribution" in result["answer_text"]
    assert "Patent KB participation:" not in result["answer_text"]
    assert result["references"] == ["CN123456789A"]
    assert result["reference_objects"] == [{"canonical_patent_id": "CN123456789A"}]
    assert result["reference_links"] == [
        {"type": "pdf_view", "file_id": 11},
        {"type": "original_view", "canonical_patent_id": "CN123456789A"},
    ]
    assert result["original_links"] == [
        {"type": "pdf_original", "file_id": 11},
        {"type": "original_view", "canonical_patent_id": "CN123456789A"},
    ]


def test_executor_pdf_kb_hybrid_route_streams_file_and_kb_answer_in_final_order(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_answer = "真实 PDF 总结：本文提出硅负极包覆方法，并报告循环寿命改善。"
    kb_answer = "这是知识库补充：该方向在专利布局上集中于包覆材料和热稳定性。"

    class _StreamingKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            if callable(content_callback):
                content_callback(kb_answer[:14])
                content_callback(kb_answer[14:])
            return {
                "answer_text": kb_answer,
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"title": "Patent KB", "message": "KB participated."}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "patent-local-kb"},
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_StreamingKbService(),
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies silicon anode coating for lithium batteries.",
            answer_question_fn=lambda **kwargs: pdf_answer,
        ),
    )
    events: list[tuple[str, str, str]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_kb",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=lambda step: events.append(("step", str(step.get("step") or ""), str(step.get("status") or ""))),
        content_callback=lambda chunk: events.append(("content", str(chunk or ""), "")),
    )

    streamed_chunks = [item[1] for item in events if item[0] == "content"]
    assert len(streamed_chunks) >= 3
    assert "".join(streamed_chunks) == result["answer_text"]
    assert "Patent KB participation:" not in "".join(streamed_chunks)
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    final_hybrid_success_index = max(
        index
        for index, item in enumerate(events)
        if item[0] == "step" and item[1] == "hybrid_answer" and item[2] == "success"
    )
    assert first_content_index < final_hybrid_success_index


def test_executor_dispatches_file_only_hybrid_route_to_patent_file_scaffold():
    class _FailingKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            raise AssertionError("kb path should not be used for file-only hybrid_qa")

    executor = PatentExecutor(
        kb_service=_FailingKbService(),
    )

    result = executor.execute(
        request=_make_file_request(
            question="请结合 PDF 和表格总结结论",
            route="hybrid_qa",
            source_scope="pdf+table",
            turn_mode="file_only",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
            ],
            selected_file_ids=[11, 33],
            trace_id="req_hybrid",
        ),
        context={"trace_id": "req_hybrid"},
    )

    assert result["handler"] == "hybrid"
    assert result["route"] == "hybrid_qa"
    assert result["source_scope"] == "pdf+table"
    assert result["kb_enabled"] is False


def test_executor_hybrid_route_uses_real_pdf_and_table_content_when_local_paths_are_available(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )
    executor = PatentExecutor(
        pdf_service=pdf_service,
        tabular_service=tabular_service,
    )

    result = executor.execute(
        request=_make_file_request(
            question="请结合 PDF 和表格总结结论",
            route="hybrid_qa",
            source_scope="pdf+table",
            turn_mode="file_only",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)},
                {"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)},
            ],
            selected_file_ids=[11, 33],
            trace_id="req_real_hybrid_summary",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert result["metadata"]["answer_mode"] == "hybrid_unified_synthesis"
    assert "LMFP/LFP" in result["answer_text"]
    assert "120mAh" in result["answer_text"]
    assert "PDF 部分：" not in result["answer_text"]
    assert "表格部分：" not in result["answer_text"]
    assert "Patent hybrid route combined selected PDF and table files" not in result["answer_text"]
    assert result["answer_text"].startswith("## 研究目的和背景")
    assert "PDF 原文证据：" not in result["answer_text"]
    assert "表格执行结果：" not in result["answer_text"]
    assert "## 局限性" in result["answer_text"]
    assert "注*" in result["answer_text"]
    assert "==== 文献 " not in result["answer_text"]
    assert not _section_body(result["answer_text"], "结论和意义").startswith("表格结果显示：")
    assert "真实 PDF 总结：" not in result["answer_text"]
    assert "LMFP/LFP 复配改善了充电安全性" in _section_body(result["answer_text"], "结论和意义")
    assert "列:" not in _section_body(result["answer_text"], "主要发现和结果")
    assert "真实表格总结：" not in result["answer_text"]
    assert "表格中未提供足够" not in result["answer_text"]


def test_executor_hybrid_file_route_streams_composed_pdf_and_table_answer(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    pdf_answer = "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。"
    table_answer = "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。"
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
            answer_question_fn=lambda **kwargs: pdf_answer,
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: table_answer,
        ),
    )
    streamed_chunks: list[str] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请结合 PDF 和表格总结结论",
            route="hybrid_qa",
            source_scope="pdf+table",
            turn_mode="file_only",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)},
                {"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)},
            ],
            selected_file_ids=[11, 33],
            trace_id="req_stream_hybrid_file",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=None,
        content_callback=streamed_chunks.append,
    )

    assert len(streamed_chunks) >= 3
    assert "".join(streamed_chunks) == result["answer_text"]
    assert "PDF 部分：" not in "".join(streamed_chunks)
    assert "表格部分：" not in "".join(streamed_chunks)
    assert "## 局限性" in result["answer_text"]
    assert "注*" in result["answer_text"]
    assert "==== 文献 " not in result["answer_text"]
    assert not _section_body(result["answer_text"], "结论和意义").startswith("表格结果显示：")
    assert "真实 PDF 总结：" not in result["answer_text"]
    assert "LMFP/LFP 复配改善了充电安全性" in _section_body(result["answer_text"], "结论和意义")
    assert "列:" not in _section_body(result["answer_text"], "主要发现和结果")
    assert "真实表格总结：" not in result["answer_text"]
    assert "表格中未提供足够" not in result["answer_text"]


def test_executor_capability_enabled_pdf_table_hybrid_emits_preview_streams_before_final(tmp_path):
    pdf_path = tmp_path / "spec-preview.pdf"
    csv_path = tmp_path / "claims-preview.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    executor = PatentExecutor(
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性，并提供了实验验证。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh，并且备注字段体现了差异。",
        ),
    )
    streamed_payloads: list[object] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            question="请结合 PDF 和表格总结结论",
            route="hybrid_qa",
            source_scope="pdf+table",
            turn_mode="file_only",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "spec-preview.pdf", "local_path": str(pdf_path)},
                {"file_id": 33, "file_type": "csv", "file_name": "claims-preview.csv", "local_path": str(csv_path)},
            ],
            selected_file_ids=[11, 33],
            trace_id="req_stream_hybrid_preview",
            options={"patent_stream_capability": "preview_v1"},
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_payloads.append,
    )

    typed_events = [payload for payload in streamed_payloads if isinstance(payload, dict)]
    assert typed_events
    assert any(event["content_role"] == "preview" and event["content_source"] == "pdf" for event in typed_events)
    assert any(event["content_role"] == "preview" and event["content_source"] == "table" for event in typed_events)
    assert any(event["content_role"] == "final" and event["content_source"] == "hybrid" for event in typed_events)
    first_final_index = next(index for index, event in enumerate(typed_events) if event["content_role"] == "final")
    assert all(
        index < first_final_index
        for index, event in enumerate(typed_events)
        if event["content_role"] == "preview"
    )
    state = PatentContentStreamState()
    for event in typed_events:
        state.observe(event)
    final_text = "".join(event["content"] for event in typed_events if event["content_role"] == "final")
    assert final_text == result["answer_text"]


def test_executor_capability_enabled_pdf_kb_hybrid_keeps_pdf_preview_before_final(tmp_path):
    pdf_path = tmp_path / "spec-kb-preview.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")

    class _PreviewKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "知识库补充：该路线在专利布局上强调包覆材料与热稳定性。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据强调包覆材料与热稳定性。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_PreviewKbService(),
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：本文提出硅负极包覆方法，并报告循环寿命改善。",
        ),
    )
    streamed_payloads: list[object] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec-kb-preview.pdf", "local_path": str(pdf_path)}],
            selected_file_ids=[11],
            trace_id="req_stream_pdf_kb_preview",
            options={"patent_stream_capability": "preview_v1"},
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_payloads.append,
    )

    typed_events = [payload for payload in streamed_payloads if isinstance(payload, dict)]
    assert typed_events
    assert any(event["content_role"] == "preview" and event["content_source"] == "pdf" for event in typed_events)
    assert any(event["content_role"] == "final" and event["content_source"] == "hybrid" for event in typed_events)
    first_final_index = next(index for index, event in enumerate(typed_events) if event["content_role"] == "final")
    assert all(
        index < first_final_index
        for index, event in enumerate(typed_events)
        if event["content_role"] == "preview"
    )
    state = PatentContentStreamState()
    for event in typed_events:
        state.observe(event)
    final_text = "".join(event["content"] for event in typed_events if event["content_role"] == "final")
    assert final_text == result["answer_text"]


def test_executor_pdf_kb_hybrid_explicitly_reports_conflict_between_file_and_kb_evidence():
    class _ConflictPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            return {
                "answer_text": "文件结论：电芯容量为 120mAh。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "pdf_answer", "title": "生成文件答案", "message": "ok", "status": "success"}],
                "metadata": {
                    "answer_mode": "pdf_text_summary",
                    "pdf_evidence_context": "PDF 原文记录容量为 120mAh，循环稳定。",
                },
                "timings": {"pdf_ms": 3},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
            }

    class _ConflictKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            return {
                "answer_text": "知识库结论：该体系容量为 90mAh。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据显示容量为 90mAh。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_ConflictKbService(),
        pdf_service=_ConflictPdfService(),
    )

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_conflict",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert "存在冲突" in result["answer_text"]
    assert "120mAh" in result["answer_text"]
    assert "90mAh" in result["answer_text"]
    assert "source_scope=" not in result["answer_text"]


def test_executor_pdf_kb_hybrid_does_not_report_conflict_for_same_metric_with_extra_year_number():
    class _AlignedPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            return {
                "answer_text": "文件结论：电芯容量为 120mAh。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "pdf_answer", "title": "生成文件答案", "message": "ok", "status": "success"}],
                "metadata": {
                    "answer_mode": "pdf_text_summary",
                    "pdf_evidence_context": "PDF 原文记录容量为 120mAh，循环稳定。",
                },
                "timings": {"pdf_ms": 3},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
            }

    class _AlignedKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            return {
                "answer_text": "知识库结论：该体系容量为 120mAh，相关记录更新于 2024 年。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据显示容量为 120mAh，相关年份为 2024。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_AlignedKbService(),
        pdf_service=_AlignedPdfService(),
    )

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_no_conflict",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert "冲突说明" not in result["answer_text"]
    assert "120mAh" in result["answer_text"]


def test_executor_pdf_table_kb_hybrid_unifies_real_file_and_kb_evidence(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)

    class _HybridKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "知识库补充：相关专利族强调热稳定性和倍率性能的平衡。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据指出该路线强调热稳定性与倍率性能平衡。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_HybridKbService(),
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
    )

    result = executor.execute(
        request=_make_file_request(
            question="请结合 PDF、表格和知识库总结结论",
            route="hybrid_qa",
            source_scope="pdf+table+kb",
            turn_mode="mixed",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)},
                {"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)},
            ],
            selected_file_ids=[11, 33],
            trace_id="req_pdf_table_kb_hybrid",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert result["metadata"]["answer_mode"] == "hybrid_unified_synthesis"
    assert "LMFP/LFP" in result["answer_text"]
    assert "120mAh" in result["answer_text"]
    assert "知识库补充" in result["answer_text"]
    assert "CN123456789A" in result["answer_text"]
    assert "匹配工作表:" not in result["answer_text"]
    assert "执行操作:" not in result["answer_text"]
    assert "文件:" not in result["answer_text"]
    assert "Patent KB participation:" not in result["answer_text"]
    assert result["metadata"]["synthesis_contract"]["source_scope"] == "pdf+table+kb"


def test_executor_pdf_table_kb_hybrid_answer_keeps_current_mixed_evidence_behavior(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)

    class _HybridKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "知识库补充：相关专利族强调热稳定性和倍率性能的平衡。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据指出该路线强调热稳定性与倍率性能平衡。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_HybridKbService(),
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
    )

    result = executor.execute(
        request=_make_file_request(
            question="请结合 PDF、表格和知识库总结结论",
            route="hybrid_qa",
            source_scope="pdf+table+kb",
            turn_mode="mixed",
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf", "local_path": str(pdf_path)},
                {"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)},
            ],
            selected_file_ids=[11, 33],
            trace_id="req_pdf_table_kb_hybrid_fastqa_structure",
        ),
        context={"recent_turns_for_llm": []},
    )

    answer = result["answer_text"]
    assert "LMFP/LFP" in answer
    assert "120mAh" in answer
    assert "知识库" in answer
    assert "CN123456789A" in answer
    assert "匹配工作表:" not in answer
    assert "执行操作:" not in answer
    assert "文件:" not in answer
    assert "知识库补充：" in answer or "知识库交叉验证：" in answer


def test_executor_pdf_kb_hybrid_preserves_file_route_cache_metadata_alongside_kb_metadata():
    class _ExplodingPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            raise AssertionError("pdf service should not run on file-route cache hit")

    class _FileRouteCacheHit:
        def get_file_route_cache(self, *, fingerprint: str):
            return {
                "handler": "pdf",
                "answer_text": "文件结论：电芯容量为 120mAh。",
                "route": "hybrid_qa",
                "query_mode": "patent_hybrid_qa",
                "source_scope": "pdf+kb",
                "steps": [{"step": "pdf_answer", "title": "生成文件答案", "message": "ok", "status": "success"}],
                "metadata": {
                    "answer_mode": "pdf_text_summary",
                    "pdf_evidence_context": "PDF 原文记录容量为 120mAh，循环稳定。",
                },
                "timings": {"pdf_ms": 3},
                "used_files": [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
                "selected_file_ids": [11],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf+kb"},
                "kb_enabled": True,
            }

    class _CachedKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "知识库结论：该体系容量为 120mAh。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "cache_hit": False,
                    "cache_namespace": "qa-core",
                    "cache_stage": "stage4",
                    "cache_fingerprint": "kb-stage4-fp-1",
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据显示容量为 120mAh。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_CachedKbService(),
        pdf_service=_ExplodingPdfService(),
        execution_cache=_FileRouteCacheHit(),
    )

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_cache_metadata",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert result["cache_hit"] is True
    assert result["metadata"]["cache_namespace"] == "qa-core"
    assert result["metadata"]["cache_stage"] == "stage4"
    assert result["metadata"]["file_route_cache_hit"] is True
    assert result["metadata"]["file_route_cache_namespace"] == "file-route"
    assert result["metadata"]["file_route_cache_fingerprint"]
    assert result["metadata"]["file_route_cache_fingerprint"] != result["metadata"]["cache_fingerprint"]


def test_executor_capability_enabled_pdf_kb_cache_hit_does_not_replay_preview_events():
    class _ExplodingPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            raise AssertionError("pdf service should not run on file-route cache hit")

    class _FileRouteCacheHit:
        def get_file_route_cache(self, *, fingerprint: str):
            return {
                "handler": "pdf",
                "answer_text": "文件结论：电芯容量为 120mAh。",
                "route": "hybrid_qa",
                "query_mode": "patent_hybrid_qa",
                "source_scope": "pdf+kb",
                "steps": [{"step": "pdf_answer", "title": "生成文件答案", "message": "ok", "status": "success"}],
                "metadata": {
                    "answer_mode": "pdf_text_summary",
                    "pdf_evidence_context": "PDF 原文记录容量为 120mAh，循环稳定。",
                },
                "timings": {"pdf_ms": 3},
                "used_files": [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
                "selected_file_ids": [11],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf+kb"},
                "kb_enabled": True,
            }

    class _CachedKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "知识库结论：该体系容量为 120mAh。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据显示容量为 120mAh。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    executor = PatentExecutor(
        kb_service=_CachedKbService(),
        pdf_service=_ExplodingPdfService(),
        execution_cache=_FileRouteCacheHit(),
    )
    streamed_payloads: list[object] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_cache_preview_guard",
            options={"patent_stream_capability": "preview_v1"},
        ),
        context={"recent_turns_for_llm": []},
        content_callback=streamed_payloads.append,
    )

    typed_events = [payload for payload in streamed_payloads if isinstance(payload, dict)]
    assert typed_events
    assert not any(event["content_role"] == "preview" for event in typed_events)
    assert all(event["content_role"] == "final" for event in typed_events)
    assert all(event["content_source"] == "hybrid" for event in typed_events)
    final_text = "".join(event["content"] for event in typed_events if event["content_role"] == "final")
    assert final_text == result["answer_text"]


def test_executor_pdf_kb_hybrid_no_evidence_keeps_live_and_final_hybrid_step_in_error_state():
    class _EmptyPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            return {
                "answer_text": "当前未拿到可读的 PDF 原文内容，暂时无法生成基于文件的回答。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "pdf_answer", "title": "生成文件答案", "message": "unavailable", "status": "error"}],
                "metadata": {
                    "answer_mode": "pdf_text_unavailable",
                    "pdf_evidence_context": "",
                },
                "timings": {"pdf_ms": 1},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
            }

    class _EmptyKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "no evidence", "status": "success"}],
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "",
                    "kb_reference_instruction": "",
                },
                "timings": {"kb_ms": 1},
            }

    executor = PatentExecutor(
        kb_service=_EmptyKbService(),
        pdf_service=_EmptyPdfService(),
    )
    progress_steps: list[dict[str, object]] = []

    result = executor.execute_with_progress(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_no_evidence",
        ),
        context={"recent_turns_for_llm": []},
        progress_callback=progress_steps.append,
        content_callback=None,
    )

    hybrid_progress = [step for step in progress_steps if step.get("step") == "hybrid_answer"]
    assert hybrid_progress[-1]["status"] == "error"
    assert result["steps"][-1]["step"] == "hybrid_answer"
    assert result["steps"][-1]["status"] == "error"


def test_executor_pdf_kb_hybrid_treats_kb_retrieval_miss_as_no_evidence(tmp_path):
    class _EmptyPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            return {
                "answer_text": "当前未拿到可读的 PDF 原文内容，暂时无法生成基于文件的回答。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "pdf_answer", "title": "生成文件答案", "message": "unavailable", "status": "error"}],
                "metadata": {
                    "answer_mode": "pdf_text_unavailable",
                    "pdf_evidence_context": "",
                },
                "timings": {"pdf_ms": 1},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
            }

    class _NotFoundKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "Patent retrieval found no matching results.",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "retrieval_not_found", "title": "Patent Retrieval", "message": "miss", "status": "success"}],
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "not_found": True,
                    "kb_evidence_context": "",
                    "kb_reference_instruction": "",
                },
                "timings": {"kb_ms": 1},
            }

    executor = PatentExecutor(
        kb_service=_NotFoundKbService(),
        pdf_service=_EmptyPdfService(),
    )

    result = executor.execute(
        request=_make_file_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            selected_file_ids=[11],
            trace_id="req_pdf_kb_not_found",
        ),
        context={"recent_turns_for_llm": []},
    )

    assert "Patent retrieval found no matching results." not in result["answer_text"]
    assert "暂时无法生成联合回答" in result["answer_text"]
    assert result["steps"][-1]["step"] == "hybrid_answer"
    assert result["steps"][-1]["status"] == "error"
