import asyncio

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from pydantic import ValidationError

from server.schemas.authority_models import (
    AuthorityAssistantAsyncRequest,
    AuthorityContextSnapshotQuery,
    AuthorityUserWriteRequest,
)
from server.schemas.request_models import ProtocolMismatchRequestError, parse_patent_request
from server.schemas.response_models import ContentEvent, DoneEvent, MetadataEvent, PatentSyncSuccess



def _base_payload() -> dict:
    return {
        "question": "Explain the patent novelty.",
        "conversation_id": "123",
        "chat_history": [],
        "requested_mode": "patent",
        "actual_mode": "patent",
        "route": "kb_qa",
        "source_scope": None,
        "turn_mode": "kb_only",
        "kb_enabled": False,
        "allow_kb_verification": False,
        "used_files": [],
        "execution_files": [],
        "selected_file_ids": [],
        "primary_file_id": None,
        "file_selection": {},
        "trace_id": "req_123",
        "options": {},
    }



def test_patent_request_rejects_non_kb_only_payload():
    payload = _base_payload()
    payload["turn_mode"] = "file_only"

    with pytest.raises(ProtocolMismatchRequestError, match="turn_mode"):
        parse_patent_request(payload)





def test_patent_request_requires_exact_protocol_literals():
    payload = _base_payload()
    payload["requested_mode"] = "PATENT"

    with pytest.raises(ProtocolMismatchRequestError, match="requested_mode"):
        parse_patent_request(payload)

    payload = _base_payload()
    payload["route"] = " kb_qa "

    with pytest.raises(ProtocolMismatchRequestError, match="route"):
        parse_patent_request(payload)

def test_patent_request_normalizes_conversation_id_and_mode_classification():
    durable_request = parse_patent_request(_base_payload())

    ephemeral_payload = _base_payload()
    ephemeral_payload["conversation_id"] = "opaque-id"
    ephemeral_request = parse_patent_request(ephemeral_payload)

    assert durable_request.conversation_id == 123
    assert durable_request.persistence_mode == "durable"
    assert ephemeral_request.conversation_id is None
    assert ephemeral_request.persistence_mode == "ephemeral"





def test_patent_request_requires_string_question_and_trace_id():
    payload = _base_payload()
    payload["question"] = 123

    with pytest.raises(ValueError, match="question must be a string"):
        parse_patent_request(payload)

    payload = _base_payload()
    payload["trace_id"] = {"bad": "value"}

    with pytest.raises(ValueError, match="trace_id must be a string"):
        parse_patent_request(payload)



def test_schema_models_reject_extra_fields_and_enforce_object_item_shapes():
    with pytest.raises(ValidationError):
        PatentSyncSuccess(
            data={
                "final_answer": "Patent answer",
                "timings": {},
                "metadata": {
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": "kb_qa",
                    "mode": "patent",
                    "query_mode": "patent",
                    "conversation_id": 123,
                    "unexpected": True,
                },
                "references": [],
                "pdf_links": [],
                "reference_links": [],
                "trace_id": "req_123",
            },
            trace_id="req_123",
        )

    with pytest.raises(ValidationError):
        AuthorityAssistantAsyncRequest(
            conversation_id=123,
            user_id=42,
            trace_id="req_123",
            idempotency_key="123:req_123:assistant",
            final_event={
                "done_seen": True,
                "answer_text": "Patent answer",
                "references": ["not-an-object"],
                "used_files": ["not-an-object"],
                "timings": {},
            },
        )

def test_sync_success_shape_matches_patent_contract():
    response = PatentSyncSuccess(
        data={
            "final_answer": "Patent answer",
            "timings": {},
            "metadata": {
                "requested_mode": "patent",
                "actual_mode": "patent",
                "route": "kb_qa",
                "mode": "patent",
                "query_mode": "patent",
                "conversation_id": 123,
            },
            "references": [],
            "pdf_links": [],
            "reference_links": [],
            "trace_id": "req_123",
        },
        trace_id="req_123",
    )

    payload = response.model_dump()
    assert payload["success"] is True
    assert payload["data"]["metadata"]["requested_mode"] == "patent"
    assert payload["data"]["metadata"]["actual_mode"] == "patent"
    assert payload["data"]["metadata"]["route"] == "kb_qa"
    assert payload["data"]["trace_id"] == "req_123"



