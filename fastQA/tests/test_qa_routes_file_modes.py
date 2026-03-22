from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
import app.routers.qa as qa_router_module
import app.services.file_routes as file_routes_module


client = TestClient(app)


def test_pdf_route_is_dispatched(monkeypatch):
    def _fake_pdf_iter(**_kwargs):
        yield {"type": "metadata", "query_mode": "PDF文献查询"}
        yield {"type": "content", "content": "pdf answer"}
        yield {"type": "done", "route": "pdf_qa", "references": ["10.1/test"]}

    monkeypatch.setattr(qa_router_module, "iter_pdf_route_events", _fake_pdf_iter)
    response = client.post(
        "/api/ask",
        json={
            "question": "总结上传的pdf",
            "requested_mode": "fast",
            "route": "pdf_qa",
            "source_scope": "pdf",
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["route"] == "pdf_qa"
    assert payload["source_scope"] == "pdf"
    assert payload["source_usage"] == {"pdf_used": True, "table_used": False, "kb_used": False}
    assert payload["metadata"]["source_scope"] == "pdf"
    assert payload["metadata"]["source_usage"] == {"pdf_used": True, "table_used": False, "kb_used": False}
    assert payload["final_answer"] == "pdf answer"


def test_tabular_route_is_dispatched(monkeypatch):
    def _fake_tabular_iter(**_kwargs):
        yield {"type": "metadata", "query_mode": "表格问答"}
        yield {"type": "content", "content": "table answer"}
        yield {"type": "done", "route": "tabular_qa", "references": []}

    monkeypatch.setattr(qa_router_module, "iter_tabular_route_events", _fake_tabular_iter)
    response = client.post(
        "/api/ask",
        json={
            "question": "分析这个excel",
            "requested_mode": "fast",
            "route": "tabular_qa",
            "source_scope": "table",
            "execution_files": [{"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["route"] == "tabular_qa"
    assert payload["source_scope"] == "table"
    assert payload["source_usage"] == {"pdf_used": False, "table_used": True, "kb_used": False}
    assert payload["metadata"]["source_scope"] == "table"
    assert payload["metadata"]["source_usage"] == {"pdf_used": False, "table_used": True, "kb_used": False}
    assert payload["final_answer"] == "table answer"

def test_hybrid_pdf_kb_route_dispatches_to_pdf_handler(monkeypatch):
    called = {"pdf": 0, "tabular": 0}

    def _fake_pdf_iter(*, adapted_request, file_context, **_kwargs):
        called["pdf"] += 1
        assert adapted_request.route == "hybrid_qa"
        assert adapted_request.source_scope == "pdf+kb"
        assert adapted_request.kb_enabled is True
        assert file_context is not None
        assert file_context.get("allow_kb_verification") is True
        yield {"type": "metadata", "query_mode": "混合问答"}
        yield {"type": "content", "content": "answer"}
        yield {"type": "done", "route": "pdf_qa", "references": []}

    def _fake_tabular_iter(**_kwargs):
        called["tabular"] += 1
        yield {"type": "metadata", "query_mode": "表格问答"}
        yield {"type": "done", "route": "hybrid_qa", "references": []}

    monkeypatch.setattr(qa_router_module, "iter_pdf_route_events", _fake_pdf_iter)
    monkeypatch.setattr(qa_router_module, "iter_tabular_route_events", _fake_tabular_iter)

    response = client.post(
        "/api/ask",
        json={
            "question": "结合文献和知识库回答",
            "requested_mode": "fast",
            "route": "hybrid_qa",
            "source_scope": "pdf+kb",
            "kb_enabled": True,
            "allow_kb_verification": True,
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["route"] == "hybrid_qa"
    assert payload["source_scope"] == "pdf+kb"
    assert payload["source_usage"] == {"pdf_used": True, "table_used": False, "kb_used": True}
    assert called["pdf"] == 1
    assert called["tabular"] == 0



def test_explicit_upstream_route_preserves_selection_without_re_resolving(monkeypatch):
    def _unexpected_resolve(**_kwargs):
        raise AssertionError("resolve_request_file_context should not run for explicit upstream file routes")

    def _fake_pdf_iter(*, adapted_request, file_context, **_kwargs):
        assert adapted_request.route == "pdf_qa"
        assert adapted_request.source_scope == "pdf"
        assert adapted_request.selected_file_ids == [1]
        assert adapted_request.primary_file_id == 1
        assert adapted_request.file_selection["selected_file_ids"] == [1]
        assert adapted_request.file_selection["primary_file_id"] == 1
        assert file_context is not None
        assert file_context["selected_file_ids"] == [1]
        assert file_context["route_hint"] == "pdf_qa"
        yield {"type": "metadata", "query_mode": "PDF文献查询"}
        yield {"type": "content", "content": "pdf answer"}
        yield {"type": "done", "route": "pdf_qa", "references": []}

    monkeypatch.setattr(file_routes_module, "resolve_request_file_context", _unexpected_resolve)
    monkeypatch.setattr(qa_router_module, "iter_pdf_route_events", _fake_pdf_iter)
    response = client.post(
        "/api/ask",
        json={
            "question": "总结这个pdf",
            "requested_mode": "fast",
            "route": "pdf_qa",
            "source_scope": "pdf",
            "kb_enabled": False,
            "selected_file_ids": [1],
            "primary_file_id": 1,
            "file_selection": {
                "strategy": "gateway",
                "selection_semantic": "upstream_selected",
            },
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["route"] == "pdf_qa"
    assert payload["source_scope"] == "pdf"
    assert payload["source_usage"] == {"pdf_used": True, "table_used": False, "kb_used": False}
    assert payload["used_files"][0]["file_id"] == 1
    assert payload["file_selection"] == {
        "strategy": "gateway",
        "selection_semantic": "upstream_selected",
        "source_scope": "pdf",
        "kb_enabled": False,
        "selected_file_ids": [1],
        "primary_file_id": 1,
    }


def test_legacy_v1_ask_uses_stream_contract_for_file_route(monkeypatch):
    def _fake_pdf_iter(**_kwargs):
        yield {"type": "metadata", "query_mode": "PDF文献查询", "route": "pdf_qa"}
        yield {"type": "content", "content": "pdf answer"}
        yield {"type": "done", "route": "pdf_qa", "references": ["10.1/test"]}

    monkeypatch.setattr(qa_router_module, "iter_pdf_route_events", _fake_pdf_iter)
    response = client.post(
        "/api/v1/ask",
        json={
            "question": "总结上传的pdf",
            "requested_mode": "fast",
            "route": "pdf_qa",
            "source_scope": "pdf",
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "metadata"' in response.text
    assert '"type": "done"' in response.text
    assert '"route": "pdf_qa"' in response.text
    assert '"source_scope": "pdf"' in response.text


def test_legacy_v1_ask_returns_json_error_for_invalid_stream_request():
    response = client.post(
        "/api/v1/ask",
        json={
            "question": "hello",
            "requested_mode": "thinking",
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "MODE_NOT_SUPPORTED"


def test_hybrid_route_dispatch_matrix(monkeypatch):
    calls = {"pdf": 0, "tabular": 0}

    def _fake_pdf_iter(**_kwargs):
        calls["pdf"] += 1
        yield {"type": "metadata", "query_mode": "PDF文献查询"}
        yield {"type": "content", "content": "pdf"}
        yield {"type": "done", "route": "pdf_qa", "references": []}

    def _fake_tabular_iter(**_kwargs):
        calls["tabular"] += 1
        yield {"type": "metadata", "query_mode": "表格问答"}
        yield {"type": "content", "content": "table"}
        yield {"type": "done", "route": "tabular_qa", "references": []}

    monkeypatch.setattr(qa_router_module, "iter_pdf_route_events", _fake_pdf_iter)
    monkeypatch.setattr(qa_router_module, "iter_tabular_route_events", _fake_tabular_iter)

    cases = [
        # pdf-only hybrid (pdf+kb) must dispatch to PDF handler.
        {
            "source_scope": "pdf+kb",
            "kb_enabled": True,
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
            "expect": "pdf",
        },
        # table+kb goes through tabular handler.
        {
            "source_scope": "table+kb",
            "kb_enabled": True,
            "execution_files": [{"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"}],
            "expect": "tabular",
        },
        # pdf+table hybrid goes through tabular handler.
        {
            "source_scope": "pdf+table",
            "kb_enabled": False,
            "execution_files": [
                {"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"},
                {"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"},
            ],
            "expect": "tabular",
        },
        # pdf+table+kb hybrid goes through tabular handler.
        {
            "source_scope": "pdf+table+kb",
            "kb_enabled": True,
            "execution_files": [
                {"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"},
                {"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"},
            ],
            "expect": "tabular",
        },
    ]

    for case in cases:
        calls["pdf"] = 0
        calls["tabular"] = 0
        response = client.post(
            "/api/ask",
            json={
                "question": "test",
                "requested_mode": "fast",
                "route": "hybrid_qa",
                "source_scope": case["source_scope"],
                "kb_enabled": case["kb_enabled"],
                "execution_files": case["execution_files"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["route"] == "hybrid_qa"
        assert payload["source_scope"] == case["source_scope"]

        tokens = set(case["source_scope"].split("+"))
        assert payload["source_usage"] == {
            "pdf_used": "pdf" in tokens,
            "table_used": "table" in tokens,
            "kb_used": "kb" in tokens,
        }

        if case["expect"] == "pdf":
            assert calls["pdf"] == 1
            assert calls["tabular"] == 0
        else:
            assert calls["pdf"] == 0
            assert calls["tabular"] == 1
