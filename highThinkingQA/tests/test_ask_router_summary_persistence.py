from __future__ import annotations

from types import SimpleNamespace

from server_fastapi.routers.ask import _persist_assistant_message_if_needed


def test_persist_assistant_message_refreshes_summary_in_same_task(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeDispatcher:
        def __init__(self) -> None:
            self.submissions: list[dict] = []

        def submit(self, *, key, fn, args=(), kwargs=None):
            self.submissions.append({"key": key})
            fn(*args, **(kwargs or {}))
            return None

    dispatcher = FakeDispatcher()

    monkeypatch.setattr("server_fastapi.routers.ask._chat_persist_async_enabled", lambda request: True)
    monkeypatch.setattr("server_fastapi.routers.ask.get_default_dispatcher", lambda: dispatcher)
    monkeypatch.setattr(
        "server_fastapi.routers.ask.conversation_service.add_message",
        lambda **kwargs: calls.append(("add_message", dict(kwargs))) or {"success": True, "data": {"message_id": 1}},
    )
    monkeypatch.setattr(
        "server_fastapi.routers.ask.conversation_service.refresh_conversation_summary",
        lambda **kwargs: calls.append(("refresh_conversation_summary", dict(kwargs))) or {"success": True, "data": {"summary": {}}},
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
            state=SimpleNamespace(config={"CHAT_PERSIST_ENABLED": True}),
        )
    )
    ask_request = SimpleNamespace(user_id=7, conversation_id=11)

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

    assert dispatcher.submissions == [{"key": "conversation:7:11"}]
    assert [item[0] for item in calls] == ["add_message", "refresh_conversation_summary"]
    assert calls[0][1]["role"] == "assistant"
    assert calls[0][1]["metadata"]["reference_links"][0]["doi"] == "10.1000/demo"
    assert calls[0][1]["metadata"]["doi_locations"]["10.1000/demo"][0]["start"] == 1
    assert calls[0][1]["metadata"]["route"] == "kb_qa"
    assert calls[1][1] == {"user_id": 7, "conversation_id": 11}