def test_stream_events_require_seq_and_ts():
    with pytest.raises(ValidationError):
        MetadataEvent(
            requested_mode="patent",
            actual_mode="patent",
            route="kb_qa",
            query_mode="patent",
            trace_id="req_123",
        )

    with pytest.raises(ValidationError):
        DoneEvent(
            final_answer="done",
            timings={},
            references=[],
            trace_id="req_123",
            seq=1,
        )

    event = ContentEvent(content="chunk", seq=2, ts="2026-03-25T12:00:00Z")
    assert event.seq == 2
    assert event.ts == "2026-03-25T12:00:00Z"



def test_authority_models_match_patent_contract():
    user_write = AuthorityUserWriteRequest(
        conversation_id=123,
        user_id=42,
        trace_id="req_123",
        idempotency_key="123:req_123:user",
        message={"role": "user", "content": "Explain the patent novelty."},
        context_hints={"selected_file_ids": [], "last_turn_route_hint": "kb_qa"},
    )
    snapshot_query = AuthorityContextSnapshotQuery(
        user_id=42,
        trace_id="req_123",
    )
    assistant_async = AuthorityAssistantAsyncRequest(
        conversation_id=123,
        user_id=42,
        trace_id="req_123",
        idempotency_key="123:req_123:assistant",
        final_event={
            "done_seen": True,
            "answer_text": "Patent answer",
            "steps": [],
            "references": [],
            "used_files": [],
            "timings": {},
        },
    )

    assert user_write.source_service == "patentQA"
    assert user_write.route == "kb_qa"
    assert snapshot_query.actual_mode == "patent"
    assert assistant_async.final_event.done_seen is True

from dataclasses import replace

from server.errors import codes
from server.errors.core import APIError
from server.schemas.response_models import ErrorEvent
from server.services.ask_service import AskService
from server.patent.executor import PatentExecutor
from server.runtime.request_context import clear_trace_id, set_trace_id
from server_fastapi.app import create_app
from server_fastapi.routers.ask import _build_streaming_response


class _FakePersistenceService:
    def __init__(self, *, fail_accept: bool = False):
        self.fail_accept = fail_accept
        self.calls = []

    def prepare_turn(self, *, request, user_id):
        context = {
            "persistence_mode": request.persistence_mode,
            "conversation_id": request.conversation_id,
            "trace_id": request.trace_id,
            "chat_history": list(request.chat_history),
            "summary": {},
            "conversation_state": {},
            "pending_overlay": None,
            "snapshot": None,
        }
        self.calls.append({"op": "prepare", "trace_id": request.trace_id, "user_id": user_id})
        return {
            "trace_id": request.trace_id,
            "context": context,
            "assistant_accept": None,
            "assistant_accept_required": request.is_durable,
            "assistant_accept_skipped": False,
        }

    def finalize_turn(self, prepared_turn, *, request, execution_result):
        self.calls.append({"op": "finalize", "trace_id": prepared_turn["trace_id"], "user_id": None})
        if self.fail_accept:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message="assistant accept failed",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            )
        return {
            "trace_id": prepared_turn["trace_id"],
            "context": prepared_turn["context"],
            "execution_result": dict(execution_result or {}),
            "assistant_accept": {"accepted": True},
            "assistant_accept_required": request.is_durable,
            "assistant_accept_skipped": False,
        }

    def abort_turn(self, prepared_turn):
        self.calls.append({"op": "abort", "trace_id": prepared_turn.get("trace_id"), "user_id": None})



def test_ask_service_sync_payload_matches_contract():
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    payload = service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    validated = PatentSyncSuccess.model_validate(payload)
    assert validated.data.final_answer == "Patent Phase 1 stub answer: Explain the patent novelty."
    assert validated.data.metadata.requested_mode == "patent"
    assert validated.data.metadata.route == "kb_qa"
    assert validated.trace_id == "req_123"



def test_stream_done_is_emitted_only_after_accept_success():
    request = parse_patent_request(_base_payload())
    success_service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )
    success_events = list(success_service.stream_ask(request, user_id=42))

    assert success_events[0]["type"] == "metadata"
    assert success_events[-1]["type"] == "done"
    assert all(event["seq"] == index for index, event in enumerate(success_events))

    failure_service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_FakePersistenceService(fail_accept=True),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )
    failure_events = list(failure_service.stream_ask(replace(request, trace_id="req_456"), user_id=42))

    assert failure_events[0]["type"] == "metadata"
    assert failure_events[-1]["type"] == "error"
    ErrorEvent.model_validate(failure_events[-1])
    assert all(event["type"] != "done" for event in failure_events)


