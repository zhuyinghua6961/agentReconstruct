from __future__ import annotations

from types import SimpleNamespace

import app.services.file_routes as file_routes_module


def test_iter_tabular_route_events_injects_kb_context(tmp_path, monkeypatch):
    # Avoid importing heavy PDF deps in bindings builder.
    monkeypatch.setattr(
        file_routes_module,
        "get_pdf_bindings",
        lambda _app_state, _logger: SimpleNamespace(extract_pdf_text=lambda _path: ""),
    )
    # Avoid building real PDF agent.
    monkeypatch.setattr(
        file_routes_module,
        "_pdf_agent_for_request",
        lambda **_kwargs: SimpleNamespace(llm=object()),
    )

    captured: dict = {}

    def _fake_iter_answer_events(**kwargs):
        captured.update(kwargs)
        yield {"type": "done", "route": kwargs.get("route_hint") or "hybrid_qa", "references": []}

    monkeypatch.setattr(file_routes_module.qa_tabular_service, "iter_answer_events", _fake_iter_answer_events)
    monkeypatch.setattr(
        file_routes_module,
        "materialize_uploaded_files",
        lambda **_kwargs: [
            {
                "file_id": 1,
                "file_type": "excel",
                "file_name": "demo.xlsx",
                "storage_ref": "minio://agentcode/uploads/demo.xlsx",
                "local_path": str(source_file.resolve()),
                "parse_status": "",
                "index_status": "",
                "processing_stage": "",
            }
        ],
    )

    runtime = SimpleNamespace(
        stage1_pre_answer_and_planning=lambda _q: {"retrieval_claims": [{"claim": "c"}]},
        stage2_targeted_retrieval=lambda **_kwargs: {
            "success": True,
            "documents": ["doc1"],
            "metadatas": [{"doi": "10.1/test", "title": "t"}],
            "distances": [0.1],
            "claim_to_results": {},
            "unique_count": 1,
            "total_count": 1,
        },
    )
    app_state = SimpleNamespace(generation_runtime=runtime, logger=None)

    source_file = tmp_path / "demo.xlsx"
    source_file.write_bytes(b"placeholder")

    adapted_request = SimpleNamespace(
        question="q",
        execution_files=[
            {
                "file_id": 1,
                "file_type": "excel",
                "file_name": "demo.xlsx",
                "storage_ref": "minio://agentcode/uploads/demo.xlsx",
                "local_path": "",
                "parse_status": "uploaded",
                "index_status": "pending",
                "processing_stage": "uploading",
            }
        ],
        used_files=[],
        kb_enabled=True,
        source_scope="table+kb",
        allow_kb_verification=False,
        trace_id="t",
        n_results_per_claim=10,
        active_stream_count=None,
    )

    events = list(
        file_routes_module.iter_tabular_route_events(
            app_state=app_state,
            adapted_request=adapted_request,
            file_context={"execution_files": adapted_request.execution_files},
            route="hybrid_qa",
            sse_event=lambda event: event,
            is_cancelled=None,
        )
    )

    assert any(
        isinstance(event, dict)
        and event.get("type") == "step"
        and event.get("step") == "kb_retrieval"
        and event.get("status") == "success"
        for event in events
    )

    assert captured.get("kb_enabled") is True
    assert captured.get("source_scope") == "table+kb"
    assert "kb_evidence_context" in captured
    assert captured.get("kb_references") == ["10.1/test"]
