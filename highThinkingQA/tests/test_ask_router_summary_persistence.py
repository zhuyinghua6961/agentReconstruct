from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from types import SimpleNamespace

import server_fastapi.routers.ask as ask_router
from server_fastapi.routers.ask import _persist_assistant_message_if_needed, _persist_user_message_if_needed
from fastapi.testclient import TestClient
from server_fastapi.app import create_app
from server_fastapi.auth.deps import AuthContext, require_auth_context


def test_ask_router_no_longer_exposes_local_conversation_authority():
    assert not hasattr(ask_router, "conversation_service")
    assert not hasattr(ask_router, "_persist_message_task")


def test_persist_assistant_message_routes_through_chat_persistence(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr("server_fastapi.routers.ask._chat_persist_async_enabled", lambda request: True)
    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_assistant_summary": staticmethod(lambda **kwargs: calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
            state=SimpleNamespace(config={"CHAT_PERSIST_ENABLED": True}),
        )
    )
    ask_request = SimpleNamespace(
        user_id=7,
        conversation_id=11,
        requested_mode="thinking",
        actual_mode="thinking",
        route="kb_qa",
        trace_id="trace-1",
    )

    _persist_assistant_message_if_needed(
        request=request,
        ask_request=ask_request,
        summary={
            "assistant_content": "总结完成",
            "query_mode": "thinking",
            "references": [{"doi": "10.1000/demo"}],
            "reference_objects": [{"doi": "10.1000/demo", "section_name": "Results"}],
            "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "doi_locations": {"10.1000/demo": [{"start": 1, "end": 3}]},
            "steps": [{"step": "step1", "message": "阶段1", "status": "success"}],
            "route": "kb_qa",
            "used_files": [{"file_id": 9}],
            "timings": {"total_ms": 123},
            "trace_id": "trace-1",
            "file_selection": {"selected_ids": [9]},
            "done_seen": True,
        },
    )

    assert calls == [
        {
            "user_id": 7,
            "conversation_id": 11,
            "trace_id": "trace-1",
            "route": "kb_qa",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "summary": {
                "assistant_content": "总结完成",
                "query_mode": "thinking",
                "references": [{"doi": "10.1000/demo"}],
                "reference_objects": [{"doi": "10.1000/demo", "section_name": "Results"}],
                "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "doi_locations": {"10.1000/demo": [{"start": 1, "end": 3}]},
                "steps": [{"step": "step1", "message": "阶段1", "status": "success"}],
                "route": "kb_qa",
                "used_files": [{"file_id": 9}],
                "timings": {"total_ms": 123},
                "trace_id": "trace-1",
                "file_selection": {"selected_ids": [9]},
                "done_seen": True,
            },
            "async_enabled": True,
        }
    ]



def test_persist_assistant_message_delegates_to_chat_persistence(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr("server_fastapi.routers.ask._chat_persist_async_enabled", lambda request: True)
    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_assistant_summary": staticmethod(lambda **kwargs: calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
            state=SimpleNamespace(config={"CHAT_PERSIST_ENABLED": True}),
        )
    )
    ask_request = SimpleNamespace(
        user_id=7,
        conversation_id=11,
        question="原问题",
        requested_mode="thinking",
        actual_mode="thinking",
        route="thinking_qa",
        options={},
    )

    _persist_assistant_message_if_needed(
        request=request,
        ask_request=ask_request,
        summary={
            "assistant_content": "总结完成",
            "query_mode": "thinking",
            "references": [{"doi": "10.1000/demo"}],
            "reference_objects": [{"doi": "10.1000/demo", "section_name": "Results"}],
            "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
            "doi_locations": {"10.1000/demo": [{"start": 1, "end": 3}]},
            "steps": [{"step": "step1", "message": "阶段1", "status": "success"}],
            "route": "thinking_qa",
            "used_files": [],
            "timings": {"total_ms": 123},
            "trace_id": "trace-1",
            "file_selection": {},
            "done_seen": True,
        },
    )

    assert calls == [
        {
            "user_id": 7,
            "conversation_id": 11,
            "trace_id": "",
            "route": "thinking_qa",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "summary": {
                "assistant_content": "总结完成",
                "query_mode": "thinking",
                "references": [{"doi": "10.1000/demo"}],
                "reference_objects": [{"doi": "10.1000/demo", "section_name": "Results"}],
                "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "doi_locations": {"10.1000/demo": [{"start": 1, "end": 3}]},
                "steps": [{"step": "step1", "message": "阶段1", "status": "success"}],
                "route": "thinking_qa",
                "used_files": [],
                "timings": {"total_ms": 123},
                "trace_id": "trace-1",
                "file_selection": {},
                "done_seen": True,
            },
            "async_enabled": True,
        }
    ]



