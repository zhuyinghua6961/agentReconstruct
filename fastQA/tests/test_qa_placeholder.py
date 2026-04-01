import asyncio
import json
from types import SimpleNamespace
from contextlib import contextmanager

from app.main import app
from app.routers.qa import AskRequest, ask, ask_stream
from app.services import chat_persistence


class _FakeRequest:
    def __init__(self, app_instance, path: str = "/api/ask"):
        self.app = app_instance
        self.headers = {}
        self.url = SimpleNamespace(path=path)

    async def is_disconnected(self) -> bool:
        return False


@contextmanager
def _runtime_state(runtime, status):
    original_runtime = app.state.generation_runtime
    original_ready = getattr(app.state, "generation_runtime_ready", False)
    original_status = dict(app.state.component_status)
    app.state.generation_runtime = runtime
    app.state.generation_runtime_ready = runtime is not None and status == "ok"
    if status is None:
        app.state.component_status.pop("generation_runtime", None)
    else:
        app.state.component_status["generation_runtime"] = {"status": status}
    try:
        yield
    finally:
        app.state.generation_runtime = original_runtime
        app.state.generation_runtime_ready = original_ready
        app.state.component_status = original_status


def _decode_json_response(response) -> dict:
    return json.loads(response.body)


async def _collect_streaming_body(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)


def test_fast_mode_ask_placeholder_returns_not_ready_for_kb_only():
    with _runtime_state(None, "disabled"):
        response = ask(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask"))

    assert response.status_code == 500
    payload = _decode_json_response(response)
    assert payload["code"] == "FASTQA_NOT_READY"
    assert payload["route"] == "kb_qa"


def test_fast_mode_stream_placeholder_returns_sse_error_for_kb_only():
    with _runtime_state(None, "disabled"):
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "metadata"' in body
    assert '"code": "FASTQA_NOT_READY"' in body
    assert '"type": "done"' not in body


def test_fast_mode_stream_can_disable_placeholder_fallback():
    with _runtime_state(None, "disabled"):
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"code": "FASTQA_NOT_READY"' in body
    assert '"fastQA generation runtime is not ready"' in body


def test_fast_mode_stream_dispatches_pdf_route(monkeypatch):
    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "PDF文献查询", "route": "pdf_qa"}
        yield {"type": "content", "content": "abc"}
        yield {"type": "done", "references": ["10.1/a"], "route": "pdf_qa"}

    monkeypatch.setattr("app.routers.qa.iter_pdf_route_events", _events)
    response = ask_stream(
        AskRequest(
            question="总结这篇文献",
            requested_mode="fast",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/a.pdf"}],
        ),
        _FakeRequest(app, "/api/ask_stream"),
    )
    body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"query_mode": "PDF文献查询"' in body
    assert '"content": "abc"' in body
    assert '"route": "pdf_qa"' in body
    assert '"type": "done"' in body


def test_fast_mode_stream_dispatches_tabular_route(monkeypatch):
    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "表格问答", "route": "tabular_qa"}
        yield {"type": "step", "step": "tabular_plan", "status": "success"}
        yield {"type": "done", "references": [], "route": "tabular_qa"}

    monkeypatch.setattr("app.routers.qa.iter_tabular_route_events", _events)
    response = ask_stream(
        AskRequest(
            question="统计这个表格",
            requested_mode="fast",
            route="tabular_qa",
            source_scope="table",
            turn_mode="file_only",
            execution_files=[{"file_id": 1, "file_type": "excel", "local_path": "/tmp/a.xlsx"}],
        ),
        _FakeRequest(app, "/api/ask_stream"),
    )
    body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"query_mode": "表格问答"' in body
    assert '"step": "tabular_plan"' in body
    assert '"route": "tabular_qa"' in body


