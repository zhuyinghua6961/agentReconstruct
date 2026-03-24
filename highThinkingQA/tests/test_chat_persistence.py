from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))


def _import_chat_persistence():
    importlib.invalidate_caches()
    for name in (
        "config",
        "server",
        "server.services",
        "server.services.chat_persistence",
        "server.services.conversation_authority_client",
    ):
        sys.modules.pop(name, None)
    config_path = ROOT / "config.py"
    config_spec = importlib.util.spec_from_file_location("config", config_path)
    assert config_spec is not None and config_spec.loader is not None
    config_module = importlib.util.module_from_spec(config_spec)
    sys.modules["config"] = config_module
    config_spec.loader.exec_module(config_module)
    module_path = ROOT / "server" / "services" / "chat_persistence.py"
    spec = importlib.util.spec_from_file_location("_ht_chat_persistence", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ht_chat_persistence"] = module
    spec.loader.exec_module(module)
    return module


def test_load_conversation_context_reads_authority_snapshot(monkeypatch):
    chat_persistence = _import_chat_persistence()

    snapshot = {
        "conversation_id": 11,
        "user_id": 7,
        "snapshot_version": 5,
        "summary": {"title": "authority-summary"},
        "recent_turns": [
            {"role": "user", "content": "old-q", "trace_id": "t1", "created_at": "2026-03-23T10:00:00Z", "message_id": "m1"},
            {"role": "assistant", "content": "old-a", "trace_id": "t2", "created_at": "2026-03-23T10:00:01Z", "message_id": "m2"},
        ],
        "conversation_state": {
            "last_turn_route": "thinking_qa",
            "last_focus_file_ids": [],
            "last_assistant_trace_id": "t2",
        },
    }

    class FakeAuthorityClient:
        def read_context_snapshot(self, **kwargs):
            assert kwargs["user_id"] == 7
            assert kwargs["conversation_id"] == 11
            assert kwargs["requested_mode"] == "thinking"
            assert kwargs["actual_mode"] == "thinking"
            return snapshot

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: FakeAuthorityClient())
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_EXECUTION_CONTEXT_READ_TARGET", "public_service")
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_OVERLAY_ENABLED", False)

    result = chat_persistence.load_conversation_context(
        user_id=7,
        conversation_id=11,
        trace_id="trace-1",
        route="thinking_qa",
        requested_mode="thinking",
        actual_mode="thinking",
        payload=None,
    )

    assert result["snapshot"] == snapshot
    assert result["summary"] == {"title": "authority-summary"}
    assert result["conversation_state"]["last_turn_route"] == "thinking_qa"
    assert result["chat_history"] == snapshot["recent_turns"]
    assert result["pending_overlay"] is None


def test_persist_user_message_delegates_to_authority_client(monkeypatch):
    chat_persistence = _import_chat_persistence()

    calls: list[dict] = []

    class FakeAuthorityClient:
        def write_user_turn(self, **kwargs):
            calls.append(dict(kwargs))
            return {"accepted": True}

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: FakeAuthorityClient())
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_EXECUTION_USER_WRITE_TARGET", "public_service")

    chat_persistence.persist_user_message(
        user_id=7,
        conversation_id=11,
        question="why",
        trace_id="trace-1",
        route="thinking_qa",
        requested_mode="thinking",
        actual_mode="thinking",
        payload=SimpleNamespace(),
        async_enabled=False,
    )

    assert calls == [
        {
            "user_id": 7,
            "conversation_id": 11,
            "content": "why",
            "trace_id": "trace-1",
            "route": "thinking_qa",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
        }
    ]


def test_persist_assistant_summary_stores_overlay_then_accepts_async(monkeypatch):
    chat_persistence = _import_chat_persistence()

    order: list[tuple[str, str]] = []

    class FakeAuthorityClient:
        def accept_assistant_turn_async(self, **kwargs):
            order.append(("accept", kwargs["answer_text"]))
            return {"accepted": True}

    class FakeDispatcher:
        def submit(self, *, key, fn, args=(), kwargs=None):
            order.append(("dispatch", key))
            return fn(*args, **(kwargs or {}))

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: FakeAuthorityClient())
    monkeypatch.setattr(chat_persistence, "get_default_dispatcher", lambda: FakeDispatcher())
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_ASSISTANT_WRITE_TARGET", "public_service")
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_OVERLAY_ENABLED", True)
    monkeypatch.setattr(
        chat_persistence,
        "_store_pending_assistant_overlay",
        lambda **kwargs: order.append(("overlay", kwargs["assistant_content"])) or True,
    )

    chat_persistence.persist_assistant_summary(
        user_id=7,
        conversation_id=11,
        trace_id="trace-1",
        route="thinking_qa",
        requested_mode="thinking",
        actual_mode="thinking",
        summary={
            "assistant_content": "final-answer",
            "done_seen": True,
            "trace_id": "trace-1",
            "route": "thinking_qa",
            "timings": {"total_ms": 100},
        },
        async_enabled=True,
    )

    assert order == [
        ("overlay", "final-answer"),
        ("dispatch", "conversation:7:11"),
        ("accept", "final-answer"),
    ]