class _SplitPhasePersistenceService:
    def __init__(
        self,
        *,
        fail_accept: bool = False,
        resolved_trace_id: str | None = None,
        finalize_without_accept: bool = False,
    ):
        self.fail_accept = fail_accept
        self.resolved_trace_id = resolved_trace_id
        self.finalize_without_accept = finalize_without_accept
        self.calls = []
        self.aborted = []

    def prepare_turn(self, *, request, user_id):
        trace_id = self.resolved_trace_id or request.trace_id
        context = {
            "persistence_mode": request.persistence_mode,
            "conversation_id": request.conversation_id,
            "trace_id": trace_id,
            "chat_history": list(request.chat_history),
            "summary": {},
            "conversation_state": {},
            "pending_overlay": None,
            "snapshot": None,
        }
        self.calls.append({"op": "prepare", "trace_id": trace_id, "user_id": user_id})
        return {
            "trace_id": trace_id,
            "context": context,
            "assistant_accept_required": request.is_durable,
            "assistant_accept_skipped": False,
        }

    def finalize_turn(self, prepared_turn, *, request, execution_result):
        trace_id = prepared_turn["trace_id"]
        context = prepared_turn["context"]
        self.calls.append({"op": "finalize", "trace_id": trace_id, "user_id": None})
        if self.fail_accept:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message="assistant accept failed",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            )
        return {
            "trace_id": trace_id,
            "context": context,
            "execution_result": dict(execution_result or {}),
            "assistant_accept": None if self.finalize_without_accept else {"accepted": True},
            "assistant_accept_skipped": False,
            "assistant_accept_required": request.is_durable,
        }

    def abort_turn(self, prepared_turn):
        self.aborted.append(dict(prepared_turn or {}))


def test_stream_emits_progress_before_accept_failure_when_split_phase_is_available():
    request = parse_patent_request(_base_payload())
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(fail_accept=True),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(request, user_id=42))

    assert [event["type"] for event in events] == ["metadata", "step", "content", "error"]
    assert events[1]["title"] == "Patent Stub"
    assert events[2]["content"] == "Patent Phase 1 stub answer: Explain the patent novelty."
    assert all(event["type"] != "done" for event in events)


def test_stream_uses_resolved_trace_id_before_first_frame():
    request = parse_patent_request(_base_payload())
    request = replace(request, trace_id="")
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(resolved_trace_id="req_generated"),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(request, user_id=42))

    assert events[0]["trace_id"] == "req_generated"
    assert events[-1]["trace_id"] == "req_generated"


def test_stream_refuses_done_when_assistant_accept_signal_is_missing():
    request = parse_patent_request(_base_payload())
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(finalize_without_accept=True),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(request, user_id=42))

    assert events[-1]["type"] == "error"
    assert all(event["type"] != "done" for event in events)


def test_sync_ask_maps_result_builder_validation_errors_to_api_error():
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "query_mode": "patent",
                "references": ["bad-reference"],
                "timings": {},
            }

    service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR


def test_stream_maps_result_builder_validation_errors_to_terminal_error():
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "query_mode": "patent",
                "references": ["bad-reference"],
                "timings": {},
            }

    service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events[0]["type"] == "metadata"
    assert events[-1]["type"] == "error"
    assert all(event["type"] != "done" for event in events)



def test_stream_maps_metadata_builder_failures_to_terminal_error():
    request = parse_patent_request(_base_payload())
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: (_ for _ in ()).throw(RuntimeError("clock boom")),
    )

    events = list(service.stream_ask(request, user_id=42))

    assert events == [
        {
            "type": "error",
            "code": codes.INTERNAL_ERROR,
            "error": "internal_error",
            "message": "internal server error",
            "trace_id": "req_123",
            "seq": 0,
            "ts": "1970-01-01T00:00:00Z",
        }
    ]