def test_fast_mode_stream_returns_http_429_when_slot_exhausted():
    limiter = app.state.ask_limiter
    acquired = []
    for _ in range(limiter.limit):
        acquired.append(limiter.try_acquire())
    assert all(acquired) is True
    try:
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
    finally:
        for _ in range(limiter.limit):
            limiter.release()

    assert response.status_code == 429
    payload = _decode_json_response(response)
    assert payload["code"] == "ASK_STREAM_BUSY"
    assert payload["error"] == "server_busy"


def test_fast_mode_stream_runtime_exception_becomes_error_done(monkeypatch):
    calls = {}

    def _raise(**_kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    def _persist_assistant_terminal_hook(**kwargs):
        calls["assistant"] = kwargs

    def _persist_user_message_hook(**_kwargs):
        return {"success": True}

    def _load_conversation_context_hook(**_kwargs):
        return None

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _raise)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", _persist_assistant_terminal_hook, raising=False)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", _load_conversation_context_hook, raising=False)
    with _runtime_state(object(), "ok"):
        response = ask_stream(
            AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
            _FakeRequest(app, "/api/ask_stream"),
        )
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"code": "FASTQA_RUNTIME_ERROR"' in body
    assert '"type": "done"' not in body
    assert calls["assistant"]["terminal_status"] == "failed"
    assert calls["assistant"]["assistant_content"] == ""
    assert calls["assistant"]["failure"]["stage"] == "runtime_prepare"
    assert calls["assistant"]["failure"]["retriable"] is True


def test_fast_mode_stream_uses_generation_runtime_when_available(monkeypatch):
    class _Runtime:
        pass

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa"}
        yield {"type": "content", "content": "abc"}
        yield {"type": "done", "route": "kb_qa", "references": [{"doi": "10.1/a"}], "timings": {"stage1": 1.0}}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    with _runtime_state(_Runtime(), "ok"):
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"content": "abc"' in body
    assert '"type": "done"' in body
    assert '"reference_links"' in body
    assert '"/api/v1/view_pdf/10.1/a"' in body


