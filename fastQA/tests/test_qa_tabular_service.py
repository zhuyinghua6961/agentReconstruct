from types import SimpleNamespace

from app.modules.qa_tabular.service import qa_tabular_service


def test_tabular_service_emits_hybrid_evidence_step(monkeypatch):
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