def test_load_context_merges_pending_overlay_when_snapshot_lags(monkeypatch):
    chat_persistence = _import_chat_persistence()

    snapshot = {
        "conversation_id": 11,
        "user_id": 7,
        "snapshot_version": 5,
        "summary": {},
        "recent_turns": [
            {"role": "user", "content": "old-q", "trace_id": "t1", "created_at": "", "message_id": "m1"},
        ],
        "conversation_state": {"last_assistant_trace_id": "older"},
    }

    class FakeAuthorityClient:
        def read_context_snapshot(self, **kwargs):
            return snapshot

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: FakeAuthorityClient())
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_EXECUTION_CONTEXT_READ_TARGET", "public_service")
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_OVERLAY_ENABLED", True)
    monkeypatch.setattr(
        chat_persistence,
        "_load_pending_assistant_overlay",
        lambda **kwargs: {"trace_id": "trace-2", "route": "thinking_qa", "assistant_content": "pending-answer"},
    )
    monkeypatch.setattr(chat_persistence, "_clear_pending_assistant_overlay", lambda **kwargs: True)

    result = chat_persistence.load_conversation_context(
        user_id=7,
        conversation_id=11,
        trace_id="trace-3",
        route="thinking_qa",
        requested_mode="thinking",
        actual_mode="thinking",
        payload=None,
    )

    assert result["chat_history"][-1] == {
        "role": "assistant",
        "content": "pending-answer",
        "trace_id": "trace-2",
        "created_at": "",
        "message_id": "",
    }
    assert result["pending_overlay"] == {
        "trace_id": "trace-2",
        "route": "thinking_qa",
        "assistant_content": "pending-answer",
    }


def test_shadow_public_service_keeps_legacy_read_write_but_emits_shadow_write(monkeypatch):
    chat_persistence = _import_chat_persistence()

    local_calls: list[tuple[str, dict]] = []
    shadow_calls: list[dict] = []

    class FakeConversationService:
        def add_message(self, **kwargs):
            local_calls.append(("add_message", dict(kwargs)))
            return {"success": True, "data": {"message_id": 1}}

    class FakeAuthorityClient:
        def write_user_turn(self, **kwargs):
            shadow_calls.append(dict(kwargs))
            return {"accepted": True}

    class FakeDispatcher:
        def submit(self, *, key, fn, args=(), kwargs=None):
            local_calls.append(("dispatch", {"key": key}))
            return fn(*args, **(kwargs or {}))

    monkeypatch.setattr(chat_persistence, "conversation_service", FakeConversationService())
    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: FakeAuthorityClient())
    monkeypatch.setattr(chat_persistence, "get_default_dispatcher", lambda: FakeDispatcher())
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_EXECUTION_USER_WRITE_TARGET", "shadow_public_service")

    chat_persistence.persist_user_message(
        user_id=7,
        conversation_id=11,
        question="shadow-q",
        trace_id="trace-1",
        route="thinking_qa",
        requested_mode="thinking",
        actual_mode="thinking",
        payload=SimpleNamespace(),
        async_enabled=False,
    )

    assert local_calls[0] == (
        "add_message",
        {
            "user_id": 7,
            "conversation_id": 11,
            "role": "user",
            "content": "shadow-q",
            "metadata": {"source": "ask_stream"},
        },
    )
    assert local_calls[1] == ("dispatch", {"key": "conversation:7:11"})
    assert shadow_calls[0]["content"] == "shadow-q"


def test_shadow_public_service_failure_does_not_break_legacy_execution(monkeypatch):
    chat_persistence = _import_chat_persistence()

    local_calls: list[dict] = []
    warnings: list[str] = []

    class FakeConversationService:
        def add_message(self, **kwargs):
            local_calls.append(dict(kwargs))
            return {"success": True, "data": {"message_id": 1}}

    class FakeAuthorityClient:
        def write_user_turn(self, **kwargs):
            raise RuntimeError("shadow-down")

    class FakeDispatcher:
        def submit(self, *, key, fn, args=(), kwargs=None):
            return fn(*args, **(kwargs or {}))

    monkeypatch.setattr(chat_persistence, "conversation_service", FakeConversationService())
    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: FakeAuthorityClient())
    monkeypatch.setattr(chat_persistence, "get_default_dispatcher", lambda: FakeDispatcher())
    monkeypatch.setattr(chat_persistence.config, "CONVERSATION_EXECUTION_USER_WRITE_TARGET", "shadow_public_service")
    monkeypatch.setattr(chat_persistence.logger, "warning", lambda message, *args, **kwargs: warnings.append(str(message)))

    chat_persistence.persist_user_message(
        user_id=7,
        conversation_id=11,
        question="shadow-q",
        trace_id="trace-1",
        route="thinking_qa",
        requested_mode="thinking",
        actual_mode="thinking",
        payload=SimpleNamespace(),
        async_enabled=False,
    )

    assert local_calls == [
        {
            "user_id": 7,
            "conversation_id": 11,
            "role": "user",
            "content": "shadow-q",
            "metadata": {"source": "ask_stream"},
        }
    ]
    assert warnings