def test_fast_mode_ask_uses_route_iterator(monkeypatch):
    monkeypatch.setattr(
        "app.routers.qa._iter_route_frames",
        lambda **_kwargs: iter(
            [
                {"type": "metadata", "route": "kb_qa"},
                {"type": "content", "content": "hello"},
                {"type": "done", "route": "kb_qa", "references": ["10.1/a"], "reference_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1/a"}], "pdf_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1/a"}], "timings": {"stage1": 1.0}},
            ]
        ),
    )
    with _runtime_state(object(), "ok"):
        response = ask(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask"))
        payload = _decode_json_response(response)

    assert response.status_code == 200
    assert payload["final_answer"] == "hello"
    assert payload["references"] == ["10.1/a"]
    assert payload["reference_objects"] == [{"doi": "10.1/a"}]
    assert payload["reference_links"] == [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1/a"}]


def test_fast_mode_stream_uses_server_active_stream_count_instead_of_client_value(monkeypatch):
    captured: dict[str, object] = {}

    def _events(**kwargs):
        captured.update(kwargs)
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    with _runtime_state(object(), "ok"):
        response = ask_stream(
            AskRequest(
                question="hello",
                requested_mode="fast",
                active_stream_count=99,
            ),
            _FakeRequest(app, "/api/ask_stream"),
        )
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "done"' in body
    assert captured["request"].active_stream_count == 1


def test_fast_mode_stream_kb_route_emits_single_authoritative_metadata(monkeypatch):
    def _events(**_kwargs):
        yield {"type": "thinking", "message": "阶段一：生成深度预回答与检索规划..."}
        yield {"type": "metadata", "query_mode": "生成驱动检索（PDF溯源）", "route": "kb_qa"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    with _runtime_state(object(), "ok"):
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert body.count('"type": "metadata"') == 1
    assert '"query_mode": "生成驱动检索（PDF溯源）"' in body


def test_trace_id_generated_when_missing(monkeypatch):
    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    with _runtime_state(object(), "ok"):
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"trace_id": "' in body
    assert "fastqa-pending" not in body


def test_fast_mode_stream_logs_summary_via_ask_stream_tap(monkeypatch):
    logged = {}

    class _Logger:
        def info(self, message, *args):
            logged["message"] = message
            logged["args"] = args

    def _events(**_kwargs):
        yield {"type": "thinking", "message": "阶段一：生成深度预回答与检索规划..."}
        yield {"type": "metadata", "query_mode": "生成驱动检索（PDF溯源）", "route": "kb_qa", "trace_id": "trace-logger"}
        yield {"type": "content", "content": "hello"}
        yield {"type": "done", "route": "kb_qa", "references": ["10.1/a"], "trace_id": "trace-logger", "timings": {"stage1": 1.0}}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    monkeypatch.setattr(app, "logger", _Logger(), raising=False)
    with _runtime_state(object(), "ok"):
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "done"' in body
    assert logged["message"].startswith("fastqa stream summary")
    assert logged["args"][0] == "kb_qa"
    assert logged["args"][1] == "生成驱动检索（PDF溯源）"
    assert logged["args"][2] is True
    assert logged["args"][4] == 1
    assert logged["args"][5] == 1


def test_fast_mode_sync_ask_logs_summary_via_ask_stream_tap(monkeypatch):
    logged = {}

    class _Logger:
        def info(self, message, *args):
            logged["message"] = message
            logged["args"] = args

    monkeypatch.setattr(app, "logger", _Logger(), raising=False)
    monkeypatch.setattr(
        "app.routers.qa._iter_route_frames",
        lambda **_kwargs: iter(
            [
                {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa", "trace_id": "trace-sync"},
                {"type": "content", "content": "hello"},
                {"type": "done", "route": "kb_qa", "references": ["10.1/a"], "trace_id": "trace-sync", "timings": {"stage1": 1.0}},
            ]
        ),
    )
    with _runtime_state(object(), "ok"):
        response = ask(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask"))

    assert response.status_code == 200
    assert logged["message"].startswith("fastqa stream summary")
    assert logged["args"][0] == "kb_qa"
    assert logged["args"][2] is True


def test_fast_mode_stream_invokes_terminal_persistence_hook(monkeypatch):
    calls = {}

    def _persist_user_message_hook(**kwargs):
        calls["user"] = kwargs

    def _persist_assistant_terminal_hook(**kwargs):
        calls["assistant"] = kwargs

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索（PDF溯源）", "route": "kb_qa", "trace_id": "trace-hook", "source_scope": "pdf+kb", "source_usage": {"pdf_used": True, "table_used": False, "kb_used": True}}
        yield {"type": "content", "content": "hello"}
        yield {"type": "done", "route": "kb_qa", "references": ["10.1/a"], "trace_id": "trace-hook", "timings": {"stage1": 1.0}, "source_scope": "pdf+kb", "source_usage": {"pdf_used": True, "table_used": False, "kb_used": True}}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", lambda **_kwargs: None, raising=False)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", _persist_assistant_terminal_hook, raising=False)
    with _runtime_state(object(), "ok"):
        response = ask_stream(
            AskRequest(question="hello", requested_mode="fast", conversation_id=12),
            _FakeRequest(app, "/api/ask_stream"),
        )
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "done"' in body
    assert calls["user"]["conversation_id"] == 12
    assert calls["user"]["question"] == "hello"
    assert calls["assistant"]["conversation_id"] == 12
    assert calls["assistant"]["terminal_status"] == "done"
    assert calls["assistant"]["assistant_content"] == "hello"
    assert calls["assistant"]["summary"]["done_seen"] is True
    assert calls["assistant"]["summary"]["references"] == ["10.1/a"]
    assert calls["assistant"]["summary"]["reference_objects"] == [{"doi": "10.1/a"}]
    assert calls["assistant"]["summary"]["source_scope"] == "pdf+kb"
    assert calls["assistant"]["summary"]["source_usage"] == {"pdf_used": True, "table_used": False, "kb_used": True}


def test_fast_mode_stream_done_does_not_fallback_to_legacy_summary_hook_when_terminal_hook_exists(monkeypatch):
    calls = {"terminal": 0, "legacy": 0}

    def _persist_user_message_hook(**_kwargs):
        return {"success": True}

    def _persist_assistant_terminal_hook(**_kwargs):
        calls["terminal"] += 1

    def _persist_assistant_summary_hook(**_kwargs):
        calls["legacy"] += 1

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa", "trace_id": "trace-done"}
        yield {"type": "content", "content": "hello"}
        yield {"type": "done", "route": "kb_qa", "references": [], "trace_id": "trace-done"}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", lambda **_kwargs: None, raising=False)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", _persist_assistant_terminal_hook, raising=False)
    monkeypatch.setattr(app.state, "persist_assistant_summary_hook", _persist_assistant_summary_hook, raising=False)
    with _runtime_state(object(), "ok"):
        response = ask_stream(
            AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
            _FakeRequest(app, "/api/ask_stream"),
        )
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "done"' in body
    assert calls == {"terminal": 1, "legacy": 0}


def test_fast_mode_stream_persists_failed_terminal_without_done(monkeypatch):
    calls = {}

    def _persist_user_message_hook(**kwargs):
        calls["user"] = kwargs

    def _persist_assistant_terminal_hook(**kwargs):
        calls["assistant"] = kwargs

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa", "trace_id": "trace-hook"}
        yield {"type": "content", "content": "partial"}
        yield {"type": "error", "code": "FASTQA_RUNTIME_ERROR", "error": "boom", "message": "boom", "trace_id": "trace-hook"}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", lambda **_kwargs: None, raising=False)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", _persist_assistant_terminal_hook, raising=False)
    with _runtime_state(object(), "ok"):
        response = ask_stream(
            AskRequest(question="hello", requested_mode="fast", conversation_id=12),
            _FakeRequest(app, "/api/ask_stream"),
        )
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "done"' not in body
    assert '"type": "error"' in body
    assert calls["user"]["conversation_id"] == 12
    assert calls["assistant"]["terminal_status"] == "failed"
    assert calls["assistant"]["assistant_content"] == "partial"
    assert calls["assistant"]["summary"]["done_seen"] is False
    assert calls["assistant"]["failure"]["message"] == "boom"


def test_fast_mode_stream_persistence_hook_receives_user_id_from_body(monkeypatch):
    calls = {}

    def _persist_user_message_hook(**kwargs):
        calls["user"] = kwargs

    def _persist_assistant_terminal_hook(**kwargs):
        calls["assistant"] = kwargs

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa", "trace_id": "trace-user-body"}
        yield {"type": "content", "content": "hello"}
        yield {"type": "done", "route": "kb_qa", "references": [], "trace_id": "trace-user-body"}

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", lambda **_kwargs: None, raising=False)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", _persist_assistant_terminal_hook, raising=False)
    with _runtime_state(object(), "ok"):
        response = ask_stream(
            AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
            _FakeRequest(app, "/api/ask_stream"),
        )
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "done"' in body
    assert calls["user"]["user_id"] == 7
    assert calls["assistant"]["user_id"] == 7


def test_fast_mode_sync_ask_persists_failed_terminal_before_returning_error(monkeypatch):
    calls = {}

    def _persist_user_message_hook(**kwargs):
        calls["user"] = kwargs

    def _persist_assistant_terminal_hook(**kwargs):
        calls["assistant"] = kwargs

    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", lambda **_kwargs: None, raising=False)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", _persist_assistant_terminal_hook, raising=False)
    monkeypatch.setattr(
        "app.routers.qa._iter_route_frames",
        lambda **_kwargs: iter(
            [
                {"type": "metadata", "route": "kb_qa", "query_mode": "kb_qa", "trace_id": "trace-sync-fail"},
                {"type": "content", "content": "partial"},
                {"type": "error", "route": "kb_qa", "trace_id": "trace-sync-fail", "code": "FASTQA_RUNTIME_ERROR", "error": "boom", "message": "boom"},
            ]
        ),
    )

    with _runtime_state(object(), "ok"):
        response = ask(
            AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
            _FakeRequest(app, "/api/ask"),
        )
        payload = _decode_json_response(response)

    assert response.status_code == 500
    assert payload["success"] is False
    assert payload["code"] == "FASTQA_RUNTIME_ERROR"
    assert payload["final_answer"] == "partial"
    assert calls["assistant"]["terminal_status"] == "failed"
    assert calls["assistant"]["assistant_content"] == "partial"
    assert calls["assistant"]["failure"]["message"] == "boom"


