from types import SimpleNamespace

from app.modules.qa_tabular.renderer import _build_tabular_prompt
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
            load_pdf_content_fn=lambda **kwargs: ("===== 文献 #1: 10.1_demo.pdf =====\n文献证据 chunk", None),
            max_pdf_chars=12000,
        )
    )

    assert events[0]["type"] == "metadata"
    hybrid_events = [event for event in events if isinstance(event, dict) and event.get("step") == "hybrid_evidence"]
    assert hybrid_events
    assert "PDF 问答方式" in str(hybrid_events[-1].get("message") or "")
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == "hybrid_qa"


def test_hybrid_service_can_use_pdf_preview_when_file_not_ready(monkeypatch):
    _stub_successful_tabular_flow(monkeypatch)
    monkeypatch.setenv("FASTQA_UPLOAD_MINIO_ONLY", "false")

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
            load_pdf_content_fn=lambda **kwargs: (None, "unavailable"),
            max_pdf_chars=12000,
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


def test_hybrid_service_rejects_pdf_preview_when_strict_minio_only(monkeypatch):
    _stub_successful_tabular_flow(monkeypatch)
    monkeypatch.setenv("FASTQA_UPLOAD_MINIO_ONLY", "true")

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
                    "storage_ref": "minio://agentcode/uploads/demo.xlsx",
                },
                {
                    "file_id": 2,
                    "file_type": "pdf",
                    "file_name": "10.1_demo.pdf",
                    "parse_status": "uploaded",
                    "index_status": "pending",
                    "processing_stage": "uploaded",
                    "file_meta": {"parsed_preview": "文献提到电压窗口为 3.0-4.2 V，并讨论倍率性能。"},
                    "storage_ref": "minio://agentcode/uploads/demo.pdf",
                    "storage_error": "object_unavailable",
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

    assert events == [{"type": "error", "error": "PDF 文件仍在处理中或源文件不可用，请稍后重试：10.1_demo.pdf"}]


def test_tabular_service_logs_summary_diagnostics(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        qa_tabular_service,
        "load_workbook",
        lambda _file_item: {
            "file_name": "demo.xlsx",
            "sheets": [{"sheet_name": "Sheet1", "sheet_index": 0, "dataframe": object()}],
        },
    )
    monkeypatch.setattr(
        qa_tabular_service,
        "plan",
        lambda **_kwargs: {"operation": "summary", "sheet_name": "Sheet1", "filters": [], "focus_columns": ["供应商", "实际容量_Ah"]},
    )
    monkeypatch.setattr(
        qa_tabular_service,
        "execute",
        lambda **_kwargs: {
            "operation": "summary",
            "sheet_name": "Sheet1",
            "row_count_before": 10,
            "row_count_after": 10,
            "summary_stats": {
                "row_count": 10,
                "column_count": 4,
                "columns": ["供应商", "实际容量_Ah", "异常标记", "生产备注"],
                "focus_columns": ["供应商", "实际容量_Ah"],
            },
            "result_rows": [
                {"供应商": "宁德时代", "实际容量_Ah": 147.56},
                {"供应商": "亿纬锂能", "实际容量_Ah": 69.77},
            ],
        },
    )
    monkeypatch.setattr(qa_tabular_service, "iter_synthesize_answer", lambda **_kwargs: iter(["结论"]))

    events = list(
        qa_tabular_service.iter_answer_events(
            question="总结这个表格里各供应商的容量差异",
            used_files=[
                {
                    "file_id": 1,
                    "file_type": "excel",
                    "file_name": "demo.xlsx",
                    "local_path": "/tmp/demo.xlsx",
                    "parse_status": "ready",
                    "index_status": "ready",
                    "processing_stage": "ready",
                }
            ],
            route_hint="tabular_qa",
            agent=SimpleNamespace(llm=object()),
            sse_event=lambda event: event,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            log_qa_interaction=lambda **kwargs: captured.update(kwargs),
            extract_pdf_text_fn=lambda _path: "",
        )
    )

    assert events[-1]["type"] == "done"
    assert captured["extra"]["summary_column_count"] == 4
    assert captured["extra"]["summary_focus_columns"] == ["供应商", "实际容量_Ah"]
    assert captured["extra"]["summary_sample_count"] == 2


def test_hybrid_compare_question_uses_summary_plan_and_cross_modal_prompt(monkeypatch):
    captured_plan = {}

    def _capture_plan(**kwargs):
        captured_plan.update(kwargs)
        return {"operation": "summary", "sheet_name": "Sheet1", "filters": []}

    _stub_successful_tabular_flow(monkeypatch)
    monkeypatch.setattr(qa_tabular_service, "plan", _capture_plan)

    events = list(
        qa_tabular_service.iter_answer_events(
            question="对比一下这些文献和表格",
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
                },
            ],
            route_hint="hybrid_qa",
            file_selection={"strategy": "explicit_selection", "selected_file_ids": [1, 2]},
            agent=SimpleNamespace(llm=object()),
            sse_event=lambda event: event,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            log_qa_interaction=lambda **_kwargs: None,
            load_pdf_content_fn=lambda **kwargs: ("===== 文献 #1: 10.1_demo.pdf =====\n电压窗口 3.0-4.2V", None),
            max_pdf_chars=12000,
        )
    )

    assert captured_plan.get("route_hint") == "hybrid_qa"
    assert captured_plan.get("table_file_count") == 1
    plan_events = [event for event in events if isinstance(event, dict) and event.get("step") == "tabular_plan"]
    assert plan_events
    assert "summary" in str(plan_events[-1].get("message") or "")

    prompt, _context = _build_tabular_prompt(
        question="对比一下这些文献和表格",
        file_name="demo.xlsx",
        plan={"operation": "summary"},
        result={"operation": "summary", "result_rows": [], "summary_stats": {"row_count": 1}},
        route_hint="hybrid_qa",
        pdf_evidence_context="===== 文献 #1: 10.1_demo.pdf =====\n电压窗口 3.0-4.2V",
    )
    assert "文献原文" in prompt
    assert "表格执行结果" in prompt
    assert "不得要求用户再提供文献或表格内容" in prompt
    assert "必须优先依据这些结果作答" not in prompt
