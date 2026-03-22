from types import SimpleNamespace

from app.modules.qa_tabular.service import qa_tabular_service


def _stub_successful_tabular_flow(monkeypatch):
    monkeypatch.setattr(
        qa_tabular_service,
        "load_workbook",
        lambda _file_item: {
            "file_name": "demo.xlsx",
            "sheets": [{"sheet_name": "Sheet1", "sheet_index": 0, "dataframe": object()}],
        },
    )
    monkeypatch.setattr(qa_tabular_service, "plan", lambda **_kwargs: {"operation": "summary", "filters": []})
    monkeypatch.setattr(
        qa_tabular_service,
        "execute",
        lambda **_kwargs: {
            "operation": "summary",
            "sheet_name": "Sheet1",
            "row_count_before": 10,
            "row_count_after": 10,
            "summary_stats": {"row_count": 10, "column_count": 3},
            "result_rows": [],
        },
    )
    monkeypatch.setattr(qa_tabular_service, "iter_synthesize_answer", lambda **_kwargs: iter(["结论"]))


def test_tabular_service_uses_pending_table_file_when_source_exists(monkeypatch):
    _stub_successful_tabular_flow(monkeypatch)

    events = list(
        qa_tabular_service.iter_answer_events(
            question="分析刚上传的表格",
            used_files=[
                {
                    "file_id": 1,
                    "file_type": "excel",
                    "file_name": "demo.xlsx",
                    "local_path": "/tmp/demo.xlsx",
                    "parse_status": "uploaded",
                    "index_status": "pending",
                    "processing_stage": "uploaded",
                }
            ],
            route_hint="tabular_qa",
            agent=SimpleNamespace(llm=object()),
            sse_event=lambda event: event,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            log_qa_interaction=lambda **_kwargs: None,
            extract_pdf_text_fn=lambda _path: "",
        )
    )

    assert any(event.get("step") == "file_readiness" for event in events if isinstance(event, dict))
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == "tabular_qa"


def test_tabular_service_returns_soft_error_when_pending_table_has_no_source(monkeypatch):
    events = list(
        qa_tabular_service.iter_answer_events(
            question="分析刚上传的表格",
            used_files=[
                {
                    "file_id": 1,
                    "file_type": "excel",
                    "file_name": "demo.xlsx",
                    "parse_status": "uploaded",
                    "index_status": "pending",
                    "processing_stage": "uploaded",
                }
            ],
            route_hint="tabular_qa",
            agent=SimpleNamespace(llm=object()),
            sse_event=lambda event: event,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            log_qa_interaction=lambda **_kwargs: None,
            extract_pdf_text_fn=lambda _path: "",
        )
    )

    assert events == [{"type": "error", "error": "表格文件仍在处理中或源文件不可用，请稍后重试：demo.xlsx"}]


def test_tabular_service_emits_hybrid_evidence_step(monkeypatch):
    _stub_successful_tabular_flow(monkeypatch)

    events = list(
        qa_tabular_service.iter_answer_events(
            question="结合表格和文献给结论",
            used_files=[
                {
                    "file_id": 1,
                    "file_type": "excel",
                    "file_name": "demo.xlsx",
                    "local_path": "/tmp/demo.xlsx",
                    "parse_status": "ready",
                    "index_status": "ready",
                    "processing_stage": "ready",
                },
                {
                    "file_id": 2,
                    "file_type": "pdf",
                    "file_name": "10.1_demo.pdf",
                    "local_path": "/tmp/demo.pdf",
                    "parse_status": "ready",
                    "index_status": "ready",
                    "processing_stage": "ready",
                    "file_meta": {"parsed_preview": "preview"},
                },
            ],
            route_hint="hybrid_qa",
            agent=SimpleNamespace(llm=object()),
            sse_event=lambda event: event,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            log_qa_interaction=lambda **_kwargs: None,
            extract_pdf_text_fn=lambda _path: "文献证据 chunk",
        )
    )

    assert events[0]["type"] == "metadata"
    assert any(event.get("step") == "hybrid_evidence" for event in events if isinstance(event, dict))
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == "hybrid_qa"


def test_hybrid_service_can_use_pdf_preview_when_file_not_ready(monkeypatch):
    _stub_successful_tabular_flow(monkeypatch)

    events = list(
        qa_tabular_service.iter_answer_events(
            question="结合文献里的电压窗口和表格给结论",
            used_files=[
                {
                    "file_id": 1,
                    "file_type": "excel",
                    "file_name": "demo.xlsx",
                    "local_path": "/tmp/demo.xlsx",
                    "parse_status": "ready",
                    "index_status": "ready",
                    "processing_stage": "ready",
                },
                {
                    "file_id": 2,
                    "file_type": "pdf",
                    "file_name": "10.1_demo.pdf",
                    "parse_status": "uploaded",
                    "index_status": "pending",
                    "processing_stage": "uploaded",
                    "file_meta": {"parsed_preview": "文献提到电压窗口为 3.0-4.2 V，并讨论倍率性能。"},
                },
            ],
            route_hint="hybrid_qa",
            agent=SimpleNamespace(llm=object()),
            sse_event=lambda event: event,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            log_qa_interaction=lambda **_kwargs: None,
            extract_pdf_text_fn=lambda _path: "",
        )
    )

    readiness_events = [event for event in events if isinstance(event, dict) and event.get("step") == "file_readiness"]
    assert readiness_events
    assert any("PDF 文件仍在处理中" in str(event.get("message") or "") for event in readiness_events)
    hybrid_events = [event for event in events if isinstance(event, dict) and event.get("step") == "hybrid_evidence"]
    assert hybrid_events
    assert hybrid_events[-1]["status"] == "success"
    assert events[-1]["type"] == "done"
    assert "10.1/demo" in events[-1]["references"]