def test_fast_mode_stream_persists_canceled_terminal_when_cancel_error_arrives(monkeypatch):
    calls = {}

    def _persist_assistant_terminal_hook(**kwargs):
        calls["assistant"] = kwargs

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa", "trace_id": "trace-cancel"}
        yield {"type": "content", "content": "partial"}
        yield {
            "type": "error",
            "code": "ASK_CANCELLED",
            "error": "cancelled",
            "message": "cancelled",
            "trace_id": "trace-cancel",
        }

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    monkeypatch.setattr(app.state, "persist_user_message_hook", lambda **_kwargs: {"success": True}, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", lambda **_kwargs: None, raising=False)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", _persist_assistant_terminal_hook, raising=False)
    with _runtime_state(object(), "ok"):
        response = ask_stream(
            AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
            _FakeRequest(app, "/api/ask_stream"),
        )
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"code": "ASK_CANCELLED"' in body
    assert calls["assistant"]["terminal_status"] == "canceled"
    assert calls["assistant"]["assistant_content"] == "partial"
    assert calls["assistant"]["failure"]["retriable"] is False


def test_fast_mode_stream_rejects_user_id_mismatch_between_header_and_body():
    request = _FakeRequest(app, "/api/ask_stream")
    request.headers = {"X-User-ID": "8"}
    response = ask_stream(
        AskRequest(question="hello", requested_mode="fast", user_id=7),
        request,
    )

    assert response.status_code == 400
    payload = _decode_json_response(response)
    assert payload["code"] == "USER_ID_MISMATCH"