def test_persist_user_message_delegates_to_chat_persistence(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr("server_fastapi.routers.ask._chat_persist_async_enabled", lambda request: True)
    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
            state=SimpleNamespace(config={"CHAT_PERSIST_ENABLED": True}),
        )
    )
    ask_request = SimpleNamespace(
        user_id=7,
        conversation_id=11,
        question="原问题",
        requested_mode="thinking",
        actual_mode="thinking",
        route="thinking_qa",
        trace_id="trace-1",
    )

    _persist_user_message_if_needed(request=request, ask_request=ask_request)

    assert calls == [
        {
            "user_id": 7,
            "conversation_id": 11,
            "question": "原问题",
            "trace_id": "trace-1",
            "route": "thinking_qa",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "payload": ask_request,
            "async_enabled": True,
        }
    ]


def test_stream_without_done_frame_still_persists_final_summary_via_completion_callback(monkeypatch):
    assistant_calls: list[dict] = []
    user_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: user_calls.append(dict(kwargs))),
                "persist_assistant_summary": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )

    def fake_stream_ask_events(**kwargs):
        completion_callback = kwargs["completion_callback"]
        yield {
            "type": "metadata",
            "mode": "thinking",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "route": "kb_qa",
            "turn_mode": "kb_only",
            "query_mode": "thinking",
            "trace_id": kwargs["trace_id"],
        }
        yield {"type": "content", "content": "alpha "}
        yield {"type": "content", "content": "[DOI: 10.1000/demo]"}
        completion_callback(
            {
                "type": "done",
                "mode": "thinking",
                "requested_mode": "thinking",
                "actual_mode": "thinking",
                "route": "kb_qa",
                "turn_mode": "kb_only",
                "final_answer": "alpha [DOI: 10.1000/demo]",
                "timings": {"total": 0.1},
                "references": [{"doi": "10.1000/demo"}],
                "reference_objects": [{"doi": "10.1000/demo", "section_name": "Results", "chunk_index": 2}],
                "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "doi_locations": {"10.1000/demo": [{"section": "Results", "chunk_index": 2}]},
                "used_files": [{"file_id": 5}],
                "file_selection": {"selected_ids": [5]},
                "trace_id": kwargs["trace_id"],
            }
        )

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask_stream",
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 200
    frames = [
        json.loads(chunk[6:])
        for chunk in response.text.split("\n\n")
        if chunk.startswith("data: ")
    ]
    assert [frame["type"] for frame in frames] == ["metadata", "content", "content"]
    assert len(user_calls) == 1
    assert len(assistant_calls) == 1
    assert assistant_calls[0]["summary"]["done_seen"] is True
    assert assistant_calls[0]["summary"]["reference_objects"] == [{"doi": "10.1000/demo", "section_name": "Results", "chunk_index": 2}]
    assert assistant_calls[0]["summary"]["doi_locations"] == {"10.1000/demo": [{"section": "Results", "chunk_index": 2}]}


def test_stream_error_persists_failed_terminal_before_error_frame(monkeypatch):
    assistant_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: None),
                "persist_assistant_terminal": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )

    def fake_stream_ask_events(**kwargs):
        yield {"type": "metadata", "query_mode": "thinking", "trace_id": kwargs["trace_id"]}
        yield {"type": "content", "content": "partial "}
        yield {
            "type": "error",
            "code": "UPSTREAM_ERROR",
            "error": "upstream_error",
            "message": "boom",
            "retriable": True,
            "trace_id": kwargs["trace_id"],
        }

    original_to_sse = ask_router._to_sse_line

    def _asserting_to_sse(payload: dict, *, seq: int) -> str:
        if payload.get("type") == "error":
            assert len(assistant_calls) == 1
            assert assistant_calls[0]["terminal_status"] == "failed"
            assert assistant_calls[0]["summary"]["assistant_content"] == "partial"
        return original_to_sse(payload, seq=seq)

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)
    monkeypatch.setattr("server_fastapi.routers.ask._to_sse_line", _asserting_to_sse)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask_stream",
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 200
    frames = [
        json.loads(chunk[6:])
        for chunk in response.text.split("\n\n")
        if chunk.startswith("data: ")
    ]
    assert [frame["type"] for frame in frames] == ["metadata", "content", "error"]
    assert assistant_calls[0]["failure"]["message"] == "boom"
    assert assistant_calls[0]["async_enabled"] is False


