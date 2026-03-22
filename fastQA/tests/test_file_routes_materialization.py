from __future__ import annotations

from types import SimpleNamespace

import app.services.file_routes as file_routes_module


def test_iter_pdf_route_events_materializes_storage_ref_file(tmp_path, monkeypatch):
    source_file = tmp_path / "demo.pdf"
    source_file.write_bytes(b"%PDF-1.4\n%uploaded\n")

    monkeypatch.setattr(
        file_routes_module,
        "get_pdf_bindings",
        lambda _app_state, _logger: SimpleNamespace(
            extract_pdf_text=lambda _path: "pdf body",
            answer_from_pdf=object(),
        ),
    )
    monkeypatch.setattr(
        file_routes_module,
        "_pdf_agent_for_request",
        lambda **_kwargs: SimpleNamespace(llm=object()),
    )

    captured: dict = {}

    def _fake_load_pdf_content_for_streaming(**kwargs):
        captured["load_pdf_path"] = kwargs["pdf_path"]
        return ("pdf body", None)

    def _fake_iter_route_answer_events(**kwargs):
        captured["pdf_path"] = kwargs["pdf_path"]
        captured["selected_pdf_files"] = kwargs["selected_pdf_files"]
        yield {"type": "done", "route": "pdf_qa", "references": []}

    monkeypatch.setattr(file_routes_module, "load_pdf_content_for_streaming", _fake_load_pdf_content_for_streaming)
    monkeypatch.setattr(file_routes_module.pdf_qa_service, "iter_route_answer_events", _fake_iter_route_answer_events)

    adapted_request = SimpleNamespace(
        question="总结上传文件",
        execution_files=[
            {
                "file_id": 1,
                "file_type": "pdf",
                "file_name": "demo.pdf",
                "storage_ref": f"local://{source_file}",
            }
        ],
        used_files=[],
        current_pdf_path="",
        pdf_path="",
        allow_kb_verification=False,
        turn_mode="file_only",
    )

    events = list(
        file_routes_module.iter_pdf_route_events(
            app_state=SimpleNamespace(logger=None, redis_service=None),
            adapted_request=adapted_request,
            file_context={"execution_files": adapted_request.execution_files},
            sse_event=lambda event: event,
            is_cancelled=None,
        )
    )

    assert captured["load_pdf_path"] == str(source_file.resolve())
    assert captured["pdf_path"] == str(source_file.resolve())
    assert captured["selected_pdf_files"][0]["local_path"] == str(source_file.resolve())
    assert events[-1]["type"] == "done"


def test_iter_pdf_route_events_returns_soft_error_when_uploaded_file_unavailable(monkeypatch):
    monkeypatch.setattr(
        file_routes_module,
        "get_pdf_bindings",
        lambda _app_state, _logger: SimpleNamespace(
            extract_pdf_text=lambda _path: "pdf body",
            answer_from_pdf=object(),
        ),
    )

    def _unexpected_iter_route_answer_events(**_kwargs):
        raise AssertionError("pdf route should not execute when uploaded file is unavailable")

    monkeypatch.setattr(file_routes_module.pdf_qa_service, "iter_route_answer_events", _unexpected_iter_route_answer_events)

    adapted_request = SimpleNamespace(
        question="总结上传文件",
        execution_files=[
            {
                "file_id": 9,
                "file_type": "pdf",
                "file_name": "missing.pdf",
                "storage_ref": "local:///tmp/definitely-missing-fastqa-upload.pdf",
            }
        ],
        used_files=[],
        current_pdf_path="",
        pdf_path="",
        allow_kb_verification=False,
        turn_mode="file_only",
    )

    events = list(
        file_routes_module.iter_pdf_route_events(
            app_state=SimpleNamespace(logger=None, redis_service=None),
            adapted_request=adapted_request,
            file_context={"execution_files": adapted_request.execution_files},
            sse_event=lambda event: event,
            is_cancelled=None,
        )
    )

    assert events == [
        {
            "type": "error",
            "error": "execution_file_unavailable",
            "message": "uploaded file is not ready for direct reading yet; retry later or refresh file metadata",
        }
    ]


def test_iter_tabular_route_events_prepares_materialized_table_file(tmp_path, monkeypatch):
    source_file = tmp_path / "demo.csv"
    source_file.write_text("city,value\nshanghai,1\n", encoding="utf-8")

    monkeypatch.setattr(
        file_routes_module,
        "get_pdf_bindings",
        lambda _app_state, _logger: SimpleNamespace(extract_pdf_text=lambda _path: ""),
    )
    monkeypatch.setattr(
        file_routes_module,
        "_pdf_agent_for_request",
        lambda **_kwargs: SimpleNamespace(llm=object()),
    )

    captured: dict = {}

    def _fake_iter_answer_events(**kwargs):
        captured.update(kwargs)
        yield {"type": "done", "route": kwargs.get("route_hint") or "tabular_qa", "references": []}

    monkeypatch.setattr(file_routes_module.qa_tabular_service, "iter_answer_events", _fake_iter_answer_events)

    adapted_request = SimpleNamespace(
        question="分析这个表格",
        execution_files=[
            {
                "file_id": 2,
                "file_type": "csv",
                "file_name": "demo.csv",
                "storage_ref": f"local://{source_file}",
                "parse_status": "pending",
                "index_status": "pending",
                "processing_stage": "uploading",
            }
        ],
        used_files=[],
        source_scope="table",
        kb_enabled=False,
        allow_kb_verification=False,
        trace_id="trace-tabular-materialized",
    )

    events = list(
        file_routes_module.iter_tabular_route_events(
            app_state=SimpleNamespace(logger=None, generation_runtime=None),
            adapted_request=adapted_request,
            file_context={"execution_files": adapted_request.execution_files},
            route="tabular_qa",
            sse_event=lambda event: event,
            is_cancelled=None,
        )
    )

    prepared_file = captured["used_files"][0]
    assert prepared_file["local_path"] == str(source_file.resolve())
    assert prepared_file["parse_status"] == ""
    assert prepared_file["index_status"] == ""
    assert prepared_file["processing_stage"] == ""
    assert events[-1]["type"] == "done"
