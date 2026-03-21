import json

from fastapi.testclient import TestClient

from server.services.ask_service import AskServiceError, AskTimeoutError
from server_fastapi.app import create_app
from server_fastapi.auth.deps import AuthContext, require_auth_context


def _parse_sse_frames(raw_text: str) -> list[dict]:
    frames = []
    for chunk in raw_text.split("\n\n"):
        if not chunk.startswith("data: "):
            continue
        payload = json.loads(chunk[6:])
        frames.append(payload)
    return frames


def test_fastapi_mode_error_contracts():
    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)

    unknown = client.post("/api/unknown/ask_stream", json={"question": "x"})
    mismatch = client.post("/api/thinking/ask_stream", json={"question": "x", "mode": "fast"})

    unknown_payload = unknown.json()
    mismatch_payload = mismatch.json()

    assert unknown.status_code == 400
    assert unknown_payload["code"] == "MODE_NOT_SUPPORTED"
    assert unknown_payload["error"] == "mode_not_supported"

    assert mismatch.status_code == 400
    assert mismatch_payload["code"] == "MODE_MISMATCH"
    assert mismatch_payload["error"] == "invalid_request"


def test_fastapi_ask_success_contract(monkeypatch):
    def fake_execute_ask(**kwargs):
        request = kwargs["request"]
        assert request.mode == "thinking"
        assert request.requested_mode == "fast"
        assert request.actual_mode == "thinking"
        assert request.route == "kb_qa"
        return {
            "final_answer": "alpha",
            "timings": {"total": 0.1},
            "metadata": {
                "mode": "thinking",
                "requested_mode": "fast",
                "actual_mode": "thinking",
                "route": "kb_qa",
                "turn_mode": "kb_only",
                "query_mode": "thinking",
                "conversation_id": None,
                "summary_available": True,
                "summary_updated_at": "2026-03-17T10:00:00+08:00",
            },
            "references": ["10.1000/demo"],
            "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "trace_id": kwargs["trace_id"],
            "used_files": [],
        }

    monkeypatch.setattr("server_fastapi.routers.ask.execute_ask", fake_execute_ask)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)
    response = client.post(
        "/api/v1/thinking/ask",
        json={"question": "demo", "requested_mode": "fast", "actual_mode": "thinking", "route": "kb_qa"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["final_answer"] == "alpha"
    assert payload["data"]["metadata"]["query_mode"] == "thinking"
    assert payload["data"]["metadata"]["requested_mode"] == "fast"
    assert payload["data"]["metadata"]["actual_mode"] == "thinking"
    assert payload["data"]["metadata"]["route"] == "kb_qa"
    assert payload["data"]["metadata"]["summary_available"] is True
    assert payload["trace_id"].startswith("req_")


def test_fastapi_ask_stream_success_contract(monkeypatch):
    def fake_stream_ask_events(**_kwargs):
        yield {
            "type": "metadata",
            "mode": "thinking",
            "requested_mode": "fast",
            "actual_mode": "thinking",
            "route": "kb_qa",
            "turn_mode": "kb_only",
            "query_mode": "thinking",
            "trace_id": "req_test",
            "summary_available": True,
            "summary_updated_at": "2026-03-17T10:00:00+08:00",
        }
        yield {"type": "content", "content": "alpha"}
        yield {
            "type": "done",
            "mode": "thinking",
            "requested_mode": "fast",
            "actual_mode": "thinking",
            "route": "kb_qa",
            "turn_mode": "kb_only",
            "final_answer": "alpha",
            "timings": {"total": 0.1},
            "references": ["10.1000/demo"],
            "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "trace_id": "req_test",
            "used_files": [],
            "file_selection": {},
        }

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)
    response = client.post("/api/v1/ask_stream", json={"question": "demo", "requested_mode": "fast"})
    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["connection"] == "keep-alive"
    assert response.headers["x-accel-buffering"] == "no"
    assert [frame["type"] for frame in frames] == ["metadata", "content", "done"]
    assert [frame["seq"] for frame in frames] == [1, 2, 3]
    assert all("ts" in frame for frame in frames)
    assert frames[0]["summary_available"] is True
    assert frames[0]["requested_mode"] == "fast"
    assert frames[-1]["actual_mode"] == "thinking"
    assert frames[-1]["route"] == "kb_qa"
    assert frames[-1]["reference_links"][0]["doi"] == "10.1000/demo"


def test_fastapi_ask_rejects_body_actual_mode_mismatch():
    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)

    response = client.post("/api/v1/thinking/ask", json={"question": "demo", "actual_mode": "fast"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "MODE_MISMATCH"


def test_fastapi_ask_stream_timeout_contract(monkeypatch):
    def fake_stream_ask_events(**_kwargs):
        raise AskTimeoutError("upstream model timeout")
        yield  # pragma: no cover

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)
    response = client.post("/api/v1/ask_stream", json={"question": "timeout"})
    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert [frame["type"] for frame in frames] == ["error"]
    assert frames[0]["code"] == "UPSTREAM_TIMEOUT"
    assert frames[0]["error"] == "upstream_timeout"
    assert frames[0]["retriable"] is True


def test_fastapi_ask_stream_upstream_error_contract(monkeypatch):
    def fake_stream_ask_events(**_kwargs):
        raise AskServiceError("mock upstream failure")
        yield  # pragma: no cover

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)
    response = client.post("/api/v1/ask_stream", json={"question": "boom"})
    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert [frame["type"] for frame in frames] == ["error"]
    assert frames[0]["code"] == "UPSTREAM_ERROR"
    assert frames[0]["error"] == "upstream_error"
    assert frames[0]["message"] == "mock upstream failure"


def test_fastapi_patent_mode_not_implemented_contract():
    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)

    ask_response = client.post("/api/v1/patent/ask", json={"question": "demo"})
    stream_response = client.post("/api/v1/patent/ask_stream", json={"question": "demo"})
    stream_frames = _parse_sse_frames(stream_response.text)

    assert ask_response.status_code == 501
    ask_payload = ask_response.json()
    assert ask_payload["code"] == "NOT_IMPLEMENTED"
    assert ask_payload["error"] == "not_implemented"

    assert stream_response.status_code == 200
    assert [frame["type"] for frame in stream_frames] == ["error"]
    assert stream_frames[0]["code"] == "NOT_IMPLEMENTED"
    assert stream_frames[0]["error"] == "not_implemented"


def test_fastapi_ask_requires_token():
    client = TestClient(create_app())
    response = client.post("/api/v1/ask", json={"question": "demo"})

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "TOKEN_MISSING"
