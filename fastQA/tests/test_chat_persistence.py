from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import chat_persistence


def test_persist_user_message_delegates_to_authority_client(monkeypatch):
    calls = {}

    class _Client:
        def write_user_turn(self, **kwargs):
            calls["write_user_turn"] = kwargs
            return {"success": True}

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())

    chat_persistence.persist_user_message(
        user_id=7,
        conversation_id=12,
        question="hello",
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        payload=SimpleNamespace(selected_file_ids=[5, "6", 0, "bad", 6], route="pdf_qa"),
    )

    assert calls["write_user_turn"] == {
        "user_id": 7,
        "conversation_id": 12,
        "trace_id": "trace-1",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "content": "hello",
        "selected_file_ids": [5, 6],
        "last_turn_route_hint": "pdf_qa",
    }


def test_persist_user_message_propagates_authority_write_error(monkeypatch):
    class _Client:
        def write_user_turn(self, **kwargs):
            raise RuntimeError("authority down")

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())

    with pytest.raises(RuntimeError, match="authority down"):
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


def test_persist_assistant_summary_delegates_to_authority_client(monkeypatch):
    calls = {}

    class _Client:
        def accept_assistant_turn_async(self, **kwargs):
            calls["accept_assistant_turn_async"] = kwargs
            return {"accepted": True}

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())

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
            "query_mode": "generated",
            "references": ["10.1/a"],
            "reference_objects": [{"doi": "10.1/a", "chunk_count": 2}],
            "steps": [{"step": "stage1"}],
            "route": "kb_qa",
            "used_files": [{"file_id": 8}],
            "timings": {"stage1": 1.0},
            "trace_id": "trace-1",
            "file_selection": {"selected_file_ids": [8]},
            "source_scope": "pdf+kb",
            "source_usage": {"pdf_used": True, "kb_used": True},
            "raw_model_payload": {"hidden": True},
            "hidden_reasoning": "secret",
            "done_seen": True,
        },
        payload=None,
    )

    assert calls["accept_assistant_turn_async"] == {
        "user_id": 7,
        "conversation_id": 12,
        "trace_id": "trace-1",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "answer_text": "final",
        "steps": [{"step": "stage1"}],
        "references": [{"doi": "10.1/a", "chunk_count": 2}],
        "used_files": [{"file_id": 8}],
        "timings": {"stage1": 1.0},
    }


def test_persist_assistant_summary_skips_without_done(monkeypatch):
    class _Client:
        def accept_assistant_turn_async(self, **kwargs):
            raise AssertionError("should not accept assistant summary without done")

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())

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


def test_load_conversation_context_reads_authority_snapshot(monkeypatch):
    calls = {}

    class _Client:
        def read_context_snapshot(self, **kwargs):
            calls["read_context_snapshot"] = kwargs
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

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())

    payload = chat_persistence.load_conversation_context(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        payload=None,
    )

    assert calls["read_context_snapshot"] == {
        "user_id": 7,
        "conversation_id": 12,
        "trace_id": "trace-1",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
    }
    assert payload["chat_history"] == [
        {
            "role": "assistant",
            "content": "previous answer",
            "trace_id": "trace-prev",
            "created_at": "2026-03-22T12:34:56Z",
            "message_id": "msg-1",
        }
    ]
    assert payload["conversation_state"]["last_focus_file_ids"] == [8]
    assert payload["snapshot_version"] == 3


