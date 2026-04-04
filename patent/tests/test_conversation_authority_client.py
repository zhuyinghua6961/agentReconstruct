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
        route="hybrid_qa",
        source_scope="pdf+kb",
        requested_mode="patent",
        actual_mode="patent",
        content="Explain the patent novelty.",
        selected_file_ids=[11],
        mode_origin_requested_mode="patent",
        mode_origin_execution_backend="patentQA",
        compatibility_route=False,
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
    assert captured["json"]["context_hints"]["mode_origin_requested_mode"] == "patent"
    assert captured["json"]["route"] == "hybrid_qa"
    assert captured["json"]["source_scope"] == "pdf+kb"
    assert captured["json"]["context_hints"]["selected_file_ids"] == [11]
    assert captured["json"]["context_hints"]["mode_origin_execution_backend"] == "patentQA"
    assert captured["json"]["context_hints"]["compatibility_route"] is False



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
        metadata={"mode_origin": {"requested_mode": "patent", "execution_backend": "fastQA", "compatibility_route": True}},
        references=[{"source_type": "patent", "canonical_patent_id": "CN123456789A"}],
        reference_objects=[{"source_type": "patent", "canonical_patent_id": "CN123456789A", "section_type": "claim"}],
        reference_links=[{"type": "original_view", "canonical_patent_id": "CN123456789A", "viewer_uri": "/api/patent/original/CN123456789A"}],
        original_links=[{"type": "original_view", "canonical_patent_id": "CN123456789A", "section": "claim", "viewer_uri": "/api/patent/original/CN123456789A"}],
    )

    assert result["accepted"] is True
    assert captured["json"]["idempotency_key"] == "123:req_123:assistant"
    assert captured["json"]["final_event"]["done_seen"] is True
    assert captured["json"]["final_event"]["metadata"]["mode_origin"]["execution_backend"] == "fastQA"
    assert captured["json"]["final_event"]["reference_objects"][0]["section_type"] == "claim"
    assert captured["json"]["final_event"]["reference_links"][0]["type"] == "original_view"
    assert captured["json"]["final_event"]["original_links"][0]["section"] == "claim"


def test_assistant_terminal_accept_uses_patent_terminal_contract(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "accepted": True,
                "event_id": "evt_terminal_1",
                "trace_id": "req_123",
                "idempotency_key": "123:req_123:assistant",
                "status": "accepted",
            },
        )

    client = ConversationAuthorityClient(base_url="http://authority", transport=_transport(handler))
    result = client.accept_assistant_terminal_async(
        user_id=42,
        conversation_id=123,
        trace_id="req_123",
        route="kb_qa",
        requested_mode="patent",
        actual_mode="patent",
        terminal_status="failed",
        answer_text="",
        steps=[{"step": "stage4", "status": "failed"}],
        timings={"stage4_ms": 21},
        failure={
            "stage": "stage4",
            "message": "patent execution failed at stage4",
            "code": "INTERNAL_ERROR",
            "retriable": False,
        },
    )

    assert result["accepted"] is True
    assert captured["method"] == "POST"
    assert captured["url"] == "http://authority/internal/conversations/123/messages/assistant-terminal-async"
    assert captured["json"]["idempotency_key"] == "123:req_123:assistant"
    assert captured["json"]["terminal_event"]["terminal_status"] == "failed"
    assert captured["json"]["terminal_event"]["done_seen"] is False
    assert captured["json"]["terminal_event"]["failure"]["stage"] == "stage4"
    assert captured["json"]["terminal_event"]["failure"]["message"] == "patent execution failed at stage4"
    assert captured["json"]["terminal_event"]["failure"]["code"] == "INTERNAL_ERROR"
    assert captured["json"]["terminal_event"]["failure"]["retriable"] is False


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
