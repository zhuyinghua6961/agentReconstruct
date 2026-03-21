from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
import app.routers.qa as qa_router_module


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
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["route"] == "pdf_qa"
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
            "execution_files": [{"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["route"] == "tabular_qa"
    assert payload["final_answer"] == "table answer"


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
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "metadata"' in response.text
    assert '"type": "done"' in response.text
    assert '"route": "pdf_qa"' in response.text


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
