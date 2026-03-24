from __future__ import annotations

from types import SimpleNamespace

from server_fastapi.routers.ask import _persist_assistant_message_if_needed, _persist_user_message_if_needed


def test_persist_assistant_message_routes_through_chat_persistence(monkeypatch):
    calls: list[dict] = []
    local_calls: list[tuple[str, dict]] = []

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
    monkeypatch.setattr(
        "server_fastapi.routers.ask.conversation_service.add_message",
        lambda **kwargs: local_calls.append(("add_message", dict(kwargs))) or {"success": True, "data": {"message_id": 1}},
    )
    monkeypatch.setattr(
        "server_fastapi.routers.ask.conversation_service.refresh_conversation_summary",
        lambda **kwargs: local_calls.append(("refresh_conversation_summary", dict(kwargs))) or {"success": True, "data": {"summary": {}}},
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
    assert local_calls == []



def test_persist_assistant_message_delegates_to_chat_persistence(monkeypatch):
    calls: list[dict] = []
    local_calls: list[tuple[str, dict]] = []

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
    monkeypatch.setattr(
        "server_fastapi.routers.ask.conversation_service.add_message",
        lambda **kwargs: local_calls.append(("add_message", dict(kwargs))) or {"success": True, "data": {"message_id": 1}},
    )
    monkeypatch.setattr(
        "server_fastapi.routers.ask.conversation_service.refresh_conversation_summary",
        lambda **kwargs: local_calls.append(("refresh_conversation_summary", dict(kwargs))) or {"success": True, "data": {"summary": {}}},
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
    assert local_calls == []



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
