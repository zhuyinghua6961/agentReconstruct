import pytest

from server.patent.executor import PatentExecutor
from server.patent.kb_service import PatentKbService
from server.patent.pdf_service import PatentPdfService
from server.patent.tabular_service import PatentTabularService
from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.retrieval_models import PatentCatalogRecord, PatentClaim, PatentDescriptionSnippet
from server.patent.retrieval_service import PatentRetrievalService
from server.schemas.request_models import PatentAskRequest



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
        options={},
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


def test_executor_pdf_streaming_generator_success_emits_final_step_before_first_content(tmp_path):
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
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    final_success_index = max(
        index for index, item in enumerate(events) if item[0] == "step" and item[1] == "pdf_answer" and item[2] == "success"
    )
    assert final_success_index < first_content_index


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
            answer_question_fn=lambda **kwargs: "对比结果：文献 1 与文献 2 存在明显差异。",
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
            answer_question_fn=lambda **kwargs: "对比结果：文献 1 与文献 2 存在明显差异。",
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
    first_content_index = next(index for index, item in enumerate(events) if item[0] == "content")
    final_success_index = max(
        index
        for index, item in enumerate(events)
        if item[0] == "step" and item[1] == "pdf_answer" and item[2] == "success"
    )
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

    assert result["metadata"]["answer_mode"] == "table_text_summary"
    assert "真实表格总结" in result["answer_text"]
    assert "Patent tabular route answered" not in result["answer_text"]


@pytest.mark.parametrize(
    ("source_scope", "execution_files", "selected_file_ids", "expected_handler", "expected_used_files"),
    [
        ("pdf+kb", [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}], [11], "pdf", [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}]),
        ("table+kb", [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}], [33], "tabular", [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}]),
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
        (
            "pdf+table+kb",
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

    assert result["answer_text"] == "kb contribution"
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
    streamed_chunks: list[str] = []

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
        progress_callback=None,
        content_callback=streamed_chunks.append,
    )

    assert len(streamed_chunks) >= 3
    assert "".join(streamed_chunks) == result["answer_text"]
    assert "Patent KB participation:" in "".join(streamed_chunks)


def test_executor_dispatches_file_only_hybrid_route_to_patent_file_scaffold():
    class _FailingKbService(PatentKbService):
        def run(self, *, request, runtime=None, conversation_context=None):
            raise AssertionError("kb path should not be used for file-only hybrid_qa")

    executor = PatentExecutor(
        kb_service=_FailingKbService(),
    )

    result = executor.execute(
        request=_make_file_request(
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

    assert result["metadata"]["answer_mode"] == "hybrid_file_synthesis"
    assert "真实 PDF 总结" in result["answer_text"]
    assert "真实表格总结" in result["answer_text"]
    assert "Patent hybrid route combined selected PDF and table files" not in result["answer_text"]


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
    assert "PDF 部分：" in "".join(streamed_chunks)
    assert "表格部分：" in "".join(streamed_chunks)
