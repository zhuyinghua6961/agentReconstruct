from __future__ import annotations

import httpx
import pytest

from server.services.conversation_authority_client import (
    AuthorityFeatureDisabledError,
    ConversationAuthorityClient,
)



def _transport(handler):
    return httpx.MockTransport(handler)



def test_user_write_uses_patent_mode_contract(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "success": True,
                "conversation_id": 123,
                "message_id": "m_1",
                "trace_id": "req_123",
                "idempotency_key": "123:req_123:user",
                "created_at": "2026-03-25T12:00:00Z",
                "deduped": False,
            },
        )

    client = ConversationAuthorityClient(base_url="http://authority", transport=_transport(handler))
    result = client.write_user_turn(
        user_id=42,
        conversation_id=123,
        trace_id="req_123",
        route="kb_qa",
        requested_mode="patent",
        actual_mode="patent",
        content="Explain the patent novelty.",
    )

    assert result["conversation_id"] == 123
    assert captured["method"] == "POST"
    assert captured["url"] == "http://authority/internal/conversations/123/messages/user"
    assert captured["headers"]["x-internal-service-name"] == "patentQA"
    assert captured["headers"]["x-internal-service-token"] == "secret-token"
    assert captured["headers"]["x-trace-id"] == "req_123"
    assert captured["json"]["source_service"] == "patentQA"
    assert captured["json"]["requested_mode"] == "patent"
    assert captured["json"]["actual_mode"] == "patent"
    assert captured["json"]["idempotency_key"] == "123:req_123:user"



def test_context_snapshot_uses_patent_query_contract(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "conversation_id": 123,
                "user_id": 42,
                "snapshot_version": 7,
                "updated_at": "2026-03-25T12:00:00Z",
                "summary": {},
                "recent_turns": [],
                "conversation_state": {},
            },
        )

    client = ConversationAuthorityClient(base_url="http://authority", transport=_transport(handler))
    result = client.read_context_snapshot(
        user_id=42,
        conversation_id=123,
        trace_id="req_123",
        route="kb_qa",
        requested_mode="patent",
        actual_mode="patent",
    )

    assert result["snapshot_version"] == 7
    assert captured["method"] == "GET"
    assert "requested_mode=patent" in captured["url"]
    assert "actual_mode=patent" in captured["url"]
    assert "source_service=patentQA" in captured["url"]



def test_assistant_accept_uses_patent_idempotency_key(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "accepted": True,
                "event_id": "evt_1",
                "trace_id": "req_123",
                "idempotency_key": "123:req_123:assistant",
                "status": "accepted",
            },
        )

    client = ConversationAuthorityClient(base_url="http://authority", transport=_transport(handler))
    result = client.accept_assistant_turn_async(
        user_id=42,
        conversation_id=123,
        trace_id="req_123",
        route="kb_qa",
        requested_mode="patent",
        actual_mode="patent",
        answer_text="Patent answer",
    )

    assert result["accepted"] is True
    assert captured["json"]["idempotency_key"] == "123:req_123:assistant"
    assert captured["json"]["final_event"]["done_seen"] is True



def test_durable_authority_mode_is_blocked_when_feature_gate_is_off(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "false")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    client = ConversationAuthorityClient(base_url="http://authority", transport=_transport(lambda request: httpx.Response(500)))

    with pytest.raises(AuthorityFeatureDisabledError):
        client.write_user_turn(
            user_id=42,
            conversation_id=123,
            trace_id="req_123",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            content="Explain the patent novelty.",
        )