def test_fast_mode_stream_preserves_reference_objects_while_keeping_reference_strings(monkeypatch):
    class _Runtime:
        pass

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa"}
        yield {"type": "content", "content": "abc"}
        yield {
            "type": "done",
            "route": "kb_qa",
            "references": [{"doi": "10.1/a", "chunk_count": 2, "sample_text": "demo"}],
            "timings": {"stage1": 1.0},
        }

    monkeypatch.setattr("app.routers.qa.qa_kb_service.iter_answer_events", _events)
    with _runtime_state(_Runtime(), "ok"):
        response = ask_stream(AskRequest(question="hello", requested_mode="fast"), _FakeRequest(app, "/api/ask_stream"))
        body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"references": ["10.1/a"]' in body
    assert '"reference_objects": [{"doi": "10.1/a", "chunk_count": 2, "sample_text": "demo"}]' in body


def test_fast_mode_stream_fail_fast_when_authority_user_write_fails(monkeypatch):
    def _persist_user_message_hook(**_kwargs):
        raise RuntimeError("authority down")

    monkeypatch.setattr("app.routers.qa._require_authority_user_write", lambda _request: True)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)

    response = ask_stream(
        AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
        _FakeRequest(app, "/api/ask_stream"),
    )

    assert response.status_code == 500
    payload = _decode_json_response(response)
    assert payload["code"] == "FASTQA_AUTHORITY_PRECONDITION_FAILED"
    assert payload["error"] == "authority down"



