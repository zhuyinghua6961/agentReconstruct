from __future__ import annotations

import json

import httpx
import pytest

from app.services.conversation_authority_client import ConversationAuthorityClient


def test_write_user_turn_uses_canonical_schema_and_accepts_dedupe():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            201,
            json={
                "success": True,
                "conversation_id": 12,
                "message_id": "msg-1",
                "trace_id": "trace-1",
                "idempotency_key": "12:trace-1:user",
                "created_at": "2026-03-22T12:34:56Z",
                "deduped": True,
            },
        )

    client = ConversationAuthorityClient(
        base_url="http://public-service",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.write_user_turn(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        content="hello authority",
        selected_file_ids=[9, "10", "x", 9, 0],
        last_turn_route_hint="pdf_qa",
    )

    assert observed["method"] == "POST"
    assert observed["url"] == "http://public-service/internal/conversations/12/messages/user"
    assert observed["headers"]["x-internal-service-name"] == "fastQA"
    assert observed["headers"]["x-internal-service-token"] == "secret-token"
    assert observed["headers"]["x-trace-id"] == "trace-1"
    assert observed["body"] == {
        "conversation_id": 12,
        "user_id": 7,
        "trace_id": "trace-1",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "idempotency_key": "12:trace-1:user",
        "message": {
            "role": "user",
            "content": "hello authority",
        },
        "context_hints": {
            "selected_file_ids": [9, 10],
            "last_turn_route_hint": "pdf_qa",
        },
    }
    assert response["message_id"] == "msg-1"
    assert response["deduped"] is True



def test_read_context_snapshot_uses_canonical_contract_without_idempotency_key():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "conversation_id": 12,
                "user_id": 7,
                "snapshot_version": 3,
                "updated_at": "2026-03-22T12:35:00Z",
                "summary": {
                    "short_summary": "",
                    "memory_facts": [],
                    "open_threads": [],
                },
                "recent_turns": [],
                "conversation_state": {
                    "last_turn_route": "kb_qa",
                    "last_focus_file_ids": [],
                    "last_assistant_trace_id": None,
                },
            },
        )

    client = ConversationAuthorityClient(
        base_url="http://public-service",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.read_context_snapshot(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
    )

    assert observed["method"] == "GET"
    assert observed["url"] == "http://public-service/internal/conversations/12/context-snapshot?user_id=7&trace_id=trace-1&source_service=fastQA&route=kb_qa&requested_mode=fast&actual_mode=fast"
    assert observed["headers"]["x-internal-service-name"] == "fastQA"
    assert observed["headers"]["x-internal-service-token"] == "secret-token"
    assert observed["headers"]["x-trace-id"] == "trace-1"
    assert observed["params"] == {
        "user_id": "7",
        "trace_id": "trace-1",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
    }
    assert "idempotency_key" not in observed["params"]
    assert response["snapshot_version"] == 3



def test_accept_assistant_turn_async_uses_canonical_final_event():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            202,
            json={
                "accepted": True,
                "event_id": "assistant-async:12:trace-1",
                "trace_id": "trace-1",
                "idempotency_key": "12:trace-1:assistant",
                "status": "accepted",
            },
        )

    client = ConversationAuthorityClient(
        base_url="http://public-service",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.accept_assistant_turn_async(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        answer_text="final answer",
        steps=[{"step": "stage1"}],
        references=[{"doi": "10.1/a"}],
        reference_objects=[{"doi": "10.1/a", "section_name": "Results", "chunk_index": 3}],
        reference_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
        pdf_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
        doi_locations={"10.1/a": [{"section": "Results", "chunk_index": 3}]},
        used_files=[{"file_id": 8}],
        timings={"latency_ms": 321},
    )

    assert observed["method"] == "POST"
    assert observed["url"] == "http://public-service/internal/conversations/12/messages/assistant-async"
    assert observed["headers"]["x-internal-service-name"] == "fastQA"
    assert observed["headers"]["x-internal-service-token"] == "secret-token"
    assert observed["headers"]["x-trace-id"] == "trace-1"
    assert observed["body"] == {
        "conversation_id": 12,
        "user_id": 7,
        "trace_id": "trace-1",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "idempotency_key": "12:trace-1:assistant",
        "final_event": {
            "done_seen": True,
            "answer_text": "final answer",
            "steps": [{"step": "stage1"}],
            "references": [{"doi": "10.1/a"}],
            "reference_objects": [{"doi": "10.1/a", "section_name": "Results", "chunk_index": 3}],
            "reference_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
            "pdf_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
            "doi_locations": {"10.1/a": [{"section": "Results", "chunk_index": 3}]},
            "used_files": [{"file_id": 8}],
            "timings": {"latency_ms": 321},
        },
    }
    assert response["accepted"] is True
    assert response["status"] == "accepted"


def test_accept_assistant_turn_terminal_async_uses_terminal_contract():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            202,
            json={
                "accepted": True,
                "event_id": "assistant-async:12:trace-1",
                "trace_id": "trace-1",
                "idempotency_key": "12:trace-1:assistant",
                "status": "accepted",
            },
        )

    client = ConversationAuthorityClient(
        base_url="http://public-service",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.accept_assistant_turn_terminal_async(
        user_id=7,
        conversation_id=12,
        trace_id="trace-1",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        terminal_status="failed",
        answer_text="partial answer",
        steps=[{"step": "stage4"}],
        references=[{"doi": "10.1/a"}],
        reference_objects=[{"doi": "10.1/a", "section_name": "Results", "chunk_index": 3}],
        reference_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
        pdf_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
        doi_locations={"10.1/a": [{"section": "Results", "chunk_index": 3}]},
        used_files=[{"file_id": 8}],
        timings={"latency_ms": 321},
        failure={
            "stage": "citation_validation",
            "message": "validation timeout",
            "code": "VALIDATION_TIMEOUT",
            "retriable": True,
        },
    )

    assert observed["method"] == "POST"
    assert observed["url"] == "http://public-service/internal/conversations/12/messages/assistant-terminal-async"
    assert observed["headers"]["x-internal-service-name"] == "fastQA"
    assert observed["headers"]["x-internal-service-token"] == "secret-token"
    assert observed["headers"]["x-trace-id"] == "trace-1"
    assert observed["body"] == {
        "conversation_id": 12,
        "user_id": 7,
        "trace_id": "trace-1",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "idempotency_key": "12:trace-1:assistant",
        "terminal_event": {
            "terminal_status": "failed",
            "done_seen": False,
            "answer_text": "partial answer",
            "steps": [{"step": "stage4"}],
            "references": [{"doi": "10.1/a"}],
            "reference_objects": [{"doi": "10.1/a", "section_name": "Results", "chunk_index": 3}],
            "reference_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
            "pdf_links": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
            "doi_locations": {"10.1/a": [{"section": "Results", "chunk_index": 3}]},
            "used_files": [{"file_id": 8}],
            "timings": {"latency_ms": 321},
            "failure": {
                "stage": "citation_validation",
                "message": "validation timeout",
                "code": "VALIDATION_TIMEOUT",
                "retriable": True,
            },
        },
    }
    assert response["accepted"] is True
    assert response["status"] == "accepted"


@pytest.mark.parametrize(
    ("response_json", "call_name"),
    [
        (
            {
                "success": True,
                "conversation_id": 12,
                "trace_id": "trace-1",
                "idempotency_key": "12:trace-1:user",
                "created_at": "2026-03-22T12:34:56Z",
                "deduped": False,
            },
            "write_user_turn",
        ),
        (
            {
                "conversation_id": 12,
                "user_id": 7,
                "updated_at": "2026-03-22T12:35:00Z",
                "summary": {},
                "recent_turns": [],
                "conversation_state": {},
            },
            "read_context_snapshot",
        ),
        (
            {
                "event_id": "assistant-async:12:trace-1",
                "trace_id": "trace-1",
                "idempotency_key": "12:trace-1:assistant",
                "status": "accepted",
            },
            "accept_assistant_turn_async",
        ),
        (
            {
                "event_id": "assistant-async:12:trace-1",
                "trace_id": "trace-1",
                "idempotency_key": "12:trace-1:assistant",
                "status": "accepted",
            },
            "accept_assistant_turn_terminal_async",
        ),
    ],
)
def test_client_rejects_malformed_contract_responses(response_json, call_name):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    client = ConversationAuthorityClient(
        base_url="http://public-service",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError):
        getattr(client, call_name)(
            user_id=7,
            conversation_id=12,
            trace_id="trace-1",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            **(
                {"content": "hello"}
                if call_name == "write_user_turn"
                else {"answer_text": "final answer"}
                if call_name == "accept_assistant_turn_async"
                else {"terminal_status": "done", "answer_text": "final answer"}
                if call_name == "accept_assistant_turn_terminal_async"
                else {}
            ),
        )
