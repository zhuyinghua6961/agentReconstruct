from app.services import chat_persistence


def test_persist_user_message_calls_conversation_service(monkeypatch):
    calls = {}

    class _Service:
        def add_message(self, **kwargs):
            calls["add_message"] = kwargs
            return {"success": True}

    monkeypatch.setattr(chat_persistence, "_get_conversation_service", lambda: _Service())

    chat_persistence.persist_user_message(
        user_id=7,
        conversation_id=12,
        question="hello",
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        payload=None,
    )

    assert calls["add_message"]["user_id"] == 7
    assert calls["add_message"]["conversation_id"] == 12
    assert calls["add_message"]["role"] == "user"
    assert calls["add_message"]["metadata"]["trace_id"] == "trace-1"


def test_persist_assistant_summary_calls_conversation_service_and_refresh(monkeypatch):
    calls = {}

    class _Service:
        def add_message(self, **kwargs):
            calls["add_message"] = kwargs
            return {"success": True}

        def refresh_conversation_summary(self, **kwargs):
            calls["refresh"] = kwargs
            return {"success": True}

    monkeypatch.setattr(chat_persistence, "_get_conversation_service", lambda: _Service())

    chat_persistence.persist_assistant_summary(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        assistant_content="final",
        summary={
            "assistant_content": "final",
            "query_mode": "生成驱动检索（PDF溯源）",
            "references": ["10.1/a"],
            "reference_objects": [{"doi": "10.1/a", "chunk_count": 2}],
            "steps": [{"step": "stage1"}],
            "route": "kb_qa",
            "used_files": [],
            "timings": {"stage1": 1.0},
            "trace_id": "trace-1",
            "file_selection": {},
            "done_seen": True,
        },
        payload=None,
    )

    assert calls["add_message"]["role"] == "assistant"
    assert calls["add_message"]["metadata"]["references"] == ["10.1/a"]
    assert calls["add_message"]["metadata"]["reference_objects"] == [{"doi": "10.1/a", "chunk_count": 2}]
    assert calls["add_message"]["metadata"]["done_seen"] is True
    assert calls["refresh"]["user_id"] == 7
    assert calls["refresh"]["conversation_id"] == 12


def test_persist_assistant_summary_skips_without_done():
    class _Service:
        def add_message(self, **kwargs):
            raise AssertionError('should not persist assistant summary without done')

        def refresh_conversation_summary(self, **kwargs):
            raise AssertionError('should not refresh without done')

    chat_persistence._get_conversation_service = lambda: _Service()
    chat_persistence.persist_assistant_summary(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        assistant_content="final",
        summary={"done_seen": False},
        payload=None,
    )


def test_persist_user_message_submits_background_task_when_async_enabled(monkeypatch):
    calls = {}

    class _Dispatcher:
        def submit(self, **kwargs):
            calls["submit"] = kwargs
            return object()

    monkeypatch.setattr(chat_persistence, "get_default_dispatcher", lambda: _Dispatcher())

    chat_persistence.persist_user_message(
        user_id=7,
        conversation_id=12,
        question="hello",
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        payload=None,
        async_enabled=True,
    )

    assert calls["submit"]["key"] == "conversation:7:12"
    assert calls["submit"]["fn"] is chat_persistence._persist_user_message_sync
    assert calls["submit"]["kwargs"]["question"] == "hello"


def test_persist_assistant_summary_submits_background_task_when_async_enabled(monkeypatch):
    calls = {}

    class _Dispatcher:
        def submit(self, **kwargs):
            calls["submit"] = kwargs
            return object()

    monkeypatch.setattr(chat_persistence, "get_default_dispatcher", lambda: _Dispatcher())

    chat_persistence.persist_assistant_summary(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        assistant_content="final",
        summary={"done_seen": True, "query_mode": "kb_qa"},
        payload=None,
        async_enabled=True,
    )

    assert calls["submit"]["key"] == "conversation:7:12"
    assert calls["submit"]["fn"] is chat_persistence._persist_assistant_summary_sync
    assert calls["submit"]["kwargs"]["assistant_content"] == "final"