def test_stream_cancel_error_persists_canceled_terminal(monkeypatch):
    assistant_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: None),
                "persist_assistant_terminal": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )

    def fake_stream_ask_events(**kwargs):
        yield {"type": "metadata", "query_mode": "thinking", "trace_id": kwargs["trace_id"]}
        yield {"type": "content", "content": "partial "}
        yield {
            "type": "error",
            "code": "ASK_CANCELLED",
            "error": "cancelled",
            "message": "cancelled",
            "retriable": False,
            "trace_id": kwargs["trace_id"],
        }

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask_stream",
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 200
    assert assistant_calls[0]["terminal_status"] == "canceled"
    assert assistant_calls[0]["failure"]["retriable"] is False
    assert assistant_calls[0]["async_enabled"] is False


def test_sync_error_persists_failed_terminal_before_error_response(monkeypatch):
    assistant_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: None),
                "persist_assistant_terminal": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr("server_fastapi.routers.ask.execute_ask", lambda **kwargs: (_ for _ in ()).throw(ask_router.AskServiceError("boom")))

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask",
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 502
    assert assistant_calls[0]["terminal_status"] == "failed"
    assert assistant_calls[0]["failure"]["message"] == "boom"
    assert assistant_calls[0]["async_enabled"] is False


def test_sync_error_persists_mapped_failure_contract(monkeypatch):
    assistant_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: None),
                "persist_assistant_terminal": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        "server_fastapi.routers.ask.execute_ask",
        lambda **kwargs: (_ for _ in ()).throw(ask_router.ModeNotSupportedError("not supported")),
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask",
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 400
    assert assistant_calls[0]["terminal_status"] == "failed"
    assert assistant_calls[0]["failure"] == {
        "stage": "unknown",
        "code": "MODE_NOT_SUPPORTED",
        "message": "not supported",
        "retriable": False,
    }


def test_stream_error_still_emits_error_frame_when_terminal_persistence_fails(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: None),
                "persist_assistant_terminal": staticmethod(lambda **kwargs: (_ for _ in ()).throw(RuntimeError("persist failed"))),
            },
        )(),
        raising=False,
    )

    def fake_stream_ask_events(**kwargs):
        yield {"type": "content", "content": "partial "}
        yield {
            "type": "error",
            "code": "UPSTREAM_ERROR",
            "error": "upstream_error",
            "message": "boom",
            "retriable": True,
            "trace_id": kwargs["trace_id"],
        }

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask_stream",
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 200
    frames = [
        json.loads(chunk[6:])
        for chunk in response.text.split("\n\n")
        if chunk.startswith("data: ")
    ]
    assert [frame["type"] for frame in frames] == ["content", "error"]
    assert frames[-1]["message"] == "boom"


def test_stream_skips_local_persistence_for_gateway_owned_task(monkeypatch):
    assistant_calls: list[dict] = []
    user_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: user_calls.append(dict(kwargs))),
                "persist_assistant_summary": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )

    def fake_stream_ask_events(**kwargs):
        completion_callback = kwargs["completion_callback"]
        yield {"type": "metadata", "query_mode": "thinking", "trace_id": kwargs["trace_id"]}
        yield {"type": "content", "content": "alpha "}
        completion_callback(
            {
                "type": "done",
                "route": "thinking_qa",
                "query_mode": "thinking",
                "final_answer": "alpha",
                "references": [],
                "trace_id": kwargs["trace_id"],
            }
        )

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask_stream",
        headers={
            "X-Gateway-Task-Execution": "1",
            "X-Gateway-Owned-Persistence": "1",
            "X-Internal-Service-Name": "gateway",
            "X-Internal-Service-Token": str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN") or ""),
        },
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 200
    frames = [
        json.loads(chunk[6:])
        for chunk in response.text.split("\n\n")
        if chunk.startswith("data: ")
    ]
    assert [frame["type"] for frame in frames] == ["metadata", "content"]
    assert user_calls == []
    assert assistant_calls == []


def test_sync_skips_local_persistence_for_gateway_owned_task(monkeypatch):
    assistant_calls: list[dict] = []
    user_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: user_calls.append(dict(kwargs))),
                "persist_assistant_summary": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        "server_fastapi.routers.ask.execute_ask",
        lambda **kwargs: {
            "final_answer": "alpha",
            "metadata": {"query_mode": "thinking"},
            "references": [],
        },
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask",
        headers={
            "X-Gateway-Task-Execution": "1",
            "X-Gateway-Owned-Persistence": "1",
            "X-Internal-Service-Name": "gateway",
            "X-Internal-Service-Token": str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN") or ""),
        },
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["final_answer"] == "alpha"
    assert user_calls == []
    assert assistant_calls == []