def test_load_conversation_context_merges_latest_pending_overlay_when_snapshot_lags(monkeypatch):
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

    cleared = {}

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())
    monkeypatch.setattr(
        chat_persistence,
        "_load_pending_assistant_overlay",
        lambda **_kwargs: {
            "trace_id": "trace-pending",
            "route": "kb_qa",
            "assistant_content": "pending final",
        },
        raising=False,
    )
    monkeypatch.setattr(
        chat_persistence,
        "_clear_pending_assistant_overlay",
        lambda **kwargs: cleared.setdefault("kwargs", kwargs),
        raising=False,
    )

    payload = chat_persistence.load_conversation_context(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        payload=None,
    )

    assert payload["chat_history"] == [
        {
            "role": "assistant",
            "content": "previous answer",
            "trace_id": "trace-prev",
            "created_at": "2026-03-22T12:34:56Z",
            "message_id": "msg-1",
        },
        {
            "role": "assistant",
            "content": "pending final",
            "trace_id": "trace-pending",
            "created_at": "",
            "message_id": "",
        },
    ]
    assert payload["pending_overlay"] == {
        "trace_id": "trace-pending",
        "route": "kb_qa",
        "assistant_content": "pending final",
    }
    assert "kwargs" not in cleared


def test_load_conversation_context_clears_pending_overlay_when_authority_converged(monkeypatch):
    class _Client:
        def read_context_snapshot(self, **kwargs):
            return {
                "conversation_id": 12,
                "user_id": 7,
                "snapshot_version": 4,
                "updated_at": "2026-03-22T12:36:00Z",
                "summary": {"short_summary": "demo", "memory_facts": [], "open_threads": []},
                "recent_turns": [
                    {
                        "message_id": "msg-2",
                        "role": "assistant",
                        "content": "pending final",
                        "created_at": "2026-03-22T12:35:59Z",
                        "trace_id": "trace-pending",
                    }
                ],
                "conversation_state": {"last_turn_route": "kb_qa", "last_focus_file_ids": [8], "last_assistant_trace_id": "trace-pending"},
            }

    cleared = {}

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())
    monkeypatch.setattr(
        chat_persistence,
        "_load_pending_assistant_overlay",
        lambda **_kwargs: {
            "trace_id": "trace-pending",
            "route": "kb_qa",
            "assistant_content": "pending final",
        },
        raising=False,
    )
    monkeypatch.setattr(
        chat_persistence,
        "_clear_pending_assistant_overlay",
        lambda **kwargs: cleared.setdefault("kwargs", kwargs),
        raising=False,
    )

    payload = chat_persistence.load_conversation_context(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        payload=None,
    )

    assert payload["chat_history"] == [
        {
            "role": "assistant",
            "content": "pending final",
            "trace_id": "trace-pending",
            "created_at": "2026-03-22T12:35:59Z",
            "message_id": "msg-2",
        }
    ]
    assert payload.get("pending_overlay") is None
    assert cleared["kwargs"] == {"user_id": 7, "conversation_id": 12}


def test_persist_assistant_summary_stores_minimal_pending_overlay(monkeypatch):
    calls = {}

    class _Client:
        def accept_assistant_turn_async(self, **kwargs):
            raise RuntimeError("authority down")

    monkeypatch.setattr(chat_persistence, "_get_authority_client", lambda: _Client())
    monkeypatch.setattr(
        chat_persistence,
        "_store_pending_assistant_overlay",
        lambda **kwargs: calls.setdefault("kwargs", kwargs),
        raising=False,
    )

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
            "query_mode": "generated",
            "references": ["10.1/a"],
            "reference_objects": [{"doi": "10.1/a", "chunk_count": 2}],
            "steps": [{"step": "stage1"}],
            "route": "kb_qa",
            "used_files": [{"file_id": 8}],
            "timings": {"stage1": 1.0},
            "trace_id": "trace-1",
            "file_selection": {"selected_file_ids": [8]},
            "source_scope": "pdf+kb",
            "source_usage": {"pdf_used": True, "kb_used": True},
            "raw_model_payload": {"hidden": True},
            "hidden_reasoning": "secret",
            "done_seen": True,
        },
        payload=None,
    )

    assert calls["kwargs"] == {
        "user_id": 7,
        "conversation_id": 12,
        "trace_id": "trace-1",
        "route": "kb_qa",
        "assistant_content": "final",
    }