def test_fast_mode_stream_fail_fast_when_authority_context_read_fails(monkeypatch):
    def _persist_user_message_hook(**_kwargs):
        return {"success": True}

    def _load_conversation_context_hook(**_kwargs):
        raise RuntimeError("snapshot down")

    monkeypatch.setattr("app.routers.qa._require_authority_context_read", lambda _request: True)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", _load_conversation_context_hook, raising=False)

    response = ask_stream(
        AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
        _FakeRequest(app, "/api/ask_stream"),
    )

    assert response.status_code == 500
    payload = _decode_json_response(response)
    assert payload["code"] == "FASTQA_AUTHORITY_PRECONDITION_FAILED"
    assert payload["error"] == "snapshot down"



def test_fast_mode_sync_ask_applies_authority_context_before_execution(monkeypatch):
    captured = {}

    def _persist_user_message_hook(**_kwargs):
        return {"success": True}

    def _load_conversation_context_hook(**_kwargs):
        return {
            "chat_history": [{"role": "assistant", "content": "previous answer", "trace_id": "trace-prev"}],
            "snapshot": {"conversation_id": 12},
            "conversation_state": {"last_turn_route": "kb_qa"},
            "summary": {"short_summary": "summary"},
            "snapshot_version": 3,
        }

    def _frames(**kwargs):
        captured["chat_history"] = kwargs["adapted_request"].chat_history
        captured["options"] = kwargs["adapted_request"].options
        yield {"type": "metadata", "route": "kb_qa", "query_mode": "kb_qa"}
        yield {"type": "content", "content": "hello"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr("app.routers.qa._require_authority_context_read", lambda _request: True)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", _load_conversation_context_hook, raising=False)
    monkeypatch.setattr("app.routers.qa._iter_route_frames", _frames)

    response = ask(
        AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
        _FakeRequest(app, "/api/ask"),
    )
    payload = _decode_json_response(response)

    assert response.status_code == 200
    assert payload["final_answer"] == "hello"
    assert captured["chat_history"] == [{"role": "assistant", "content": "previous answer", "trace_id": "trace-prev"}]
    assert captured["options"]["authority_conversation_state"] == {"last_turn_route": "kb_qa"}
    assert captured["options"]["authority_summary"] == {"short_summary": "summary"}
    assert captured["options"]["authority_snapshot_version"] == 3


def test_fast_mode_sync_ask_keeps_pending_overlay_metadata_in_options(monkeypatch):
    captured = {}

    def _persist_user_message_hook(**_kwargs):
        return {"success": True}

    def _load_conversation_context_hook(**_kwargs):
        return {
            "chat_history": [
                {"role": "assistant", "content": "previous answer", "trace_id": "trace-prev"},
                {"role": "assistant", "content": "pending final", "trace_id": "trace-pending"},
            ],
            "snapshot": {"conversation_id": 12},
            "conversation_state": {"last_turn_route": "kb_qa"},
            "summary": {"short_summary": "summary"},
            "snapshot_version": 3,
            "pending_overlay": {"trace_id": "trace-pending", "route": "kb_qa", "assistant_content": "pending final"},
        }

    def _frames(**kwargs):
        captured["chat_history"] = kwargs["adapted_request"].chat_history
        captured["options"] = kwargs["adapted_request"].options
        yield {"type": "metadata", "route": "kb_qa", "query_mode": "kb_qa"}
        yield {"type": "content", "content": "hello"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr("app.routers.qa._require_authority_context_read", lambda _request: True)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", _load_conversation_context_hook, raising=False)
    monkeypatch.setattr("app.routers.qa._iter_route_frames", _frames)

    response = ask(
        AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
        _FakeRequest(app, "/api/ask"),
    )
    payload = _decode_json_response(response)

    assert response.status_code == 200
    assert payload["final_answer"] == "hello"
    assert captured["chat_history"][-1] == {"role": "assistant", "content": "pending final", "trace_id": "trace-pending"}
    assert captured["options"]["authority_pending_overlay"] == {
        "trace_id": "trace-pending",
        "route": "kb_qa",
        "assistant_content": "pending final",
    }


def test_fast_mode_sync_ask_ignores_pending_overlay_redis_failure(monkeypatch):
    calls = {}
    captured = {}

    class _Client:
        def read_context_snapshot(self, **kwargs):
            return {
                "conversation_id": 12,
                "user_id": 7,
                "snapshot_version": 3,
                "updated_at": "2026-03-22T12:35:00Z",
                "summary": {"short_summary": "demo", "memory_facts": [], "open_threads": []},
                "recent_turns": [
                    {
                        "message_id": "msg-1",
                        "role": "assistant",
                        "content": "previous answer",
                        "created_at": "2026-03-22T12:34:56Z",
                        "trace_id": "trace-prev",
                    }
                ],
                "conversation_state": {"last_turn_route": "kb_qa", "last_focus_file_ids": [8], "last_assistant_trace_id": "trace-prev"},
            }

    def _persist_user_message_hook(**_kwargs):
        return {"success": True}

    def _load_pending_assistant_overlay(**_kwargs):
        calls["overlay_attempted"] = True
        raise RuntimeError("redis down")

    def _frames(**kwargs):
        captured["chat_history"] = kwargs["adapted_request"].chat_history
        yield {"type": "metadata", "route": "kb_qa", "query_mode": "kb_qa"}
        yield {"type": "content", "content": "hello"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())
    monkeypatch.setattr(chat_persistence, "_load_pending_assistant_overlay", _load_pending_assistant_overlay, raising=False)
    monkeypatch.setattr("app.routers.qa._require_authority_context_read", lambda _request: True)
    monkeypatch.setattr(app.state, "persist_user_message_hook", _persist_user_message_hook, raising=False)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", chat_persistence.load_conversation_context, raising=False)
    monkeypatch.setattr("app.routers.qa._iter_route_frames", _frames)

    response = ask(
        AskRequest(question="hello", requested_mode="fast", conversation_id=12, user_id=7),
        _FakeRequest(app, "/api/ask"),
    )
    payload = _decode_json_response(response)

    assert response.status_code == 200
    assert payload["final_answer"] == "hello"
    assert calls["overlay_attempted"] is True
    assert captured["chat_history"] == [
        {
            "role": "assistant",
            "content": "previous answer",
            "trace_id": "trace-prev",
            "created_at": "2026-03-22T12:34:56Z",
            "message_id": "msg-1",
        }
    ]

from app.modules.generation_pipeline.answer_summary import (
    apply_answer_summary_experiment as apply_fast_answer_summary_experiment,
    summary_experiment_enabled as fast_summary_experiment_enabled,
)


def test_fast_answer_summary_experiment_appends_summary_block_when_enabled():
    answer, meta = apply_fast_answer_summary_experiment(
        "## 主结论\n\n厚电极在高电流密度下容易形成更大的液相锂盐浓度梯度，因此极化上升更快 (doi=10.1/demo)。\n\n电解液传输路径变长、孔隙传质受限以及有效反应面积下降，会共同放大浓差极化并拖低倍率性能 (doi=10.1/demo)。\n\n如果继续提高面载量而不同步优化孔结构和润湿条件，极化会进一步累积，最终表现为容量利用率下降与末端电压提前触底。",
        enabled=True,
    )

    assert answer.startswith("## 主结论")
    assert "\n\n## 总结\n\n- " in answer
    assert meta["generated"] is True
    assert meta["format"] == "bullet_fallback"


def test_fast_answer_summary_experiment_skips_short_answer():
    answer, meta = apply_fast_answer_summary_experiment("结论很短。", enabled=True)

    assert answer == "结论很短。"
    assert meta["generated"] is False
    assert meta["skipped_reason"] == "short_answer"


def test_fast_answer_summary_experiment_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ANSWER_SUMMARY_EXPERIMENT", raising=False)

    assert fast_summary_experiment_enabled() is True


def test_fast_answer_summary_experiment_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("ANSWER_SUMMARY_EXPERIMENT", "0")

    assert fast_summary_experiment_enabled() is False
