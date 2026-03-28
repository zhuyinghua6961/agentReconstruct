from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from highThinkingQA.server.services.conversation_authority_client import (
    ConversationAuthorityClient,
)


def test_write_user_turn_uses_highthinking_contract() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            201,
            json={
                "success": True,
                "conversation_id": 42,
                "message_id": "msg-1",
                "trace_id": "trace-1",
                "idempotency_key": "42:trace-1:user",
                "created_at": "2026-03-23T00:00:00Z",
                "deduped": False,
            },
        )

    client = ConversationAuthorityClient(
        base_url="http://authority.test",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.write_user_turn(
        user_id=7,
        conversation_id=42,
        trace_id="trace-1",
        route="/api/ask",
        requested_mode="fast",
        actual_mode="fast",
        content="hello",
        selected_file_ids=[5, "5", 9, 0, -2, "bad"],
        last_turn_route_hint="  /api/ask  ",
    )

    assert response == {
        "success": True,
        "conversation_id": 42,
        "message_id": "msg-1",
        "trace_id": "trace-1",
        "idempotency_key": "42:trace-1:user",
        "created_at": "2026-03-23T00:00:00Z",
        "deduped": False,
    }
    assert captured["method"] == "POST"
    assert captured["url"] == "http://authority.test/internal/conversations/42/messages/user"
    assert captured["headers"]["x-internal-service-name"] == "highThinkingQA"
    assert captured["headers"]["x-internal-service-token"] == "secret-token"
    assert captured["headers"]["x-trace-id"] == "trace-1"
    assert captured["json"] == {
        "conversation_id": 42,
        "user_id": 7,
        "trace_id": "trace-1",
        "source_service": "highThinkingQA",
        "route": "/api/ask",
        "requested_mode": "thinking",
        "actual_mode": "thinking",
        "idempotency_key": "42:trace-1:user",
        "message": {
            "role": "user",
            "content": "hello",
        },
        "context_hints": {
            "selected_file_ids": [5, 9],
            "last_turn_route_hint": "/api/ask",
        },
    }

    client.close()


def test_read_context_snapshot_uses_thinking_mode_contract() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "conversation_id": 42,
                "user_id": 7,
                "snapshot_version": 3,
                "updated_at": "2026-03-23T00:00:00Z",
                "summary": {
                    "short_summary": "short",
                    "memory_facts": [{"fact": "A"}],
                    "open_threads": [{"thread": "B"}],
                },
                "recent_turns": [
                    {
                        "message_id": "msg-1",
                        "role": "user",
                        "content": "hello",
                        "created_at": "2026-03-23T00:00:00Z",
                        "trace_id": "trace-1",
                    }
                ],
                "conversation_state": {
                    "last_turn_route": "/api/ask",
                    "last_focus_file_ids": [11],
                    "last_assistant_trace_id": "trace-0",
                },
            },
        )

    client = ConversationAuthorityClient(
        base_url="http://authority.test",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.read_context_snapshot(
        user_id=7,
        conversation_id=42,
        trace_id="trace-1",
        route="/api/ask",
        requested_mode="fast",
        actual_mode="fast",
    )

    assert response == {
        "conversation_id": 42,
        "user_id": 7,
        "snapshot_version": 3,
        "updated_at": "2026-03-23T00:00:00Z",
        "summary": {
            "short_summary": "short",
            "memory_facts": [{"fact": "A"}],
            "open_threads": [{"thread": "B"}],
        },
        "recent_turns": [
            {
                "message_id": "msg-1",
                "role": "user",
                "content": "hello",
                "created_at": "2026-03-23T00:00:00Z",
                "trace_id": "trace-1",
            }
        ],
        "conversation_state": {
            "last_turn_route": "/api/ask",
            "last_focus_file_ids": [11],
            "last_assistant_trace_id": "trace-0",
        },
    }
    assert captured["method"] == "GET"
    assert captured["url"] == (
        "http://authority.test/internal/conversations/42/context-snapshot"
        "?user_id=7&trace_id=trace-1&source_service=highThinkingQA"
        "&route=%2Fapi%2Fask&requested_mode=thinking&actual_mode=thinking"
    )
    assert captured["headers"]["x-internal-service-name"] == "highThinkingQA"
    assert captured["headers"]["x-internal-service-token"] == "secret-token"
    assert captured["headers"]["x-trace-id"] == "trace-1"

    client.close()


def test_accept_assistant_turn_async_uses_highthinking_service_name() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            202,
            json={
                "accepted": True,
                "event_id": "assistant-async:42:trace-1",
                "trace_id": "trace-1",
                "idempotency_key": "42:trace-1:assistant",
                "status": "accepted",
            },
        )

    client = ConversationAuthorityClient(
        base_url="http://authority.test",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.accept_assistant_turn_async(
        user_id=7,
        conversation_id=42,
        trace_id="trace-1",
        route="/api/ask",
        requested_mode="fast",
        actual_mode="fast",
        answer_text="final answer",
        steps=[{"type": "reasoning"}],
        references=[{"title": "doc"}],
        reference_objects=[{"doi": "10.1/a", "section_name": "Discussion", "chunk_index": 2}],
        reference_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
        pdf_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
        doi_locations={"10.1/a": [{"section": "Discussion", "chunk_index": 2}]},
        used_files=[{"file_id": 11}],
        timings={"total_ms": 12},
    )

    assert response == {
        "accepted": True,
        "event_id": "assistant-async:42:trace-1",
        "trace_id": "trace-1",
        "idempotency_key": "42:trace-1:assistant",
        "status": "accepted",
    }
    assert captured["method"] == "POST"
    assert captured["url"] == "http://authority.test/internal/conversations/42/messages/assistant-async"
    assert captured["headers"]["x-internal-service-name"] == "highThinkingQA"
    assert captured["headers"]["x-internal-service-token"] == "secret-token"
    assert captured["headers"]["x-trace-id"] == "trace-1"
    assert captured["json"] == {
        "conversation_id": 42,
        "user_id": 7,
        "trace_id": "trace-1",
        "source_service": "highThinkingQA",
        "route": "/api/ask",
        "requested_mode": "thinking",
        "actual_mode": "thinking",
        "idempotency_key": "42:trace-1:assistant",
        "final_event": {
            "done_seen": True,
            "answer_text": "final answer",
            "steps": [{"type": "reasoning"}],
            "references": [{"title": "doc"}],
            "reference_objects": [{"doi": "10.1/a", "section_name": "Discussion", "chunk_index": 2}],
            "reference_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
            "pdf_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
            "doi_locations": {"10.1/a": [{"section": "Discussion", "chunk_index": 2}]},
            "used_files": [{"file_id": 11}],
            "timings": {"total_ms": 12},
        },
    }

    client.close()