def test_stream_maps_prepare_time_failures_to_terminal_error():
    class _PrepareFailurePersistence:
        def prepare_turn(self, *, request, user_id):
            raise APIError(
                code=codes.PATENT_BUSY,
                message="durable patent turn is already in flight",
                status_code=409,
                error="patent_busy",
                retriable=True,
            )

        def abort_turn(self, prepared_turn):
            return None

    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_PrepareFailurePersistence(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events == [
        {
            "type": "error",
            "code": codes.PATENT_BUSY,
            "error": "patent_busy",
            "message": "durable patent turn is already in flight",
            "trace_id": "req_123",
            "seq": 0,
            "ts": "2026-03-26T00:00:00Z",
        }
    ]



TEST_JWT_SECRET = "patent-test-secret"


def _make_auth_token(user_id: int, *, secret: str = TEST_JWT_SECRET) -> str:
    serializer = URLSafeTimedSerializer(secret)
    return serializer.dumps({"user_id": user_id, "role": "user"}, salt="highthinking.auth.access")


def _stream_events(response) -> list[dict]:
    payloads = []
    for chunk in response.text.strip().split("\n\n"):
        item = chunk.strip()
        if not item:
            continue
        assert item.startswith("data: ")
        payloads.append(__import__("json").loads(item[6:]))
    return payloads



class _RouteFakeAskService:
    def __init__(self):
        self.sync_calls = []
        self.stream_calls = []

    def sync_ask(self, request, *, user_id):
        self.sync_calls.append({"trace_id": request.trace_id, "user_id": user_id})
        return {
            "success": True,
            "data": {
                "final_answer": "route stub",
                "timings": {},
                "metadata": {
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": "kb_qa",
                    "mode": "patent",
                    "query_mode": "patent",
                    "conversation_id": request.conversation_id,
                },
                "references": [],
                "pdf_links": [],
                "reference_links": [],
                "trace_id": request.trace_id,
            },
            "trace_id": request.trace_id,
        }

    def stream_ask(self, request, *, user_id):
        self.stream_calls.append({"trace_id": request.trace_id, "user_id": user_id})
        return iter(
            [
                {
                    "type": "metadata",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": "kb_qa",
                    "query_mode": "patent",
                    "trace_id": request.trace_id,
                    "seq": 0,
                    "ts": "2026-03-26T00:00:00Z",
                },
                {
                    "type": "done",
                    "final_answer": "route stub",
                    "timings": {},
                    "references": [],
                    "trace_id": request.trace_id,
                    "used_files": [],
                    "reference_links": [],
                    "pdf_links": [],
                    "file_selection": {},
                    "seq": 1,
                    "ts": "2026-03-26T00:00:00Z",
                },
            ]
        )


class _RaisingStreamAskService:
    def __init__(self, exc, *, emit_metadata_first: bool = False, metadata_trace_id: str | None = None):
        self.exc = exc
        self.emit_metadata_first = emit_metadata_first
        self.metadata_trace_id = metadata_trace_id

    def sync_ask(self, request, *, user_id):
        raise self.exc

    def stream_ask(self, request, *, user_id):
        if self.emit_metadata_first:
            def _generator():
                yield {
                    "type": "metadata",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": "kb_qa",
                    "query_mode": "patent",
                    "trace_id": self.metadata_trace_id or request.trace_id,
                    "seq": 0,
                    "ts": "2026-03-26T00:00:00Z",
                }
                raise self.exc
            return _generator()
        raise self.exc


class _ResolvedTraceStreamAskService:
    def sync_ask(self, request, *, user_id):
        raise NotImplementedError

    def stream_ask(self, request, *, user_id):
        return iter(
            [
                {
                    "type": "metadata",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": "kb_qa",
                    "query_mode": "patent",
                    "trace_id": "req_resolved",
                    "seq": 0,
                    "ts": "2026-03-26T00:00:00Z",
                },
                {
                    "type": "content",
                    "content": "route stub",
                    "seq": 1,
                    "ts": "2026-03-26T00:00:00Z",
                },
                {
                    "type": "done",
                    "final_answer": "route stub",
                    "timings": {},
                    "references": [],
                    "used_files": [],
                    "reference_links": [],
                    "pdf_links": [],
                    "file_selection": {},
                    "seq": 2,
                    "ts": "2026-03-26T00:00:00Z",
                },
            ]
        )



def test_patent_route_aliases_all_dispatch_to_patent_ask():
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    payload = _base_payload()
    payload["conversation_id"] = "opaque-ephemeral"

    sync_paths = ["/api/ask", "/api/v1/ask", "/api/patent/ask", "/api/v1/patent/ask"]
    stream_paths = ["/api/ask_stream", "/api/v1/ask_stream", "/api/patent/ask_stream", "/api/v1/patent/ask_stream"]

    with TestClient(app) as client:
        for route in sync_paths:
            response = client.post(route, json=payload)
            assert response.status_code == 200
            assert response.json()["data"]["final_answer"] == "route stub"
        for route in stream_paths:
            response = client.post(route, json=payload)
            assert response.status_code == 200
            events = _stream_events(response)
            assert events[0]["type"] == "metadata"
            assert events[-1]["type"] == "done"

    assert len(fake.sync_calls) == 4
    assert len(fake.stream_calls) == 4
    assert all(call["user_id"] is None for call in fake.sync_calls + fake.stream_calls)


def test_ephemeral_sync_ask_returns_success_without_authority_calls():
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = "opaque-ephemeral"
    payload["kb_enabled"] = True

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["final_answer"] == "Patent Phase 1 stub answer: Explain the patent novelty."


def test_durable_stream_busy_conversation_returns_busy_error(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    app.state.ask_service = _RaisingStreamAskService(
        APIError(
            code=codes.PATENT_BUSY,
            message="durable patent turn is already in flight",
            status_code=409,
            error="patent_busy",
            retriable=True,
        )
    )
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == codes.PATENT_BUSY
    assert all(event["type"] != "done" for event in events)


def test_durable_sync_request_is_blocked_by_route_gate_before_auth_or_service(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("PATENT_DURABLE_MODE_ENABLED", raising=False)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask", json=_base_payload())

    assert response.status_code == 503
    assert response.json()["code"] == codes.DURABLE_MODE_DISABLED
    assert fake.sync_calls == []


def test_durable_stream_request_is_blocked_by_route_gate_before_auth_or_service(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("PATENT_DURABLE_MODE_ENABLED", raising=False)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=_base_payload())

    assert response.status_code == 503
    assert response.json()["code"] == codes.DURABLE_MODE_DISABLED
    assert fake.stream_calls == []


def test_durable_request_requires_auth_after_rollout_gate_is_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask", json=_base_payload())

    assert response.status_code == 401
    assert response.json()["code"] == codes.TOKEN_MISSING
    assert fake.sync_calls == []


def test_durable_sync_request_blocks_when_dependencies_are_not_ready(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY
    assert fake.sync_calls == []


def test_durable_stream_request_blocks_when_dependencies_are_not_ready(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY
    assert fake.stream_calls == []


def test_durable_stream_requires_auth_before_dependency_readiness(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=_base_payload())

    assert response.status_code == 401
    assert response.json()["code"] == codes.TOKEN_MISSING
    assert fake.stream_calls == []


def test_durable_request_blocks_when_runtime_dispatcher_degrades_after_start(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    runtime_state = dict(app.state.runtime_dispatcher.runtime_state())
    app.state.runtime_dispatcher.runtime_state = lambda: {**runtime_state, "ready": False}
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY
    assert fake.sync_calls == []


def test_stream_renewal_failure_emits_terminal_error_not_done(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    app.state.ask_service = _RaisingStreamAskService(
        APIError(
            code=codes.SERVICE_NOT_READY,
            message="durable patent runtime guard renewal failed",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        ),
        emit_metadata_first=True,
    )
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[0]["type"] == "metadata"
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == codes.SERVICE_NOT_READY
    assert all(event["type"] != "done" for event in events)


def test_stream_terminal_error_uses_latest_resolved_trace_id(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    app.state.ask_service = _RaisingStreamAskService(
        APIError(
            code=codes.SERVICE_NOT_READY,
            message="durable patent runtime guard renewal failed",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        ),
        emit_metadata_first=True,
        metadata_trace_id="req_resolved",
    )
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    events = _stream_events(response)
    assert events[0]["trace_id"] == "req_resolved"
    assert events[-1]["type"] == "error"
    assert events[-1]["trace_id"] == "req_resolved"


def test_stream_terminal_error_uses_middleware_trace_before_first_frame():
    service = _RaisingStreamAskService(
        APIError(
            code=codes.SERVICE_NOT_READY,
            message="durable patent runtime guard renewal failed",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        )
    )
    request = type(
        "_Request",
        (),
        {"app": type("_App", (), {"state": type("_State", (), {"ask_service": service, "runtime_dispatcher": None})()})()},
    )()
    ask_request = type("_AskRequest", (), {"trace_id": ""})()
    token = set_trace_id("req_generated")
    try:
        response = _build_streaming_response(request=request, ask_request=ask_request, user_id=42)

        async def _collect_body() -> str:
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
            return "".join(chunks)

        payloads = []
        for chunk in asyncio.run(_collect_body()).strip().split("\n\n"):
            item = chunk.strip()
            if not item:
                continue
            assert item.startswith("data: ")
            payloads.append(__import__("json").loads(item[6:]))
    finally:
        clear_trace_id(token)

    assert payloads[-1]["type"] == "error"
    assert payloads[-1]["trace_id"] == "req_generated"


def test_success_stream_carries_resolved_trace_id_to_later_frames():
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = "opaque-ephemeral"
    app.state.ask_service = _ResolvedTraceStreamAskService()

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    events = _stream_events(response)
    assert [event["trace_id"] for event in events] == ["req_resolved", "req_resolved", "req_resolved"]
    assert events[-1]["type"] == "done"


def test_ephemeral_request_still_runs_when_durable_redis_path_is_unavailable(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "false")
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = "opaque-ephemeral"
    payload["kb_enabled"] = True

    with TestClient(app) as client:
        response = client.post("/api/patent/ask", json=payload)

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["final_answer"] == "Patent Phase 1 stub answer: Explain the patent novelty."