def test_sync_public_headers_without_internal_auth_still_persist(monkeypatch):
    assistant_calls: list[dict] = []
    user_calls: list[dict] = []

    monkeypatch.setattr(
        "server_fastapi.routers.ask.chat_persistence",
        type(
            "FakeChatPersistence",
            (),
            {
                "persist_user_message": staticmethod(lambda **kwargs: user_calls.append(dict(kwargs))),
                "persist_assistant_summary": staticmethod(lambda **kwargs: assistant_calls.append(dict(kwargs))),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        "server_fastapi.routers.ask.execute_ask",
        lambda **kwargs: {
            "final_answer": "alpha",
            "metadata": {"query_mode": "thinking"},
            "references": [],
        },
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask",
        headers={
            "X-Gateway-Task-Execution": "1",
            "X-Gateway-Owned-Persistence": "1",
        },
        json={"question": "demo", "requested_mode": "thinking", "conversation_id": 11},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["final_answer"] == "alpha"
    assert len(user_calls) == 1
    assert len(assistant_calls) == 1


async def _collect_streaming_body(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return "".join(chunks)


class _DisconnectingRequest:
    def __init__(self, app):
        self.app = app
        self.headers = {
            "X-Gateway-Task-Execution": "1",
            "X-Gateway-Owned-Persistence": "1",
            "X-Internal-Service-Name": "gateway",
            "X-Internal-Service-Token": str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN") or ""),
        }
        self._checks = 0

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > 1


def test_gateway_owned_sync_disconnect_passes_cancel_event_to_execute_ask(monkeypatch):
    captured: dict[str, object] = {}

    def fake_execute_ask(**kwargs):
        captured["cancel_event"] = kwargs.get("cancel_event")
        cancel_event = kwargs.get("cancel_event")
        assert isinstance(cancel_event, threading.Event)
        deadline = time.time() + 0.5
        while time.time() < deadline and not cancel_event.is_set():
            time.sleep(0.01)
        if cancel_event.is_set():
            raise ask_router.AskCancelledError("cancelled")
        return {"final_answer": "alpha", "metadata": {"query_mode": "thinking"}, "references": []}

    monkeypatch.setattr("server_fastapi.routers.ask.execute_ask", fake_execute_ask)

    app = create_app()
    request = _DisconnectingRequest(app)
    ask_request = SimpleNamespace(
        user_id=7,
        conversation_id=11,
        question="demo",
        requested_mode="thinking",
        actual_mode="thinking",
        route="thinking_qa",
        turn_mode="kb_only",
        trace_id="trace-sync-disconnect",
    )

    try:
        asyncio.run(
            ask_router._execute_sync_ask_with_disconnect_support(
                request=request,
                ask_request=ask_request,
                trace_id="trace-sync-disconnect",
            )
        )
    except Exception as exc:
        assert exc.__class__.__name__ == "AskCancelledError"
    else:  # pragma: no cover
        raise AssertionError("expected AskCancelledError")

    assert isinstance(captured.get("cancel_event"), threading.Event)
    assert captured["cancel_event"].is_set() is True


def test_gateway_owned_stream_disconnect_passes_cancel_event_to_executor(monkeypatch):
    captured: dict[str, object] = {}

    def fake_stream_ask_events(**kwargs):
        captured["cancel_event"] = kwargs.get("cancel_event")
        yield {"type": "metadata", "query_mode": "thinking", "trace_id": kwargs["trace_id"]}
        cancel_event = kwargs.get("cancel_event")
        assert isinstance(cancel_event, threading.Event)
        deadline = time.time() + 0.5
        while time.time() < deadline and not cancel_event.is_set():
            time.sleep(0.01)
        if cancel_event.is_set():
            yield {
                "type": "error",
                "code": "ASK_CANCELLED",
                "error": "cancelled",
                "message": "cancelled",
                "retriable": False,
                "trace_id": kwargs["trace_id"],
            }
            return
        yield {
            "type": "done",
            "route": "thinking_qa",
            "query_mode": "thinking",
            "final_answer": "alpha",
            "references": [],
            "trace_id": kwargs["trace_id"],
        }

    monkeypatch.setattr("server_fastapi.routers.ask.stream_ask_events", fake_stream_ask_events)

    app = create_app()
    request = _DisconnectingRequest(app)
    slot = SimpleNamespace(release=lambda: None)
    ask_request = SimpleNamespace(
        user_id=7,
        conversation_id=11,
        question="demo",
        requested_mode="thinking",
        actual_mode="thinking",
        route="thinking_qa",
        turn_mode="kb_only",
        trace_id="trace-disconnect",
    )

    response = ask_router._build_stream_response(
        request=request,
        ask_request=ask_request,
        trace_id="trace-disconnect",
        slot=slot,
    )
    body = asyncio.run(_collect_streaming_body(response))

    assert isinstance(captured.get("cancel_event"), threading.Event)
    assert '"type": "done"' not in body
