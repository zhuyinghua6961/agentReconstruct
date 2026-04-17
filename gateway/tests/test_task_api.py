import json
import threading
import time
from dataclasses import replace
import logging

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.auth import AuthContext, require_auth_context
from app.core.config import GatewaySettings
from app.integrations.redis.service import RedisService
from app.main import app
from app.models.files import ConversationFileRow
from app.services import execution_queue_status as queue_status_module
from app.services import qa_tasks as qa_task_module
from app.services.execution_admission import ExecutionAdmissionDispatcher, ExecutionAdmissionWorker
from app.services.execution_event_relay import ExecutionEventRelayStore
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


_TASK_AUTH_STATE = {"user_id": 42, "role": "user", "username": "user42"}


def _set_current_task_user(user_id: int, *, role: str = "user", username: str | None = None) -> None:
    _TASK_AUTH_STATE["user_id"] = int(user_id)
    _TASK_AUTH_STATE["role"] = str(role or "user")
    _TASK_AUTH_STATE["username"] = str(username or f"user{int(user_id)}")


@pytest.fixture(autouse=True)
def _fresh_task_state():
    previous_settings = app.state.settings
    previous_queue = app.state.execution_queue_status_store
    previous_relay = app.state.execution_event_relay_store
    previous_slot_leases = app.state.execution_slot_lease_store
    previous_conversation_persistence = app.state.conversation_persistence_service
    previous_quota_proxy = app.state.quota_proxy_service
    app.state.settings = replace(app.state.settings, refresh_survivable_qa_tasks_enabled=True)
    app.state.execution_queue_status_store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
    )
    app.state.execution_event_relay_store = ExecutionEventRelayStore(
        redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
    )
    app.state.execution_slot_lease_store = ExecutionSlotLeaseStore(
        redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
    )
    app.state.proxy_service.set_transport(None)
    app.state.conversation_persistence_service.set_transport(None)
    app.state.quota_proxy_service.set_transport(None)
    app.state.gateway_auth_service.set_transport(None)
    _set_current_task_user(42)

    async def _fake_auth_context() -> AuthContext:
        return AuthContext(
            user_id=int(_TASK_AUTH_STATE["user_id"]),
            role=str(_TASK_AUTH_STATE["role"]),
            username=str(_TASK_AUTH_STATE["username"]),
        )

    app.dependency_overrides[require_auth_context] = _fake_auth_context
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_auth_context, None)
        app.state.settings = previous_settings
        app.state.execution_queue_status_store = previous_queue
        app.state.execution_event_relay_store = previous_relay
        app.state.execution_slot_lease_store = previous_slot_leases
        app.state.conversation_persistence_service = previous_conversation_persistence
        app.state.quota_proxy_service = previous_quota_proxy
        app.state.proxy_service.set_transport(None)
        app.state.conversation_persistence_service.set_transport(None)
        app.state.quota_proxy_service.set_transport(None)
        app.state.gateway_auth_service.set_transport(None)


def _set_health_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "path": path})
        if path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-health-default", "quota_type": payload.get("quota_type") or "ask_query", "noop": False}},
            )
        if path.startswith("/internal/quota/grants/") and path.endswith("/finalize"):
            return httpx.Response(200, json={"success": True, "data": {"grant_id": path.split("/")[-2], "counted": bool(payload.get("success")), "idempotent": False}})
        if path.endswith("/create-turn"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": int(payload.get("conversation_id") or 0),
                    "task_id": str(payload.get("task_id") or ""),
                    "user_message_id": "m_user_default",
                    "assistant_message_id": "m_assistant_default",
                    "status": "queued",
                    "deduped": False,
                },
            )
        if path.endswith("/rollback-create"):
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"status": "ok", "path": path})

    transport = httpx.MockTransport(handler)
    app.state.proxy_service.set_transport(transport)
    app.state.conversation_persistence_service.set_transport(transport)
    app.state.quota_proxy_service.set_transport(transport)


def _set_task_transport(handler) -> None:
    def _wrapped(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        try:
            return handler(request)
        except AssertionError:
            if path == "/internal/quota/grants/precheck":
                return httpx.Response(
                    200,
                    json={"success": True, "data": {"grant_id": "grant-task-default", "quota_type": payload.get("quota_type") or "ask_query", "noop": False}},
                )
            if path.startswith("/internal/quota/grants/") and path.endswith("/finalize"):
                return httpx.Response(200, json={"success": True, "data": {"grant_id": path.split("/")[-2], "counted": bool(payload.get("success")), "idempotent": False}})
            raise

    transport = httpx.MockTransport(_wrapped)
    app.state.proxy_service.set_transport(transport)
    app.state.conversation_persistence_service.set_transport(transport)
    app.state.quota_proxy_service.set_transport(transport)


def _json_request_body(request: httpx.Request) -> dict:
    try:
        raw = request.content.decode("utf-8") if request.content else ""
    except Exception:
        return {}
    return json.loads(raw) if raw else {}


def _sse_payloads(body: bytes) -> list[dict]:
    payloads: list[dict] = []
    for frame in body.decode("utf-8").split("\n\n"):
        if not frame.strip():
            continue
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            payloads.append(json.loads(line[5:].strip()))
            break
    return payloads


def _request_body(**overrides):
    payload = {
        "conversation_id": 123,
        "question": "What is the current status?",
        "requested_mode": "fast",
        "user_id": 42,
        "chat_history": [],
        "pdf_context": {},
        "options": {},
    }
    payload.update(overrides)
    return payload


def _queued_task_record(**overrides):
    record = {
        "request_id": "req_task_default",
        "status": "queued",
        "conversation_id": 123,
        "assistant_message_id": "msg_task_default",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "target_backend": "fast",
        "route": "kb_qa",
        "queue_tier": "high",
        "created_at": "2026-04-06T10:00:00+00:00",
        "updated_at": "2026-04-06T10:00:00+00:00",
        "enqueued_at": "2026-04-06T10:00:00+00:00",
        "expires_at": "2026-04-06T10:15:00+00:00",
        "cancel_allowed": True,
        "user_id": 42,
        "quota_type": "ask_query",
        "quota_grant_id": "grant-task-default",
        "execution_snapshot": {
            "question": "What is the current status?",
            "conversation_id": 123,
            "user_id": 42,
            "chat_history": [],
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "trace_id": "req_task_default",
            "options": {},
        },
    }
    record.update(overrides)
    return record


class _BlockingAsyncStream(httpx.AsyncByteStream):
    def __init__(self, *, first_chunk: bytes, second_chunk: bytes, first_released: threading.Event, continue_event: threading.Event) -> None:
        self._first_chunk = first_chunk
        self._second_chunk = second_chunk
        self._first_released = first_released
        self._continue_event = continue_event

    async def __aiter__(self):
        yield self._first_chunk
        self._first_released.set()
        self._continue_event.wait(timeout=5)
        yield self._second_chunk

    async def aclose(self) -> None:
        return None


class _AbortAwareBlockingAsyncStream(httpx.AsyncByteStream):
    def __init__(self, *, first_chunk: bytes, second_chunk: bytes, first_released: threading.Event, continue_event: threading.Event, closed_event: threading.Event) -> None:
        self._first_chunk = first_chunk
        self._second_chunk = second_chunk
        self._first_released = first_released
        self._continue_event = continue_event
        self._closed_event = closed_event

    async def __aiter__(self):
        yield self._first_chunk
        self._first_released.set()
        while not self._continue_event.wait(timeout=0.05):
            if self._closed_event.is_set():
                return
        if self._closed_event.is_set():
            return
        yield self._second_chunk

    async def aclose(self) -> None:
        self._closed_event.set()


class _AsyncPauseStream(httpx.AsyncByteStream):
    def __init__(self, *, first_chunk: bytes, second_chunk: bytes, first_released: threading.Event, continue_event: threading.Event) -> None:
        self._first_chunk = first_chunk
        self._second_chunk = second_chunk
        self._first_released = first_released
        self._continue_event = continue_event

    async def __aiter__(self):
        yield self._first_chunk
        self._first_released.set()
        while not self._continue_event.is_set():
            await anyio.sleep(0.01)
        yield self._second_chunk

    async def aclose(self) -> None:
        return None


def test_create_task_returns_gateway_managed_summary_and_persists_request_record():
    _set_health_transport()
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"]
    assert payload["task_id"] == payload["request_id"]
    assert payload["conversation_id"] == 123
    assert payload["status"] == "queued"
    assert payload["requested_mode"] == "fast"
    assert payload["actual_mode"] == "fast"
    assert payload["route"] == "kb_qa"
    assert payload["queue_tier"] == "high"
    assert payload["last_seq"] == 1
    assert payload["events_url"].endswith(f"/api/v1/tasks/{payload['task_id']}/events")
    assert payload["cancel_url"].endswith(f"/api/v1/tasks/{payload['task_id']}/cancel")

    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert stored["request_id"] == payload["task_id"]
    assert stored["status"] == "queued"
    assert stored["enqueued_at"] == stored["created_at"]
    assert stored["target_backend"] == "fast"
    assert stored["transport_kind"] == "sse"


def test_create_task_summary_exposes_accept_timestamp_telemetry():
    _set_health_transport()
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["telemetry"]["accepted_at_ms"], int)
    assert payload["telemetry"]["accepted_at_ms"] > 0

    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert stored["telemetry"]["accepted_at_ms"] == payload["telemetry"]["accepted_at_ms"]


def test_create_task_logs_task_correlation_id(caplog):
    _set_health_transport()
    client = TestClient(app)

    with caplog.at_level(logging.INFO, logger="app.services.qa_tasks"):
        response = client.post("/api/v1/tasks", json=_request_body(client_request_id="client_req_001"))

    assert response.status_code == 200
    payload = response.json()
    assert any(
        "gateway task queued" in record.getMessage()
        and f"task_id={payload['task_id']}" in record.getMessage()
        and "client_request_id=client_req_001" in record.getMessage()
        for record in caplog.records
    )


def test_create_task_logs_gateway_task_create_milestones(caplog):
    _set_health_transport()
    client = TestClient(app)

    with caplog.at_level(logging.INFO):
        response = client.post("/api/v1/tasks", json=_request_body(client_request_id="client_req_002"))

    assert response.status_code == 200
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "gateway task create accepted" in text
    assert "gateway task create quota precheck completed" in text
    assert "gateway task create authority turn persisted" in text
    assert "gateway task queued" in text


def test_create_task_rejects_client_request_id_with_control_chars():
    _set_health_transport()
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(client_request_id="client_req_\n001"))

    assert response.status_code == 422


def test_task_events_preserve_gateway_relay_sequence_instead_of_downstream_payload_seq():
    _set_health_transport()
    client = TestClient(app)
    record = _queued_task_record(request_id="req_seq_authority", assistant_message_id="msg_seq_authority")
    app.state.execution_queue_status_store.put_request(record, ttl_seconds=600)
    app.state.execution_event_relay_store.append_frame(
        "req_seq_authority",
        {"type": "content", "seq": 7, "content": "downstream-delta"},
        ttl_seconds=600,
    )

    response = client.get("/api/v1/tasks/req_seq_authority/events?after_seq=0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["events"][0]["seq"] == 1
    assert payload["events"][0]["content"] == "downstream-delta"


def test_create_task_is_gated_when_refresh_survivable_tasks_flag_is_disabled():
    _set_health_transport()
    app.state.settings = replace(app.state.settings, refresh_survivable_qa_tasks_enabled=False)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 404
    assert response.json()["detail"] == "task_api_disabled"


def test_task_api_requires_authorization_when_real_auth_dependency_is_used():
    app.dependency_overrides.pop(require_auth_context, None)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 401
    assert response.json()["detail"] == "token_missing"


def test_create_task_binds_user_id_from_authenticated_context():
    _set_health_transport()
    _set_current_task_user(84)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(user_id=None))

    assert response.status_code == 200
    payload = response.json()
    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert stored["user_id"] == 84
    assert stored["execution_snapshot"]["user_id"] == 84


def test_create_task_rejects_body_user_id_that_does_not_match_authenticated_user():
    _set_health_transport()
    _set_current_task_user(42)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(user_id=7))

    assert response.status_code == 400
    assert response.json()["detail"] == "task_user_id_mismatch"


def test_concurrent_create_task_same_conversation_creates_only_one_real_task():
    _set_health_transport()
    original_assert = qa_task_module.QATaskService._assert_task_create_admission
    barrier = threading.Barrier(2)
    clients = [TestClient(app), TestClient(app)]
    responses: list[object] = []

    def delayed_assert(self, payload):
        original_assert(self, payload)
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass

    qa_task_module.QATaskService._assert_task_create_admission = delayed_assert
    try:
        def run_create(client: TestClient):
            response = client.post("/api/v1/tasks", json=_request_body(conversation_id=123, user_id=42))
            responses.append(response)

        thread_1 = threading.Thread(target=run_create, args=(clients[0],), daemon=True)
        thread_2 = threading.Thread(target=run_create, args=(clients[1],), daemon=True)
        thread_1.start()
        thread_2.start()
        thread_1.join(timeout=5)
        thread_2.join(timeout=5)
    finally:
        qa_task_module.QATaskService._assert_task_create_admission = original_assert

    assert not thread_1.is_alive()
    assert not thread_2.is_alive()
    status_codes = sorted(response.status_code for response in responses)
    assert status_codes == [200, 409]
    queued_records = app.state.execution_queue_status_store.list_requests(status="queued")
    matching_records = [record for record in queued_records if int(record.get("conversation_id") or 0) == 123]
    assert len(matching_records) == 1


def test_create_task_real_auth_dependency_resolves_user_via_public_auth_me():
    _set_health_transport()
    app.dependency_overrides.pop(require_auth_context, None)

    def auth_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/me"
        assert request.headers["authorization"] == "Bearer live-demo"
        return httpx.Response(
            200,
            json={"success": True, "data": {"id": 99, "username": "live", "role": "user"}},
        )

    app.state.gateway_auth_service.set_transport(httpx.MockTransport(auth_handler))
    client = TestClient(app)

    response = client.post(
        "/api/v1/tasks",
        headers={"Authorization": "Bearer live-demo"},
        json=_request_body(user_id=None),
    )

    assert response.status_code == 200
    payload = response.json()
    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert stored["user_id"] == 99


def test_existing_task_reads_and_cancel_remain_available_when_create_flag_is_disabled():
    app.state.settings = replace(app.state.settings, refresh_survivable_qa_tasks_enabled=False)
    _set_task_transport(
        lambda request: httpx.Response(
            200,
            json={"success": True, "status": "canceled" if request.url.path.endswith("/assistant-terminal") else {"grant_id": "grant-task-default"}},
        )
        if request.url.path.endswith("/assistant-terminal")
        else httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-task-default", "counted": False, "idempotent": False}})
    )
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        _queued_task_record(request_id="req_flag_readback", conversation_id=321),
        ttl_seconds=900,
    )
    app.state.execution_event_relay_store.append_frame(
        "req_flag_readback",
        {"type": "state", "status": "queued"},
        ttl_seconds=900,
    )
    client = TestClient(app)

    detail_response = client.get("/api/v1/tasks/req_flag_readback")
    cancel_response = client.post("/api/v1/tasks/req_flag_readback/cancel")

    assert detail_response.status_code == 200
    assert detail_response.json()["task_id"] == "req_flag_readback"
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "canceled"


def test_task_detail_and_cancel_hide_records_owned_by_another_user():
    _set_health_transport()
    app.state.execution_queue_status_store.put_request(
        _queued_task_record(request_id="req_other_user", user_id=7),
        ttl_seconds=900,
    )
    client = TestClient(app)

    detail_response = client.get("/api/v1/tasks/req_other_user")
    cancel_response = client.post("/api/v1/tasks/req_other_user/cancel")

    assert detail_response.status_code == 404
    assert detail_response.json()["detail"] == "task_not_found"
    assert cancel_response.status_code == 404
    assert cancel_response.json()["detail"] == "task_not_found"


def test_create_task_persists_downstream_authorization_for_worker_execution():
    _set_health_transport()
    client = TestClient(app)

    response = client.post(
        "/api/v1/tasks",
        headers={"Authorization": "Bearer task-worker-token"},
        json=_request_body(requested_mode="thinking"),
    )

    assert response.status_code == 200
    payload = response.json()
    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert stored["downstream_authorization"] == "Bearer task-worker-token"
    assert stored["execution_snapshot"]["downstream_authorization"] == "Bearer task-worker-token"


def test_create_task_prechecks_quota_stores_grant_and_emits_initial_queued_state_event(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-task-create-1", "quota_type": payload["quota_type"], "noop": False}},
            )
        if path.endswith("/create-turn"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": str(payload.get("task_id") or ""),
                    "user_message_id": "m_user_task_create",
                    "assistant_message_id": "m_assistant_task_create",
                    "status": "queued",
                    "deduped": False,
                },
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 200
    payload = response.json()
    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert [path for path, _ in calls] == [
        "/api/health",
        "/internal/quota/grants/precheck",
        f"/internal/conversations/123/tasks/{payload['task_id']}/create-turn",
    ]
    assert calls[1][1]["quota_type"] == "ask_query"
    assert stored["quota_type"] == "ask_query"
    assert stored["quota_grant_id"] == "grant-task-create-1"
    queued_events = client.get(f"/api/v1/tasks/{payload['task_id']}/events", params={"after_seq": 0}).json()["events"]
    assert [event["seq"] for event in queued_events] == [1]
    assert queued_events[0]["type"] == "state"
    assert queued_events[0]["status"] == "queued"
    assert client.get(f"/api/v1/tasks/{payload['task_id']}").json()["last_seq"] == 1


def test_create_task_allows_patent_mode_and_persists_patent_backend_target():
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "grant_id": "grant-task-patent-create",
                        "quota_type": payload.get("quota_type") or "ask_query",
                        "noop": False,
                    },
                },
            )
        if path.endswith("/create-turn"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": str(payload.get("task_id") or ""),
                    "user_message_id": "m_user_patent",
                    "assistant_message_id": "m_assistant_patent",
                    "status": "queued",
                    "deduped": False,
                },
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(requested_mode="patent"))

    assert response.status_code == 200
    payload = response.json()
    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert stored["requested_mode"] == "patent"
    assert stored["actual_mode"] == "patent"
    assert stored["target_backend"] == "patent"
    assert stored["quota_grant_id"] == "grant-task-patent-create"
    assert [path for path, _ in calls] == [
        "/api/health",
        "/internal/quota/grants/precheck",
        f"/internal/conversations/123/tasks/{payload['task_id']}/create-turn",
    ]


def test_create_task_rejects_patent_file_route_when_patent_file_routes_are_disabled():
    async def _list_files(*, conversation_id, request=None):
        _ = conversation_id, request
        return [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]

    original_settings = app.state.settings
    original_list_files = app.state.conversation_file_service.list_files
    app.state.settings = replace(app.state.settings, patent_file_routes_enabled=False)
    app.state.conversation_file_service.list_files = _list_files
    _set_health_transport()
    client = TestClient(app)

    try:
        response = client.post(
            "/api/v1/tasks",
            json=_request_body(
                requested_mode="patent",
                question="请总结这篇文献",
                pdf_context={"selected_ids": [11]},
            ),
        )
    finally:
        app.state.settings = original_settings
        app.state.conversation_file_service.list_files = original_list_files

    assert response.status_code == 503
    assert response.json()["detail"] == "patent_file_route_disabled"
    assert app.state.execution_queue_status_store.list_requests() == []


def test_create_task_persists_patent_file_route_protocol_fields_for_worker_execution():
    async def _list_files(*, conversation_id, request=None):
        _ = conversation_id, request
        return [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]

    original_list_files = app.state.conversation_file_service.list_files
    app.state.conversation_file_service.list_files = _list_files
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "grant_id": "grant-task-patent-mixed",
                        "quota_type": payload.get("quota_type") or "file_qa",
                        "noop": False,
                    },
                },
            )
        if path.endswith("/create-turn"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": str(payload.get("task_id") or ""),
                    "user_message_id": "m_user_patent_mixed",
                    "assistant_message_id": "m_assistant_patent_mixed",
                    "status": "queued",
                    "deduped": False,
                },
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/v1/tasks",
            json=_request_body(
                requested_mode="patent",
                question="请结合知识库总结这篇文献",
                pdf_context={"selected_ids": [11]},
            ),
        )
    finally:
        app.state.conversation_file_service.list_files = original_list_files

    assert response.status_code == 200
    payload = response.json()
    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    snapshot = stored["execution_snapshot"]
    assert snapshot["requested_mode"] == "patent"
    assert snapshot["actual_mode"] == "patent"
    assert snapshot["route"] == "hybrid_qa"
    assert snapshot["turn_mode"] == "mixed"
    assert snapshot["source_scope"] == "pdf+kb"
    assert snapshot["kb_enabled"] is True
    assert snapshot["allow_kb_verification"] is True
    assert snapshot["selected_file_ids"] == [11]
    assert snapshot["execution_files"][0]["file_id"] == 11
    assert snapshot["execution_files"][0]["file_type"] == "pdf"
    assert snapshot["execution_files"][0]["file_name"] == "battery-paper.pdf"
    assert snapshot["file_selection"]["turn_mode"] == "mixed"


def test_admission_worker_sends_patent_protocol_fields_to_upstream_stream(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_patent_protocol",
            requested_mode="patent",
            actual_mode="patent",
            target_backend="patent",
            route="hybrid_qa",
            turn_mode="mixed",
            source_scope="pdf+kb",
            kb_enabled=True,
            allow_kb_verification=True,
            selected_file_ids=[11],
            execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"}],
            assistant_message_id="msg_worker_patent_protocol",
            quota_grant_id="grant-worker-patent-protocol",
            execution_snapshot={
                "question": "请结合知识库总结这篇文献",
                "conversation_id": 123,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "patent",
                "actual_mode": "patent",
                "route": "hybrid_qa",
                "source_scope": "pdf+kb",
                "turn_mode": "mixed",
                "kb_enabled": True,
                "allow_kb_verification": True,
                "used_files": [{"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"}],
                "execution_files": [{"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"}],
                "selected_file_ids": [11],
                "strategy": "explicit_selection",
                "primary_file_id": 11,
                "file_selection": {
                    "strategy": "explicit_selection",
                    "selected_file_ids": [11],
                    "turn_mode": "mixed",
                    "source_scope": "pdf+kb",
                    "kb_enabled": True,
                },
                "route_reasons": ["EXPLICIT_SELECTED_FILES", "EXPLICIT_MIXED_INTENT"],
                "route_confidence": 1.0,
                "classifier_used": False,
                "trace_id": "req_worker_patent_protocol",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_patent_protocol", {"type": "state", "status": "queued"}, ttl_seconds=900)
    captured_payload = {}

    class _Handle:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def body_iter(self):
            yield (
                b'data: {"type":"metadata","query_mode":"patent","route":"hybrid_qa","trace_id":"req_worker_patent_protocol"}\n\n'
                b'data: {"type":"done","final_answer":"ok","query_mode":"patent","route":"hybrid_qa","trace_id":"req_worker_patent_protocol"}\n\n'
            )

        async def abort(self):
            return None

    async def _fake_open_json_stream(*, request, target, path, payload):
        _ = request, target, path
        captured_payload.update(payload)
        return _Handle()

    original_open_json_stream = app.state.proxy_service.open_json_stream
    app.state.proxy_service.open_json_stream = _fake_open_json_stream

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/internal/conversations/123/tasks/req_worker_patent_protocol/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/123/tasks/req_worker_patent_protocol/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-patent-protocol/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-patent-protocol", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-patent-protocol",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:09+00:00",
    )

    try:
        result = worker.run_dispatch_cycle()
    finally:
        app.state.proxy_service.open_json_stream = original_open_json_stream

    assert result.outcome == "completed"
    assert captured_payload["requested_mode"] == "patent"
    assert captured_payload["actual_mode"] == "patent"
    assert captured_payload["route"] == "hybrid_qa"
    assert captured_payload["turn_mode"] == "mixed"
    assert captured_payload["source_scope"] == "pdf+kb"
    assert captured_payload["kb_enabled"] is True
    assert captured_payload["allow_kb_verification"] is True
    assert captured_payload["selected_file_ids"] == [11]
    assert captured_payload["execution_files"] == [{"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"}]


def test_admission_worker_sends_patent_stream_capability_header_for_file_route_tasks(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_patent_capability",
            requested_mode="patent",
            actual_mode="patent",
            target_backend="patent",
            route="hybrid_qa",
            turn_mode="file_only",
            source_scope="pdf+table",
            kb_enabled=False,
            allow_kb_verification=False,
            selected_file_ids=[11, 33],
            execution_files=[
                {"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"},
                {"file_id": 33, "file_type": "excel", "file_name": "claims.xlsx"},
            ],
            assistant_message_id="msg_worker_patent_capability",
            quota_grant_id="grant-worker-patent-capability",
            execution_snapshot={
                "question": "请对比两个文件",
                "conversation_id": 123,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "patent",
                "actual_mode": "patent",
                "route": "hybrid_qa",
                "source_scope": "pdf+table",
                "turn_mode": "file_only",
                "kb_enabled": False,
                "allow_kb_verification": False,
                "used_files": [
                    {"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"},
                    {"file_id": 33, "file_type": "excel", "file_name": "claims.xlsx"},
                ],
                "execution_files": [
                    {"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"},
                    {"file_id": 33, "file_type": "excel", "file_name": "claims.xlsx"},
                ],
                "selected_file_ids": [11, 33],
                "primary_file_id": 11,
                "file_selection": {
                    "selected_file_ids": [11, 33],
                    "primary_file_id": 11,
                    "turn_mode": "file_only",
                    "source_scope": "pdf+table",
                },
                "trace_id": "req_worker_patent_capability",
                "options": {"patent_stream_capability": "preview_v1"},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_patent_capability", {"type": "state", "status": "queued"}, ttl_seconds=900)
    captured_headers = {}

    class _Handle:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def body_iter(self):
            yield (
                b'data: {"type":"metadata","query_mode":"patent","route":"hybrid_qa","trace_id":"req_worker_patent_capability"}\n\n'
                b'data: {"type":"done","final_answer":"ok","query_mode":"patent","route":"hybrid_qa","trace_id":"req_worker_patent_capability"}\n\n'
            )

        async def abort(self):
            return None

    async def _fake_open_json_stream(*, request, target, path, payload):
        _ = target, path, payload
        captured_headers.update({key.decode("latin1").lower(): value.decode("latin1") for key, value in request.scope.get("headers", [])})
        return _Handle()

    original_open_json_stream = app.state.proxy_service.open_json_stream
    app.state.proxy_service.open_json_stream = _fake_open_json_stream

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/internal/conversations/123/tasks/req_worker_patent_capability/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/123/tasks/req_worker_patent_capability/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-patent-capability/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-patent-capability", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-patent-capability",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:10+00:00",
    )

    try:
        result = worker.run_dispatch_cycle()
    finally:
        app.state.proxy_service.open_json_stream = original_open_json_stream

    assert result.outcome == "completed"
    assert captured_headers["x-patent-stream-capability"] == "preview_v1"


def test_get_task_normalizes_raw_cancelled_status_to_public_canceled():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {
            "request_id": "req_cancelled",
            "status": "cancelled",
            "conversation_id": 99,
            "user_id": 42,
            "assistant_message_id": "msg_99",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "route": "kb_qa",
            "queue_tier": "low",
            "created_at": "2026-04-06T10:00:00+00:00",
            "cancel_allowed": False,
        },
        ttl_seconds=900,
    )
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_cancelled")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "req_cancelled"
    assert payload["status"] == "canceled"
    assert payload["terminal"] is True
    assert payload["cancel_allowed"] is False


def test_get_task_exposes_admitted_status_and_last_seq_from_relay():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        {
            "request_id": "req_admitted",
            "status": "admitted",
            "conversation_id": 88,
            "user_id": 42,
            "assistant_message_id": "msg_88",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "admitted_at": "2026-04-06T10:00:02+00:00",
        },
        ttl_seconds=900,
    )
    relay_store.append_frame("req_admitted", {"type": "state", "status": "admitted"}, ttl_seconds=600)
    relay_store.append_frame("req_admitted", {"type": "content", "delta": "hello"}, ttl_seconds=600)
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_admitted")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "admitted"
    assert payload["last_seq"] == 2
    assert payload["cancel_allowed"] is True
    assert payload["replay_available"] is True


def test_get_task_reconciles_expired_queued_task_into_terminal_truth_and_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    monkeypatch.setattr(queue_status_module.time, "time", lambda: 1000.0)
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_expired_sync",
            conversation_id=71,
            assistant_message_id="msg_expired_sync",
            quota_grant_id="grant-expired-sync",
        ),
        ttl_seconds=10,
    )
    monkeypatch.setattr(queue_status_module.time, "time", lambda: 1011.0)
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/internal/conversations/71/tasks/req_expired_sync/assistant-terminal":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 71,
                    "task_id": "req_expired_sync",
                    "assistant_message_id": "msg_expired_sync",
                    "status": "expired",
                },
            )
        if path == "/internal/quota/grants/grant-expired-sync/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-expired-sync", "counted": False, "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_expired_sync")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "expired"
    assert payload["terminal"] is True
    stored = queue_store.get_request("req_expired_sync")
    assert stored is not None
    assert stored["status"] == "expired"
    assert stored["terminal_sync_pending"] is False
    assert [path for path, _ in calls] == [
        "/internal/conversations/71/tasks/req_expired_sync/assistant-terminal",
        "/internal/quota/grants/grant-expired-sync/finalize",
    ]
    assert calls[0][1]["terminal_status"] == "expired"
    assert calls[1][1]["success"] is False


def test_get_task_reconciles_completed_terminal_sync_with_success_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {
            "request_id": "req_completed_sync",
            "status": "completed",
            "conversation_id": 72,
            "user_id": 42,
            "assistant_message_id": "msg_completed_sync",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:06+00:00",
            "completed_at": "2026-04-06T10:00:06+00:00",
            "cancel_allowed": False,
            "quota_grant_id": "grant-completed-sync",
            "terminal_sync_pending": True,
            "terminal_sync_payload": {
                "terminal_status": "completed",
                "last_seq": 4,
                "answer_text": "finished answer",
                "steps": [{"title": "retrieve"}],
                "failure": {},
                "quota_success": True,
            },
        },
        ttl_seconds=900,
    )
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/internal/conversations/72/tasks/req_completed_sync/assistant-terminal":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 72,
                    "task_id": "req_completed_sync",
                    "assistant_message_id": "msg_completed_sync",
                    "status": "completed",
                },
            )
        if path == "/internal/quota/grants/grant-completed-sync/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-completed-sync", "counted": True, "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_completed_sync")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    stored = queue_store.get_request("req_completed_sync")
    assert stored is not None
    assert stored["terminal_sync_pending"] is False
    assert [path for path, _ in calls] == [
        "/internal/conversations/72/tasks/req_completed_sync/assistant-terminal",
        "/internal/quota/grants/grant-completed-sync/finalize",
    ]
    assert calls[0][1]["terminal_status"] == "completed"
    assert calls[0][1]["answer_text"] == "finished answer"
    assert calls[1][1]["success"] is True


def test_get_task_reconciles_pending_progress_sync_for_live_task(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {
            "request_id": "req_progress_sync",
            "status": "running",
            "conversation_id": 73,
            "user_id": 42,
            "assistant_message_id": "msg_progress_sync",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:04+00:00",
            "started_at": "2026-04-06T10:00:03+00:00",
            "cancel_allowed": True,
            "persisted_last_seq": 1,
            "progress_sync_pending": True,
            "progress_sync_payload": {
                "status": "running",
                "last_seq": 3,
                "content_delta": "partial",
                "steps": [{"title": "retrieve"}],
            },
        },
        ttl_seconds=900,
    )
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/internal/conversations/73/tasks/req_progress_sync/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": "running"})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_progress_sync")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    stored = queue_store.get_request("req_progress_sync")
    assert stored is not None
    assert stored["persisted_last_seq"] == 3
    assert stored["progress_sync_pending"] is False
    assert "progress_sync_payload" not in stored
    assert calls == [
        (
            "/internal/conversations/73/tasks/req_progress_sync/assistant-progress",
            {
                "conversation_id": 73,
                "user_id": 42,
                "task_id": "req_progress_sync",
                "status": "running",
                "content_delta": "partial",
                "steps": [{"title": "retrieve"}],
                "last_seq": 3,
            },
        )
    ]


def test_admission_worker_batches_content_progress_flushes_and_marks_persisted_last_seq(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_leases_store if hasattr(app.state, "execution_slot_leases_store") else app.state.execution_slot_lease_store
    request_id = "req_worker_batched_progress"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=98,
            assistant_message_id="msg_worker_batched_progress",
            quota_grant_id="grant-worker-batched-progress",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)
    progress_calls: list[dict] = []
    terminal_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            chunk = "chunk-12345"
            frames = [
                b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_batched_progress"}\n\n'
            ]
            frames.extend(
                f'data: {{"type":"content","content":"{chunk}"}}\n\n'.encode("utf-8")
                for _ in range(100)
            )
            frames.append(
                b'data: {"type":"done","final_answer":"'
                + (chunk.encode("utf-8") * 100)
                + b'","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_batched_progress"}\n\n'
            )
            return httpx.Response(200, content=b"".join(frames), headers={"content-type": "text/event-stream"})
        if path == f"/internal/conversations/98/tasks/{request_id}/assistant-progress":
            progress_calls.append(payload)
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == f"/internal/conversations/98/tasks/{request_id}/assistant-terminal":
            terminal_calls.append(payload)
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-batched-progress/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-batched-progress", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-batched-progress",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    assert result.outcome == "completed"
    content_progress_calls = [payload for payload in progress_calls if payload.get("content_delta")]
    assert len(content_progress_calls) <= 20
    assert (len(content_progress_calls) / 100) <= 0.2
    assert terminal_calls[0]["terminal_status"] == "completed"
    assert terminal_calls[0]["last_seq"] > 0
    assert content_progress_calls[-1]["last_seq"] == terminal_calls[0]["last_seq"] - 1
    stored = queue_store.get_request(request_id)
    assert stored is not None
    assert stored["persisted_last_seq"] == terminal_calls[0]["last_seq"]


def test_admission_worker_idle_flush_persists_progress_before_next_event_arrives(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    monkeypatch.setattr(qa_task_module, "_PROGRESS_FLUSH_MAX_IDLE_SECONDS", 0.05)
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_idle_flush"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=99,
            assistant_message_id="msg_worker_idle_flush",
            quota_grant_id="grant-worker-idle-flush",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)
    first_chunk_released = threading.Event()
    continue_event = threading.Event()
    idle_progress_seen = threading.Event()
    calls: list[tuple[str, dict]] = []
    result_holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_AsyncPauseStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_idle_flush"}\n\n'
                        b'data: {"type":"content","content":"hello"}\n\n'
                    ),
                    second_chunk=b'data: {"type":"done","final_answer":"hello","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_idle_flush"}\n\n',
                    first_released=first_chunk_released,
                    continue_event=continue_event,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == f"/internal/conversations/99/tasks/{request_id}/assistant-progress":
            if payload.get("content_delta") == "hello":
                idle_progress_seen.set()
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == f"/internal/conversations/99/tasks/{request_id}/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-idle-flush/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-idle-flush", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-idle-flush",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        result_holder["result"] = worker.run_dispatch_cycle()

    thread = threading.Thread(target=_run_worker, daemon=True)
    thread.start()
    assert first_chunk_released.wait(timeout=5)
    assert idle_progress_seen.wait(timeout=5)
    terminal_calls_before_done = [
        payload
        for path, payload in calls
        if path == f"/internal/conversations/99/tasks/{request_id}/assistant-terminal"
    ]
    assert terminal_calls_before_done == []
    continue_event.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    result = result_holder["result"]
    assert result.outcome == "completed"
    progress_calls = [payload for path, payload in calls if path == f"/internal/conversations/99/tasks/{request_id}/assistant-progress"]
    assert any(payload.get("content_delta") == "hello" for payload in progress_calls)


def test_admission_worker_patent_preview_chunks_do_not_sync_into_main_assistant_progress(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_patent_preview_progress"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=99,
            assistant_message_id="msg_worker_patent_preview_progress",
            requested_mode="patent",
            actual_mode="patent",
            target_backend="patent",
            route="hybrid_qa",
            turn_mode="file_only",
            source_scope="pdf+table",
            quota_grant_id="grant-worker-patent-preview-progress",
            execution_snapshot={
                "question": "请对比两个文件",
                "conversation_id": 99,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "patent",
                "actual_mode": "patent",
                "route": "hybrid_qa",
                "turn_mode": "file_only",
                "source_scope": "pdf+table",
                "trace_id": request_id,
                "options": {"patent_stream_capability": "preview_v1"},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)
    progress_calls: list[dict] = []
    terminal_calls: list[dict] = []

    class _Handle:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def body_iter(self):
            yield (
                b'data: {"type":"metadata","query_mode":"patent","route":"hybrid_qa","trace_id":"req_worker_patent_preview_progress"}\n\n'
                b'data: {"type":"content","content":"PDF preview","content_role":"preview","content_source":"pdf","content_stream_id":"pdf:primary","content_phase":"start","replace_stream":true}\n\n'
                b'data: {"type":"content","content":" plus more","content_role":"preview","content_source":"pdf","content_stream_id":"pdf:primary","content_phase":"delta"}\n\n'
                b'data: {"type":"content","content":"final","content_role":"final","content_source":"hybrid","content_stream_id":"final:answer","content_phase":"start","replace_stream":true}\n\n'
                b'data: {"type":"content","content":" answer","content_role":"final","content_source":"hybrid","content_stream_id":"final:answer","content_phase":"delta"}\n\n'
                b'data: {"type":"done","final_answer":"final answer","query_mode":"patent","route":"hybrid_qa","trace_id":"req_worker_patent_preview_progress"}\n\n'
            )

        async def abort(self):
            return None

    async def _fake_open_json_stream(*, request, target, path, payload):
        _ = request, target, path, payload
        return _Handle()

    original_open_json_stream = app.state.proxy_service.open_json_stream
    app.state.proxy_service.open_json_stream = _fake_open_json_stream

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == f"/internal/conversations/99/tasks/{request_id}/assistant-progress":
            progress_calls.append(payload)
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == f"/internal/conversations/99/tasks/{request_id}/assistant-terminal":
            terminal_calls.append(payload)
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-patent-preview-progress/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-worker-patent-preview-progress", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-patent-preview-progress",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:11+00:00",
    )

    try:
        result = worker.run_dispatch_cycle()
    finally:
        app.state.proxy_service.open_json_stream = original_open_json_stream

    assert result.outcome == "completed"
    progress_text = "".join(payload.get("content_delta") or "" for payload in progress_calls)
    assert progress_text == "final answer"
    assert terminal_calls[0]["answer_text"] == "final answer"
    replay = relay_store.get_frames(request_id, after_sequence=0)
    content_events = [frame["payload"] for frame in replay if frame["payload"].get("type") == "content"]
    assert any(event.get("content_role") == "preview" for event in content_events)
    assert any(event.get("content_role") == "final" for event in content_events)


def test_admission_worker_idle_flush_does_not_duplicate_content_when_first_flush_overlaps_new_content(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    monkeypatch.setattr(qa_task_module, "_PROGRESS_FLUSH_MAX_IDLE_SECONDS", 0.05)
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_idle_overlap"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=100,
            assistant_message_id="msg_worker_idle_overlap",
            quota_grant_id="grant-worker-idle-overlap",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)
    first_chunk_released = threading.Event()
    continue_event = threading.Event()
    first_progress_started = threading.Event()
    allow_first_progress_return = threading.Event()
    progress_calls: list[dict] = []
    terminal_calls: list[dict] = []
    result_holder: dict[str, object] = {}

    async def _progress_task_assistant(**kwargs):
        payload = {
            "status": kwargs.get("status"),
            "content_delta": kwargs.get("content_delta"),
            "last_seq": kwargs.get("last_seq"),
            "steps": list(kwargs.get("steps") or []),
        }
        progress_calls.append(payload)
        if payload["content_delta"] == "hello":
            first_progress_started.set()
            while not allow_first_progress_return.is_set():
                await anyio.sleep(0.01)
        return {"success": True, "status": payload["status"]}

    async def _terminal_task_assistant(**kwargs):
        terminal_calls.append(
            {
                "terminal_status": kwargs.get("terminal_status"),
                "answer_text": kwargs.get("answer_text"),
                "last_seq": kwargs.get("last_seq"),
            }
        )
        return {"success": True, "status": kwargs.get("terminal_status")}

    monkeypatch.setattr(app.state.conversation_persistence_service, "progress_task_assistant", _progress_task_assistant)
    monkeypatch.setattr(app.state.conversation_persistence_service, "terminal_task_assistant", _terminal_task_assistant)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_AsyncPauseStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_idle_overlap"}\n\n'
                        b'data: {"type":"content","content":"hello"}\n\n'
                    ),
                    second_chunk=(
                        b'data: {"type":"content","content":"world"}\n\n'
                        b'data: {"type":"done","final_answer":"helloworld","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_idle_overlap"}\n\n'
                    ),
                    first_released=first_chunk_released,
                    continue_event=continue_event,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/quota/grants/grant-worker-idle-overlap/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-worker-idle-overlap", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-idle-overlap",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        result_holder["result"] = worker.run_dispatch_cycle()

    thread = threading.Thread(target=_run_worker, daemon=True)
    thread.start()
    assert first_chunk_released.wait(timeout=5)
    assert first_progress_started.wait(timeout=5)
    continue_event.set()
    time.sleep(0.1)
    allow_first_progress_return.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    result = result_holder["result"]
    assert result.outcome == "completed"
    assert [payload["content_delta"] for payload in progress_calls if payload["content_delta"]] == ["hello", "world"]
    assert len(terminal_calls) == 1
    assert terminal_calls[0]["terminal_status"] == "completed"
    assert terminal_calls[0]["answer_text"] == "helloworld"


def test_admission_worker_clears_inflight_progress_when_progress_flush_raises(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    monkeypatch.setattr(qa_task_module, "_PROGRESS_FLUSH_MAX_IDLE_SECONDS", 0.05)
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_idle_overlap_failure"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=101,
            assistant_message_id="msg_worker_idle_overlap_failure",
            quota_grant_id="grant-worker-idle-overlap-failure",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)
    first_chunk_released = threading.Event()
    continue_event = threading.Event()
    first_progress_started = threading.Event()
    progress_calls: list[dict] = []
    terminal_calls: list[dict] = []
    result_holder: dict[str, object] = {}
    original_sync_progress_best_effort = qa_task_module.GatewayTaskExecutor._sync_progress_best_effort
    first_call = {"pending": True}

    async def _raising_once_sync_progress_best_effort(self, *, request, internal_request, status, last_seq, content_delta="", steps=None):
        payload = {
            "status": status,
            "content_delta": content_delta,
            "last_seq": last_seq,
            "steps": list(steps or []),
        }
        progress_calls.append(payload)
        if payload["content_delta"] == "hello" and first_call["pending"]:
            first_call["pending"] = False
            first_progress_started.set()
            raise RuntimeError("simulated progress flush failure")
        return await original_sync_progress_best_effort(
            self,
            request=request,
            internal_request=internal_request,
            status=status,
            last_seq=last_seq,
            content_delta=content_delta,
            steps=steps,
        )

    async def _terminal_task_assistant(**kwargs):
        terminal_calls.append(
            {
                "terminal_status": kwargs.get("terminal_status"),
                "answer_text": kwargs.get("answer_text"),
                "last_seq": kwargs.get("last_seq"),
            }
        )
        return {"success": True, "status": kwargs.get("terminal_status")}

    monkeypatch.setattr(qa_task_module.GatewayTaskExecutor, "_sync_progress_best_effort", _raising_once_sync_progress_best_effort)
    monkeypatch.setattr(app.state.conversation_persistence_service, "terminal_task_assistant", _terminal_task_assistant)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_AsyncPauseStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_idle_overlap_failure"}\n\n'
                        b'data: {"type":"content","content":"hello"}\n\n'
                    ),
                    second_chunk=(
                        b'data: {"type":"content","content":"world"}\n\n'
                        b'data: {"type":"done","final_answer":"helloworld","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_idle_overlap_failure"}\n\n'
                    ),
                    first_released=first_chunk_released,
                    continue_event=continue_event,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == f"/internal/conversations/101/tasks/{request_id}/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/quota/grants/grant-worker-idle-overlap-failure/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-worker-idle-overlap-failure", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-idle-overlap-failure",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        result_holder["result"] = worker.run_dispatch_cycle()

    thread = threading.Thread(target=_run_worker, daemon=True)
    thread.start()
    assert first_chunk_released.wait(timeout=5)
    assert first_progress_started.wait(timeout=5)
    continue_event.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    result = result_holder["result"]
    assert result.outcome == "completed"
    assert [payload["content_delta"] for payload in progress_calls if payload["content_delta"]] == ["hello", "helloworld"]
    assert len(terminal_calls) == 1
    assert terminal_calls[0]["terminal_status"] == "completed"
    assert terminal_calls[0]["answer_text"] == "helloworld"


def test_admission_worker_cancel_flushes_pending_content_before_canceled_terminal(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_cancel_pending_flush"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=91,
            assistant_message_id="msg_worker_cancel_pending_flush",
            quota_grant_id="grant-worker-cancel-pending-flush",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)
    first_chunk_released = threading.Event()
    continue_event = threading.Event()
    calls: list[tuple[str, dict]] = []
    result_holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_BlockingAsyncStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_cancel_pending_flush"}\n\n'
                        b'data: {"type":"content","content":"hi"}\n\n'
                    ),
                    second_chunk=b'data: {"type":"done","final_answer":"should_not_commit","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_cancel_pending_flush"}\n\n',
                    first_released=first_chunk_released,
                    continue_event=continue_event,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == f"/internal/conversations/91/tasks/{request_id}/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == f"/internal/conversations/91/tasks/{request_id}/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-cancel-pending-flush/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-cancel-pending-flush", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-cancel-pending-flush",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        result_holder["result"] = worker.run_dispatch_cycle()

    thread = threading.Thread(target=_run_worker, daemon=True)
    thread.start()
    assert first_chunk_released.wait(timeout=5)

    client = TestClient(app)
    response = client.post(f"/api/v1/tasks/{request_id}/cancel")
    assert response.status_code == 200
    continue_event.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    result = result_holder["result"]
    assert result.outcome == "canceled"
    progress_calls = [payload for path, payload in calls if path == f"/internal/conversations/91/tasks/{request_id}/assistant-progress"]
    terminal_calls = [payload for path, payload in calls if path == f"/internal/conversations/91/tasks/{request_id}/assistant-terminal"]
    assert any(payload.get("content_delta") == "hi" for payload in progress_calls)
    assert len(terminal_calls) == 1
    flushed = next(payload for payload in progress_calls if payload.get("content_delta") == "hi")
    assert flushed["last_seq"] < terminal_calls[0]["last_seq"]
    assert terminal_calls[0]["terminal_status"] == "canceled"


def test_admission_worker_failure_terminal_persistence_does_not_clear_pending_progress(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_failed_terminal_retry"
    calls: list[tuple[str, dict]] = []
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=90,
            assistant_message_id="msg_worker_failed_terminal_retry",
            quota_grant_id="grant-worker-failed-terminal-retry",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"failed-terminal-retry"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"error","message":"boom"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == f"/internal/conversations/90/tasks/{request_id}/assistant-progress":
            return httpx.Response(500, json={"success": False, "error": "progress_sync_failed"})
        if path == f"/internal/conversations/90/tasks/{request_id}/assistant-terminal":
            return httpx.Response(500, json={"success": False, "error": "terminal_write_failed"})
        if path == "/internal/quota/grants/grant-worker-failed-terminal-retry/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-failed-terminal-retry", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-failed-terminal-retry",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    stored = queue_store.get_request(request_id)
    assert result.outcome == "failed"
    assert stored is not None
    assert stored["status"] == "failed"
    assert int(stored.get("persisted_last_seq") or 0) == 0
    assert stored["progress_sync_pending"] is True
    assert stored["progress_sync_payload"]["content_delta"] == "hello"
    assert stored["terminal_sync_pending"] is True
    assert stored["terminal_sync_payload"]["terminal_status"] == "failed"


def test_get_task_keeps_terminal_sync_pending_when_quota_finalize_returns_failure_payload(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {
            "request_id": "req_completed_quota_retry",
            "status": "completed",
            "conversation_id": 74,
            "user_id": 42,
            "assistant_message_id": "msg_completed_quota_retry",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:06+00:00",
            "completed_at": "2026-04-06T10:00:06+00:00",
            "cancel_allowed": False,
            "quota_grant_id": "grant-completed-quota-retry",
            "terminal_sync_pending": True,
            "terminal_sync_payload": {
                "terminal_status": "completed",
                "last_seq": 4,
                "answer_text": "finished answer",
                "steps": [],
                "failure": {},
                "quota_success": True,
            },
        },
        ttl_seconds=900,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/conversations/74/tasks/req_completed_quota_retry/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": "completed"})
        if request.url.path == "/internal/quota/grants/grant-completed-quota-retry/finalize":
            return httpx.Response(200, json={"success": False, "error": "quota_finalize_failed"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_completed_quota_retry")

    assert response.status_code == 200
    stored = queue_store.get_request("req_completed_quota_retry")
    assert stored is not None
    assert stored["terminal_sync_pending"] is True


def test_get_task_events_replays_only_frames_after_requested_sequence():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        {
            "request_id": "req_events",
            "status": "running",
            "conversation_id": 77,
            "user_id": 42,
            "assistant_message_id": "msg_77",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "route": "kb_qa",
            "queue_tier": "low",
            "created_at": "2026-04-06T10:00:00+00:00",
            "started_at": "2026-04-06T10:00:05+00:00",
        },
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events", {"type": "state", "status": "admitted"}, ttl_seconds=600)
    relay_store.append_frame("req_events", {"type": "state", "status": "running"}, ttl_seconds=600)
    relay_store.append_frame("req_events", {"type": "content", "delta": "partial"}, ttl_seconds=600)
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_events/events", params={"after_seq": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "req_events"
    assert payload["after_seq"] == 1
    assert [item["seq"] for item in payload["events"]] == [2, 3]
    assert payload["events"][0]["status"] == "running"
    assert payload["events"][0]["task_id"] == "req_events"
    assert payload["events"][0]["conversation_id"] == 77
    assert payload["events"][0]["assistant_message_id"] == "msg_77"


def test_get_task_events_stream_replays_then_live_tails_until_terminal():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        {
            "request_id": "req_events_stream",
            "status": "running",
            "conversation_id": 88,
            "user_id": 42,
            "assistant_message_id": "msg_88",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:05+00:00",
            "started_at": "2026-04-06T10:00:05+00:00",
        },
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events_stream", {"type": "state", "status": "queued"}, ttl_seconds=900)
    relay_store.append_frame("req_events_stream", {"type": "state", "status": "running"}, ttl_seconds=900)

    def _append_updates() -> None:
        time.sleep(0.05)
        relay_store.append_frame("req_events_stream", {"type": "content", "delta": "hello"}, ttl_seconds=900)
        time.sleep(0.05)
        terminal = dict(queue_store.get_request("req_events_stream") or {})
        terminal["status"] = "cancelled"
        terminal["updated_at"] = "2026-04-06T10:00:10+00:00"
        queue_store.put_request(terminal, ttl_seconds=900)
        relay_store.append_frame("req_events_stream", {"type": "state", "status": "canceled"}, ttl_seconds=900)

    updater = threading.Thread(target=_append_updates, daemon=True)
    updater.start()
    client = TestClient(app)

    with client.stream(
        "GET",
        "/api/v1/tasks/req_events_stream/events",
        params={"after_seq": 1},
        headers={"accept": "text/event-stream"},
    ) as response:
        body = b"".join(response.iter_bytes())

    updater.join(timeout=1)
    assert not updater.is_alive()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    payloads = _sse_payloads(body)
    assert [item["seq"] for item in payloads] == [2, 3, 4]
    assert payloads[0]["status"] == "running"
    assert payloads[1]["type"] == "content"
    assert payloads[1]["delta"] == "hello"
    assert payloads[2]["status"] == "canceled"


def test_get_task_events_filters_duplicate_upstream_seq_and_ignores_post_terminal_replay_frames():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        {
            "request_id": "req_events_guard",
            "status": "completed",
            "conversation_id": 89,
            "user_id": 42,
            "assistant_message_id": "msg_89",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:10+00:00",
            "started_at": "2026-04-06T10:00:05+00:00",
        },
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events_guard", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=900)
    relay_store.append_frame("req_events_guard", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=900)
    relay_store.append_frame("req_events_guard", {"type": "done", "seq": 8, "final_answer": "hello"}, ttl_seconds=900)
    relay_store.append_frame("req_events_guard", {"type": "content", "seq": 9, "content": "should_not_surface"}, ttl_seconds=900)
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_events_guard/events", params={"after_seq": 0})

    assert response.status_code == 200
    payload = response.json()
    assert [item["seq"] for item in payload["events"]] == [1, 2]
    assert [item["type"] for item in payload["events"]] == ["content", "done"]
    replay = client.get("/api/v1/tasks/req_events_guard/events", params={"after_seq": 1}).json()["events"]
    assert [item["type"] for item in replay] == ["done"]


def test_get_task_events_hides_already_polluted_frames_after_a_terminal_replay_window():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        {
            "request_id": "req_events_polluted_terminal",
            "status": "completed",
            "conversation_id": 90,
            "user_id": 42,
            "assistant_message_id": "msg_90",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:10+00:00",
            "started_at": "2026-04-06T10:00:05+00:00",
        },
        ttl_seconds=900,
    )
    relay_store._memory_frames["req_events_polluted_terminal"] = [
        {"sequence": 1, "payload": {"type": "content", "content": "hello"}},
        {"sequence": 2, "payload": {"type": "done", "final_answer": "hello"}},
        {"sequence": 3, "payload": {"type": "content", "content": "should_not_surface"}},
    ]
    relay_store._memory_expiry["req_events_polluted_terminal"] = relay_store._now() + 900
    relay_store._memory_request_ids.add("req_events_polluted_terminal")
    relay_store._memory_total_frames = 3
    relay_store._memory_latest_sequence["req_events_polluted_terminal"] = 3
    client = TestClient(app)

    response = client.get("/api/v1/tasks/req_events_polluted_terminal/events", params={"after_seq": 2})

    assert response.status_code == 200
    assert response.json()["events"] == []


def test_task_events_stream_immediately_dispatches_head_queued_task_without_waiting_for_worker_poll(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    app.state.settings = replace(
        app.state.settings,
        admission=replace(
            app.state.settings.admission,
            enabled=True,
            dispatcher_enabled=True,
        ),
    )
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_events_immediate_dispatch",
            conversation_id=188,
            assistant_message_id="msg_events_immediate_dispatch",
            quota_grant_id="grant-events-immediate-dispatch",
            execution_snapshot={
                "question": "dispatch immediately",
                "conversation_id": 188,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "trace_id": "req_events_immediate_dispatch",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events_immediate_dispatch", {"type": "state", "status": "queued"}, ttl_seconds=900)

    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_events_immediate_dispatch"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"hello","query_mode":"fast","route":"kb_qa","trace_id":"req_events_immediate_dispatch"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/188/tasks/req_events_immediate_dispatch/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/188/tasks/req_events_immediate_dispatch/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-events-immediate-dispatch/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-events-immediate-dispatch", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    with client.stream(
        "GET",
        "/api/v1/tasks/req_events_immediate_dispatch/events",
        params={"after_seq": 1},
        headers={"accept": "text/event-stream"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    payloads = _sse_payloads(body)
    assert [item["seq"] for item in payloads] == [2, 3, 4, 5]
    assert payloads[0]["status"] == "admitted"
    assert payloads[1]["status"] == "running"
    assert payloads[2]["type"] == "content"
    assert payloads[3]["type"] == "done"
    assert queue_store.get_request("req_events_immediate_dispatch")["status"] == "completed"
    call_paths = [path for path, _ in calls]
    assert call_paths[0] == "/internal/conversations/188/tasks/req_events_immediate_dispatch/assistant-progress"
    assert "/api/fast/ask_stream" in call_paths
    assert "/internal/conversations/188/tasks/req_events_immediate_dispatch/assistant-terminal" in call_paths
    assert call_paths[-1] == "/internal/quota/grants/grant-events-immediate-dispatch/finalize"


def test_get_task_summary_exposes_first_chunk_latency_chain_after_immediate_dispatch(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    app.state.settings = replace(
        app.state.settings,
        admission=replace(
            app.state.settings.admission,
            enabled=True,
            dispatcher_enabled=True,
        ),
    )
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_events_latency_chain",
            conversation_id=188,
            assistant_message_id="msg_events_latency_chain",
            quota_grant_id="grant-events-latency-chain",
            telemetry={"accepted_at_ms": 1000},
            execution_snapshot={
                "question": "dispatch with latency chain",
                "conversation_id": 188,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "trace_id": "req_events_latency_chain",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events_latency_chain", {"type": "state", "status": "queued"}, ttl_seconds=900)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_events_latency_chain","metadata":{"telemetry":{"backend_stream_opened_at_ms":1200}}}\n\n'
                    b'data: {"type":"step","step":"stage1","status":"processing","message":"stage1"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"hello","query_mode":"fast","route":"kb_qa","trace_id":"req_events_latency_chain"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/188/tasks/req_events_latency_chain/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/188/tasks/req_events_latency_chain/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-events-latency-chain/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-events-latency-chain", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    with client.stream(
        "GET",
        "/api/v1/tasks/req_events_latency_chain/events",
        params={"after_seq": 1},
        headers={"accept": "text/event-stream"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert _sse_payloads(body)[-1]["type"] == "done"

    summary = client.get("/api/v1/tasks/req_events_latency_chain").json()
    telemetry = summary["telemetry"]

    assert telemetry["accepted_at_ms"] == 1000
    assert isinstance(telemetry["dispatch_started_at_ms"], int)
    assert isinstance(telemetry["backend_stream_opened_at_ms"], int)
    assert telemetry["backend_stream_opened_at_ms"] == 1200
    assert isinstance(telemetry["first_step_at_ms"], int)
    assert isinstance(telemetry["first_content_at_ms"], int)
    assert telemetry["accepted_to_first_step_ms"] >= 0
    assert telemetry["dispatch_to_first_step_ms"] >= 0
    assert telemetry["accepted_to_first_content_ms"] >= 0


def test_get_task_summary_prefers_done_telemetry_when_done_arrives_without_content_frame(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    app.state.settings = replace(
        app.state.settings,
        admission=replace(
            app.state.settings.admission,
            enabled=True,
            dispatcher_enabled=True,
        ),
    )
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_events_done_latency_chain",
            conversation_id=190,
            assistant_message_id="msg_events_done_latency_chain",
            quota_grant_id="grant-events-done-latency-chain",
            telemetry={"accepted_at_ms": 1000},
            execution_snapshot={
                "question": "dispatch done telemetry only",
                "conversation_id": 190,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "trace_id": "req_events_done_latency_chain",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events_done_latency_chain", {"type": "state", "status": "queued"}, ttl_seconds=900)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_events_done_latency_chain","metadata":{"telemetry":{"backend_stream_opened_at_ms":1200}}}\n\n'
                    b'data: {"type":"step","step":"stage1","status":"processing","message":"stage1"}\n\n'
                    b'data: {"type":"done","final_answer":"hello","query_mode":"fast","route":"kb_qa","trace_id":"req_events_done_latency_chain","metadata":{"telemetry":{"first_step_at_ms":1300,"first_content_at_ms":1450}}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/190/tasks/req_events_done_latency_chain/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/190/tasks/req_events_done_latency_chain/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-events-done-latency-chain/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-events-done-latency-chain", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    with client.stream(
        "GET",
        "/api/v1/tasks/req_events_done_latency_chain/events",
        params={"after_seq": 1},
        headers={"accept": "text/event-stream"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert _sse_payloads(body)[-1]["type"] == "done"

    summary = client.get("/api/v1/tasks/req_events_done_latency_chain").json()
    telemetry = summary["telemetry"]

    assert telemetry["accepted_at_ms"] == 1000
    assert telemetry["backend_stream_opened_at_ms"] == 1200
    assert telemetry["first_step_at_ms"] == 1300
    assert telemetry["first_content_at_ms"] == 1450
    assert telemetry["accepted_to_first_step_ms"] == 300
    assert telemetry["accepted_to_first_content_ms"] == 450


def test_task_events_immediate_dispatch_does_not_double_count_duplicate_upstream_seq_in_terminal_answer(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    app.state.settings = replace(
        app.state.settings,
        admission=replace(
            app.state.settings.admission,
            enabled=True,
            dispatcher_enabled=True,
        ),
    )
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_events_duplicate_live",
            conversation_id=189,
            assistant_message_id="msg_events_duplicate_live",
            quota_grant_id="grant-events-duplicate-live",
            execution_snapshot={
                "question": "dedupe duplicate upstream seq",
                "conversation_id": 189,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "trace_id": "req_events_duplicate_live",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events_duplicate_live", {"type": "state", "status": "queued"}, ttl_seconds=900)

    terminal_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_events_duplicate_live"}\n\n'
                    b'data: {"type":"content","seq":7,"content":"hello"}\n\n'
                    b'data: {"type":"content","seq":7,"content":"hello"}\n\n'
                    b'data: {"type":"done","query_mode":"fast","route":"kb_qa","trace_id":"req_events_duplicate_live"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/189/tasks/req_events_duplicate_live/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/189/tasks/req_events_duplicate_live/assistant-terminal":
            terminal_payloads.append(payload)
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-events-duplicate-live/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-events-duplicate-live", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    with client.stream(
        "GET",
        "/api/v1/tasks/req_events_duplicate_live/events",
        params={"after_seq": 1},
        headers={"accept": "text/event-stream"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    payloads = _sse_payloads(body)
    assert [item["seq"] for item in payloads] == [2, 3, 4, 5]
    assert [item["type"] for item in payloads if item["type"] != "state"] == ["content", "done"]
    assert terminal_payloads[-1]["answer_text"] == "hello"


def test_task_events_immediate_dispatch_does_not_double_count_duplicate_upstream_seq_after_seq_less_step(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    app.state.settings = replace(
        app.state.settings,
        admission=replace(
            app.state.settings.admission,
            enabled=True,
            dispatcher_enabled=True,
        ),
    )
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_events_duplicate_interleaved",
            conversation_id=190,
            assistant_message_id="msg_events_duplicate_interleaved",
            quota_grant_id="grant-events-duplicate-interleaved",
            execution_snapshot={
                "question": "dedupe duplicate upstream seq after step",
                "conversation_id": 190,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "trace_id": "req_events_duplicate_interleaved",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_events_duplicate_interleaved", {"type": "state", "status": "queued"}, ttl_seconds=900)

    terminal_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_events_duplicate_interleaved"}\n\n'
                    b'data: {"type":"content","seq":7,"content":"hello"}\n\n'
                    b'data: {"type":"step","step":"retrieve","status":"processing","message":"retrieving"}\n\n'
                    b'data: {"type":"content","seq":7,"content":"hello"}\n\n'
                    b'data: {"type":"done","query_mode":"fast","route":"kb_qa","trace_id":"req_events_duplicate_interleaved"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/190/tasks/req_events_duplicate_interleaved/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/190/tasks/req_events_duplicate_interleaved/assistant-terminal":
            terminal_payloads.append(payload)
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-events-duplicate-interleaved/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-events-duplicate-interleaved", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    with client.stream(
        "GET",
        "/api/v1/tasks/req_events_duplicate_interleaved/events",
        params={"after_seq": 1},
        headers={"accept": "text/event-stream"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    payloads = _sse_payloads(body)
    assert [item["seq"] for item in payloads] == [2, 3, 4, 5, 6]
    assert [item["type"] for item in payloads if item["type"] != "state"] == ["content", "step", "done"]
    assert terminal_payloads[-1]["answer_text"] == "hello"


def test_cancel_task_terminalizes_queued_request_persists_canceled_state_and_aborts_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_cancel_quota",
            conversation_id=51,
            assistant_message_id="msg_cancel_quota",
            quota_grant_id="grant-cancel-quota",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_cancel_quota", {"type": "state", "status": "queued"}, ttl_seconds=900)
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/internal/conversations/51/tasks/req_cancel_quota/assistant-terminal":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 51,
                    "task_id": "req_cancel_quota",
                    "assistant_message_id": "msg_cancel_quota",
                    "status": "canceled",
                },
            )
        if path == "/internal/quota/grants/grant-cancel-quota/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-cancel-quota", "counted": False, "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks/req_cancel_quota/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    stored = queue_store.get_request("req_cancel_quota")
    assert stored is not None
    assert stored["status"] == "cancelled"
    assert [path for path, _ in calls] == [
        "/internal/conversations/51/tasks/req_cancel_quota/assistant-terminal",
        "/internal/quota/grants/grant-cancel-quota/finalize",
    ]
    assert calls[0][1]["terminal_status"] == "canceled"
    assert calls[1][1]["success"] is False
    replay = client.get("/api/v1/tasks/req_cancel_quota/events", params={"after_seq": 0}).json()["events"]
    assert [item["seq"] for item in replay] == [1, 2]
    assert replay[-1]["status"] == "canceled"


def test_cancel_task_terminalizes_queued_request_and_is_idempotent():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {
            "request_id": "req_cancel_queue",
            "status": "queued",
            "conversation_id": 51,
            "user_id": 42,
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:00+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
            "cancel_allowed": True,
        },
        ttl_seconds=900,
    )
    client = TestClient(app)

    first = client.post("/api/v1/tasks/req_cancel_queue/cancel")
    second = client.post("/api/v1/tasks/req_cancel_queue/cancel")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "canceled"
    assert second.json()["status"] == "canceled"
    assert app.state.execution_queue_status_store.get_request("req_cancel_queue")["status"] == "cancelled"


@pytest.mark.parametrize("raw_status", ["admitted", "running"])
def test_cancel_task_terminalizes_live_task_and_releases_lease(raw_status: str):
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
            {
                "request_id": f"req_{raw_status}",
                "status": raw_status,
                "conversation_id": 52,
                "user_id": 42,
                "requested_mode": "thinking",
            "actual_mode": "thinking",
            "route": "kb_qa",
            "queue_tier": "low",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:03+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
            "lease_owner_id": "worker-a",
            "cancel_allowed": False,
        },
        ttl_seconds=900,
    )
    slot_store.acquire(
        request_id=f"req_{raw_status}",
        capacity_key="thinking",
        owner_id="worker-a",
        ttl_seconds=30,
        acquired_at="2026-04-06T10:00:03+00:00",
    )
    client = TestClient(app)

    response = client.post(f"/api/v1/tasks/req_{raw_status}/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "canceled"
    assert payload["terminal"] is True
    assert slot_store.get(f"req_{raw_status}") is None


def test_cancel_task_terminalizes_running_request_persists_canceled_state_and_aborts_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_running_cancel",
            status="running",
            conversation_id=52,
            assistant_message_id="msg_running_cancel",
            lease_owner_id="worker-running",
            quota_grant_id="grant-running-cancel",
            cancel_allowed=False,
        ),
        ttl_seconds=900,
    )
    slot_store.acquire(
        request_id="req_running_cancel",
        capacity_key="fast_or_patent",
        owner_id="worker-running",
        ttl_seconds=60,
        acquired_at="2026-04-06T10:00:03+00:00",
    )
    relay_store.append_frame("req_running_cancel", {"type": "state", "status": "queued"}, ttl_seconds=900)
    relay_store.append_frame("req_running_cancel", {"type": "state", "status": "admitted"}, ttl_seconds=900)
    relay_store.append_frame("req_running_cancel", {"type": "state", "status": "running"}, ttl_seconds=900)
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/internal/conversations/52/tasks/req_running_cancel/assistant-terminal":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 52,
                    "task_id": "req_running_cancel",
                    "assistant_message_id": "msg_running_cancel",
                    "status": "canceled",
                },
            )
        if path == "/internal/quota/grants/grant-running-cancel/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-running-cancel", "counted": False, "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks/req_running_cancel/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    assert slot_store.get("req_running_cancel") is None
    assert [path for path, _ in calls] == [
        "/internal/conversations/52/tasks/req_running_cancel/assistant-terminal",
        "/internal/quota/grants/grant-running-cancel/finalize",
    ]
    replay = client.get("/api/v1/tasks/req_running_cancel/events", params={"after_seq": 2}).json()["events"]
    assert [item["seq"] for item in replay] == [3, 4]
    assert replay[-1]["status"] == "canceled"


def test_cancel_task_returns_terminal_summary_when_cas_conflict_races_with_existing_cancel(monkeypatch):
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    queued_record = {
        "request_id": "req_conflict_terminal",
        "status": "running",
        "conversation_id": 61,
        "user_id": 42,
        "requested_mode": "thinking",
        "actual_mode": "thinking",
        "route": "kb_qa",
        "queue_tier": "low",
        "created_at": "2026-04-06T10:00:00+00:00",
        "updated_at": "2026-04-06T10:00:03+00:00",
        "enqueued_at": "2026-04-06T10:00:00+00:00",
        "lease_owner_id": "worker-a",
    }
    canceled_record = dict(queued_record)
    canceled_record.update(
        {
            "status": "cancelled",
            "cancelled_at": "2026-04-06T10:00:05+00:00",
            "updated_at": "2026-04-06T10:00:05+00:00",
            "cancel_allowed": False,
        }
    )
    get_calls = {"count": 0}

    def _get_request(task_id: str):
        get_calls["count"] += 1
        if get_calls["count"] == 1:
            return dict(queued_record)
        return dict(canceled_record)

    monkeypatch.setattr(queue_store, "get_request", _get_request)
    monkeypatch.setattr(queue_store, "cancel_active_request", lambda task_id, cancelled_at=None: None)
    client = TestClient(app)

    response = client.post("/api/v1/tasks/req_conflict_terminal/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


def test_cancel_task_does_not_append_false_canceled_state_when_race_already_completed(monkeypatch):
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    running_record = {
        "request_id": "req_conflict_completed",
        "status": "running",
        "conversation_id": 64,
        "user_id": 42,
        "requested_mode": "fast",
        "actual_mode": "fast",
        "route": "kb_qa",
        "queue_tier": "high",
        "created_at": "2026-04-06T10:00:00+00:00",
        "updated_at": "2026-04-06T10:00:03+00:00",
        "enqueued_at": "2026-04-06T10:00:00+00:00",
        "assistant_message_id": "msg_conflict_completed",
        "quota_grant_id": "grant-conflict-completed",
        "cancel_allowed": True,
    }
    completed_record = dict(running_record)
    completed_record.update(
        {
            "status": "completed",
            "completed_at": "2026-04-06T10:00:05+00:00",
            "updated_at": "2026-04-06T10:00:05+00:00",
            "cancel_allowed": False,
        }
    )
    relay_store.append_frame("req_conflict_completed", {"type": "state", "status": "queued"}, ttl_seconds=900)
    relay_store.append_frame("req_conflict_completed", {"type": "state", "status": "admitted"}, ttl_seconds=900)
    relay_store.append_frame("req_conflict_completed", {"type": "state", "status": "running"}, ttl_seconds=900)
    get_calls = {"count": 0}
    calls: list[str] = []

    def _get_request(task_id: str):
        get_calls["count"] += 1
        return dict(running_record if get_calls["count"] == 1 else completed_record)

    monkeypatch.setattr(queue_store, "get_request", _get_request)
    monkeypatch.setattr(queue_store, "cancel_active_request", lambda task_id, cancelled_at=None: None)
    _set_task_transport(lambda request: calls.append(request.url.path) or httpx.Response(200, json={"success": True}))
    client = TestClient(app)

    response = client.post("/api/v1/tasks/req_conflict_completed/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    replay = client.get("/api/v1/tasks/req_conflict_completed/events", params={"after_seq": 0}).json()["events"]
    assert [item["status"] for item in replay if item["type"] == "state"] == ["queued", "admitted", "running"]
    assert calls == []


def test_cancel_task_retries_once_after_conflict_on_live_record(monkeypatch):
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    live_record = {
        "request_id": "req_conflict_retry",
        "status": "admitted",
        "conversation_id": 62,
        "user_id": 42,
        "requested_mode": "fast",
        "actual_mode": "fast",
        "route": "kb_qa",
        "queue_tier": "high",
        "created_at": "2026-04-06T10:00:00+00:00",
        "updated_at": "2026-04-06T10:00:02+00:00",
        "enqueued_at": "2026-04-06T10:00:00+00:00",
    }
    canceled_record = dict(live_record)
    canceled_record.update(
        {
            "status": "cancelled",
            "cancelled_at": "2026-04-06T10:00:05+00:00",
            "updated_at": "2026-04-06T10:00:05+00:00",
            "cancel_allowed": False,
        }
    )
    cancel_calls = {"count": 0}

    monkeypatch.setattr(queue_store, "get_request", lambda task_id: dict(canceled_record if cancel_calls["count"] >= 2 else live_record))

    def _cancel_active_request(task_id: str, *, cancelled_at: str | None = None):
        cancel_calls["count"] += 1
        if cancel_calls["count"] == 1:
            return None
        return dict(canceled_record)

    monkeypatch.setattr(queue_store, "cancel_active_request", _cancel_active_request)
    client = TestClient(app)

    response = client.post("/api/v1/tasks/req_conflict_retry/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    assert cancel_calls["count"] == 2


def test_cancel_task_does_not_report_success_when_live_lease_release_fails(monkeypatch):
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        {
            "request_id": "req_lease_fail",
            "status": "running",
            "conversation_id": 63,
            "user_id": 42,
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "route": "kb_qa",
            "queue_tier": "low",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:03+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
            "lease_owner_id": "worker-a",
        },
        ttl_seconds=900,
    )
    slot_store.acquire(
        request_id="req_lease_fail",
        capacity_key="thinking",
        owner_id="worker-a",
        ttl_seconds=30,
        acquired_at="2026-04-06T10:00:03+00:00",
    )
    monkeypatch.setattr(slot_store, "release", lambda task_id, owner_id: False)
    client = TestClient(app)

    response = client.post("/api/v1/tasks/req_lease_fail/cancel")

    assert response.status_code == 500
    assert slot_store.get("req_lease_fail") is not None


def test_create_task_persists_single_user_turn_and_placeholder_and_binds_assistant_message_id(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls: list[tuple[str, dict, dict[str, str]]] = []
    state = {"messages": [], "active_task_id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        headers = {key.lower(): value for key, value in request.headers.items()}
        calls.append((path, payload, headers))
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/create-turn"):
            task_id = path.split("/")[-2]
            state["messages"].append(
                {
                    "message_id": "m_user_001",
                    "role": "user",
                    "content": payload["message"]["content"],
                    "task_id": task_id,
                }
            )
            state["messages"].append(
                {
                    "message_id": "m_assistant_001",
                    "role": "assistant",
                    "task_id": task_id,
                }
            )
            state["active_task_id"] = task_id
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "user_message_id": "m_user_001",
                    "assistant_message_id": "m_assistant_001",
                    "status": "queued",
                    "deduped": False,
                },
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message_id"] == "m_assistant_001"
    assert payload["status"] == "queued"
    assert [path for path, _, _ in calls if path != "/api/health"] == [
        "/internal/quota/grants/precheck",
        f"/internal/conversations/123/tasks/{payload['task_id']}/create-turn",
    ]
    assert calls[2][2]["x-internal-service-name"] == "gateway"
    assert calls[2][2]["x-internal-service-token"] == "authority-test-token"
    assert len(state["messages"]) == 2
    assert [message["role"] for message in state["messages"]] == ["user", "assistant"]
    assert state["active_task_id"] == payload["task_id"]
    stored = app.state.execution_queue_status_store.get_request(payload["task_id"])
    assert stored is not None
    assert stored["assistant_message_id"] == "m_assistant_001"


def test_create_task_rejects_same_conversation_when_live_task_exists_without_persisting_side_effects():
    _set_current_task_user(7)
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {
            "request_id": "req_live_same_conversation",
            "status": "running",
            "conversation_id": 123,
            "user_id": 7,
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:05+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
        },
        ttl_seconds=900,
    )
    calls: list[str] = []
    _set_task_transport(lambda request: calls.append(request.url.path) or httpx.Response(200, json={"status": "ok"}))
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(user_id=7))

    assert response.status_code == 409
    assert response.json()["detail"] == "task_conversation_active"
    assert calls == []
    assert len(app.state.execution_queue_status_store.list_requests()) == 1


def test_create_task_rejects_same_conversation_when_provisioning_task_exists_without_persisting_side_effects():
    _set_current_task_user(7)
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {
            "request_id": "req_live_provisioning_same_conversation",
            "status": "provisioning",
            "conversation_id": 123,
            "user_id": 7,
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:05+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
            "cancel_allowed": False,
        },
        ttl_seconds=900,
    )
    calls: list[str] = []
    _set_task_transport(lambda request: calls.append(request.url.path) or httpx.Response(200, json={"status": "ok"}))
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(user_id=7))

    assert response.status_code == 409
    assert response.json()["detail"] == "task_conversation_active"
    assert calls == []
    assert len(app.state.execution_queue_status_store.list_requests()) == 1


def test_create_task_rejects_when_user_active_task_cap_reached_without_persisting_side_effects():
    queue_store = app.state.execution_queue_status_store
    for index, status in enumerate(["queued", "admitted", "running", "queued", "running"], start=1):
        queue_store.put_request(
            {
                "request_id": f"req_user_cap_{index}",
                "status": status,
                "conversation_id": 200 + index,
                "user_id": 42,
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "queue_tier": "high",
                "created_at": f"2026-04-06T10:00:0{index}+00:00",
                "updated_at": f"2026-04-06T10:00:0{index}+00:00",
                "enqueued_at": f"2026-04-06T10:00:0{index}+00:00",
            },
            ttl_seconds=900,
        )
    calls: list[str] = []
    _set_task_transport(lambda request: calls.append(request.url.path) or httpx.Response(200, json={"status": "ok"}))
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(conversation_id=999, user_id=42))

    assert response.status_code == 429
    assert response.json()["detail"] == "task_user_active_limit"
    assert calls == []
    assert len(app.state.execution_queue_status_store.list_requests()) == 5


def test_create_task_uses_configured_per_user_active_task_cap(monkeypatch):
    monkeypatch.setenv("INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE", "2")
    app.state.settings = replace(GatewaySettings.from_env(), refresh_survivable_qa_tasks_enabled=True)
    _set_current_task_user(77)
    queue_store = app.state.execution_queue_status_store
    for index, status in enumerate(["queued", "running"], start=1):
        queue_store.put_request(
            {
                "request_id": f"req_user_config_cap_{index}",
                "status": status,
                "conversation_id": 300 + index,
                "user_id": 77,
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "queue_tier": "high",
                "created_at": f"2026-04-06T10:10:0{index}+00:00",
                "updated_at": f"2026-04-06T10:10:0{index}+00:00",
                "enqueued_at": f"2026-04-06T10:10:0{index}+00:00",
            },
            ttl_seconds=900,
        )
    calls: list[str] = []
    _set_task_transport(lambda request: calls.append(request.url.path) or httpx.Response(200, json={"status": "ok"}))
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(conversation_id=1001, user_id=77))

    assert response.status_code == 429
    assert response.json()["detail"] == "task_user_active_limit"
    assert calls == []


def test_create_task_queue_full_does_not_create_task_or_persist_conversation_side_effects():
    _set_current_task_user(5000)
    queue_store = app.state.execution_queue_status_store
    for index in range(200):
        queue_store.put_request(
            {
                "request_id": f"req_queue_{index}",
                "status": "queued",
                "conversation_id": 1000 + index,
                "user_id": index + 1,
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "queue_tier": "high",
                "created_at": "2026-04-06T10:00:00+00:00",
                "updated_at": "2026-04-06T10:00:00+00:00",
                "enqueued_at": "2026-04-06T10:00:00+00:00",
            },
            ttl_seconds=900,
        )
    calls: list[str] = []
    _set_task_transport(lambda request: calls.append(request.url.path) or httpx.Response(200, json={"status": "ok"}))
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(conversation_id=5000, user_id=5000))

    assert response.status_code == 503
    assert response.json()["detail"] == "task_queue_full"
    assert calls == []
    assert len(app.state.execution_queue_status_store.list_requests(status="queued")) == 200


def test_create_task_uses_configured_queue_max_size(monkeypatch):
    monkeypatch.setenv("INTERACTIVE_QUEUE_MAX_SIZE", "3")
    app.state.settings = replace(GatewaySettings.from_env(), refresh_survivable_qa_tasks_enabled=True)
    _set_current_task_user(7000)
    queue_store = app.state.execution_queue_status_store
    for index in range(3):
        queue_store.put_request(
            {
                "request_id": f"req_queue_config_{index}",
                "status": "queued",
                "conversation_id": 6000 + index,
                "user_id": 8000 + index,
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "queue_tier": "high",
                "created_at": "2026-04-06T10:20:00+00:00",
                "updated_at": "2026-04-06T10:20:00+00:00",
                "enqueued_at": "2026-04-06T10:20:00+00:00",
            },
            ttl_seconds=900,
        )
    calls: list[str] = []
    _set_task_transport(lambda request: calls.append(request.url.path) or httpx.Response(200, json={"status": "ok"}))
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body(conversation_id=7000, user_id=7000))

    assert response.status_code == 503
    assert response.json()["detail"] == "task_queue_full"
    assert calls == []


def test_create_task_rolls_back_conversation_side_effects_when_queue_record_write_fails(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls: list[str] = []
    state = {"messages": [], "active_task_id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append(path)
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/create-turn"):
            task_id = path.split("/")[-2]
            state["messages"].append(
                {
                    "message_id": "m_user_rollback",
                    "role": "user",
                    "task_id": task_id,
                }
            )
            state["messages"].append({"message_id": "m_assistant_rollback", "role": "assistant", "task_id": task_id})
            state["active_task_id"] = task_id
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "user_message_id": "m_user_rollback",
                    "assistant_message_id": "m_assistant_rollback",
                    "status": "queued",
                    "deduped": False,
                },
            )
        if path.endswith("/rollback-create"):
            task_id = path.split("/")[-2]
            user_message_id = str(payload.get("user_message_id") or "")
            assistant_message_id = str(payload.get("assistant_message_id") or "")
            state["messages"] = [
                message
                for message in state["messages"]
                if message.get("message_id") not in {user_message_id, assistant_message_id}
                and message.get("task_id") != task_id
            ]
            if state["active_task_id"] == task_id:
                state["active_task_id"] = None
            return httpx.Response(200, json={"success": True, "conversation_id": 123, "task_id": task_id})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    put_request_calls = {"count": 0}
    original_put_request = app.state.execution_queue_status_store.put_request

    def _fail_second_put_request(record, ttl_seconds):
        put_request_calls["count"] += 1
        if put_request_calls["count"] == 2:
            return False
        return original_put_request(record, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(app.state.execution_queue_status_store, "put_request", _fail_second_put_request)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == "task_create_failed"
    assert len(state["messages"]) == 0
    assert state["active_task_id"] is None
    assert calls[1] == "/internal/quota/grants/precheck"
    assert calls[2].endswith("/create-turn")
    assert calls[3].endswith("/rollback-create")
    assert calls[4].endswith("/finalize")


def test_create_task_rolls_back_when_queue_record_write_raises(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls: list[str] = []
    state = {"messages": [], "active_task_id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append(path)
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/create-turn"):
            task_id = path.split("/")[-2]
            state["messages"].append({"message_id": "m_user_raise", "role": "user", "task_id": task_id})
            state["messages"].append({"message_id": "m_assistant_raise", "role": "assistant", "task_id": task_id})
            state["active_task_id"] = task_id
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "user_message_id": "m_user_raise",
                    "assistant_message_id": "m_assistant_raise",
                    "status": "queued",
                    "deduped": False,
                },
            )
        if path.endswith("/rollback-create"):
            task_id = path.split("/")[-2]
            state["messages"] = []
            if state["active_task_id"] == task_id:
                state["active_task_id"] = None
            return httpx.Response(200, json={"success": True, "conversation_id": 123, "task_id": task_id})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)

    put_request_calls = {"count": 0}
    original_put_request = app.state.execution_queue_status_store.put_request

    def _raise_second_put_request(record, ttl_seconds):
        put_request_calls["count"] += 1
        if put_request_calls["count"] == 2:
            raise RuntimeError("queue store exploded")
        return original_put_request(record, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(app.state.execution_queue_status_store, "put_request", _raise_second_put_request)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == "task_create_failed"
    assert state["messages"] == []
    assert state["active_task_id"] is None
    assert calls[-2].endswith("/rollback-create")
    assert calls[-1].endswith("/finalize")


def test_create_task_surfaces_compensation_failure_when_rollback_cannot_complete(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls: list[str] = []
    state = {"messages": [], "active_task_id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append(path)
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/create-turn"):
            task_id = path.split("/")[-2]
            state["messages"].append({"message_id": "m_user_compensation", "role": "user", "task_id": task_id})
            state["messages"].append({"message_id": "m_assistant_compensation", "role": "assistant", "task_id": task_id})
            state["active_task_id"] = task_id
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "user_message_id": "m_user_compensation",
                    "assistant_message_id": "m_assistant_compensation",
                    "status": "queued",
                    "deduped": False,
                },
            )
        if path.endswith("/rollback-create"):
            return httpx.Response(500, json={"success": False, "error": "rollback_failed"})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    put_request_calls = {"count": 0}
    original_put_request = app.state.execution_queue_status_store.put_request

    def _fail_second_put_request(record, ttl_seconds):
        put_request_calls["count"] += 1
        if put_request_calls["count"] == 2:
            return False
        return original_put_request(record, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(app.state.execution_queue_status_store, "put_request", _fail_second_put_request)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == "task_create_rollback_failed"
    assert len(state["messages"]) == 2
    assert state["active_task_id"]
    assert any(path.endswith("/rollback-create") for path in calls)
    finalize_calls = [path for path in calls if path.endswith("/finalize")]
    assert finalize_calls == ["/internal/quota/grants/grant-task-default/finalize"]
    remaining = app.state.execution_queue_status_store.list_requests()
    assert remaining == []


def test_create_task_rejects_blank_assistant_message_id_and_rolls_back(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls: list[str] = []
    state = {"messages": [], "active_task_id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append(path)
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/create-turn"):
            task_id = path.split("/")[-2]
            state["messages"].append({"message_id": "m_user_blank_assistant", "role": "user", "task_id": task_id})
            state["messages"].append({"message_id": "m_assistant_blank", "role": "assistant", "task_id": task_id})
            state["active_task_id"] = task_id
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "user_message_id": "m_user_blank_assistant",
                    "assistant_message_id": "",
                    "status": "queued",
                    "deduped": False,
                },
            )
        if path.endswith("/rollback-create"):
            state["messages"] = []
            state["active_task_id"] = None
            return httpx.Response(200, json={"success": True, "conversation_id": 123})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == "task_create_failed"
    assert state["messages"] == []
    assert state["active_task_id"] is None
    assert len(app.state.execution_queue_status_store.list_requests()) == 0
    assert calls[-2].endswith("/rollback-create")
    assert calls[-1].endswith("/finalize")


def test_create_task_rejects_blank_user_message_id_and_rolls_back(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls: list[str] = []
    state = {"messages": [], "active_task_id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append(path)
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/create-turn"):
            task_id = path.split("/")[-2]
            state["messages"].append({"message_id": "m_user_blank", "role": "user", "task_id": task_id})
            state["active_task_id"] = task_id
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "user_message_id": "",
                    "assistant_message_id": "m_assistant_unused",
                    "status": "queued",
                    "deduped": False,
                },
            )
        if path.endswith("/rollback-create"):
            state["messages"] = []
            state["active_task_id"] = None
            return httpx.Response(200, json={"success": True, "conversation_id": 123})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == "task_create_failed"
    assert state["messages"] == []
    assert state["active_task_id"] is None
    assert len(app.state.execution_queue_status_store.list_requests()) == 0
    assert calls[-2].endswith("/rollback-create")
    assert calls[-1].endswith("/finalize")


def test_create_task_stores_provisional_request_before_atomic_turn_creation_and_cleans_it_on_failure(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    calls: list[str] = []
    observed_task_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/internal/conversations/123/tasks/task_force_failure/create-turn":
            raise AssertionError("unexpected hard-coded task id")
        if path.endswith("/create-turn"):
            task_id = path.split("/")[-2]
            observed_task_ids.append(task_id)
            stored = queue_store.get_request(task_id)
            assert stored is not None
            assert stored["status"] == "provisioning"
            assert stored.get("assistant_message_id") in {None, ""}
            return httpx.Response(500, json={"success": False, "error": "atomic_create_failed"})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", json=_request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == "task_create_failed"
    assert calls[1] == "/internal/quota/grants/precheck"
    assert calls[2].endswith("/create-turn")
    assert calls[3].endswith("/finalize")
    assert len(observed_task_ids) == 1
    assert queue_store.get_request(observed_task_ids[0]) is None


def test_admission_worker_does_not_dispatch_provisioning_requests(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_provisioning_only"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            status="provisioning",
            cancel_allowed=False,
            assistant_message_id=None,
        ),
        ttl_seconds=900,
    )
    executor_calls: list[str] = []
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-provisioning-guard",
        executor=lambda request, lease: executor_calls.append(str(request.get("request_id") or "")) or {"outcome": "completed"},
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    assert result.outcome == "no_queued"
    assert executor_calls == []
    assert queue_store.get_request(request_id)["status"] == "provisioning"


def test_get_task_recovers_provisioning_record_into_queued_summary_and_initial_event(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    request_id = "req_provisioning_recover"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            status="provisioning",
            cancel_allowed=False,
            assistant_message_id=None,
            conversation_id=123,
            user_id=42,
            execution_snapshot={
                "question": "recover me",
                "conversation_id": 123,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "selected_file_ids": [11],
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path.endswith("/create-turn"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": request_id,
                    "user_message_id": "m_user_recover",
                    "assistant_message_id": "m_assistant_recover",
                    "status": "queued",
                    "deduped": True,
                },
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    client = TestClient(app)

    first_response = client.get(f"/api/v1/tasks/{request_id}")
    second_response = client.get(f"/api/v1/tasks/{request_id}")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    payload = first_response.json()
    assert payload["status"] == "queued"
    assert payload["assistant_message_id"] == "m_assistant_recover"
    assert second_response.json()["status"] == "queued"
    stored = queue_store.get_request(request_id)
    assert stored is not None
    assert stored["status"] == "queued"
    assert stored["assistant_message_id"] == "m_assistant_recover"
    assert stored["execution_snapshot"]["user_message_id"] == "m_user_recover"
    assert stored["execution_snapshot"]["assistant_message_id"] == "m_assistant_recover"
    replay = relay_store.get_frames(request_id, after_sequence=0)
    assert [frame["sequence"] for frame in replay] == [1]
    assert replay[0]["payload"] == {"type": "state", "status": "queued"}
    assert calls == [
        (
            f"/internal/conversations/123/tasks/{request_id}/create-turn",
            {
                "conversation_id": 123,
                "user_id": 42,
                "task_id": request_id,
                "trace_id": request_id,
                "source_service": "fastQA",
                "route": "kb_qa",
                "requested_mode": "fast",
                "actual_mode": "fast",
                "message": {"role": "user", "content": "recover me"},
                "context_hints": {"selected_file_ids": [11], "last_turn_route_hint": "kb_qa"},
                "status": "queued",
                "last_seq": 0,
            },
        )
    ]


def test_get_task_does_not_attempt_provisioning_recovery_when_recovery_lock_is_unavailable(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    request_id = "req_provisioning_lock_skip"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            status="provisioning",
            cancel_allowed=False,
            assistant_message_id=None,
            conversation_id=123,
            user_id=42,
            execution_snapshot={
                "question": "recover me later",
                "conversation_id": 123,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "selected_file_ids": [],
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    _set_task_transport(handler)

    class _NoopLockManager:
        def acquire(self, *segments, owner, ttl_seconds=5, wait_timeout_seconds=2.0, retry_interval_seconds=0.01):
            _ = segments, owner, ttl_seconds, wait_timeout_seconds, retry_interval_seconds
            return None

        def release(self, handle):
            _ = handle
            return False

    monkeypatch.setattr(app.state, "distributed_lock_manager", _NoopLockManager())
    client = TestClient(app)

    response = client.get(f"/api/v1/tasks/{request_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "provisioning"
    assert calls == []
    stored = queue_store.get_request(request_id)
    assert stored is not None
    assert stored["status"] == "provisioning"
    assert relay_store.get_frames(request_id, after_sequence=0) == []


def test_admission_worker_executes_task_stream_updates_progress_and_finalizes_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_stream",
            conversation_id=91,
            assistant_message_id="msg_worker_stream",
            quota_grant_id="grant-worker-stream",
            execution_snapshot={
                "question": "stream this task",
                "conversation_id": 91,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "trace_id": "req_worker_stream",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_stream", {"type": "state", "status": "queued"}, ttl_seconds=900)
    calls: list[tuple[str, dict]] = []
    backend_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            backend_headers.append({key.lower(): value for key, value in request.headers.items()})
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_stream"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"hello","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_stream"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/91/tasks/req_worker_stream/assistant-progress":
            return httpx.Response(200, json={"success": True, "conversation_id": 91, "task_id": "req_worker_stream", "assistant_message_id": "msg_worker_stream", "status": payload.get("status")})
        if path == "/internal/conversations/91/tasks/req_worker_stream/assistant-terminal":
            return httpx.Response(200, json={"success": True, "conversation_id": 91, "task_id": "req_worker_stream", "assistant_message_id": "msg_worker_stream", "status": "completed"})
        if path == "/internal/quota/grants/grant-worker-stream/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-stream", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-task-api",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    assert result.outcome == "completed"
    assert queue_store.get_request("req_worker_stream")["status"] == "completed"
    replay = relay_store.get_frames("req_worker_stream", after_sequence=0)
    assert [frame["sequence"] for frame in replay] == [1, 2, 3, 4, 5]
    assert [frame["payload"]["type"] for frame in replay] == ["state", "state", "state", "content", "done"]
    assert replay[1]["payload"]["status"] == "admitted"
    assert replay[2]["payload"]["status"] == "running"
    assert backend_headers[0]["x-gateway-task-execution"] == "1"
    assert backend_headers[0]["x-gateway-owned-persistence"] == "1"
    assert backend_headers[0]["x-internal-service-name"] == "gateway"
    assert backend_headers[0]["x-internal-service-token"] == "authority-test-token"
    assert calls[-1][0] == "/internal/quota/grants/grant-worker-stream/finalize"
    assert calls[-1][1]["success"] is True


def test_admission_worker_logs_dispatch_and_stream_milestones(monkeypatch, caplog):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_stream_log",
            conversation_id=92,
            assistant_message_id="msg_worker_stream_log",
            quota_grant_id="grant-worker-stream-log",
            execution_snapshot={
                "question": "stream this task with logs",
                "conversation_id": 92,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "trace_id": "req_worker_stream_log",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_stream_log", {"type": "state", "status": "queued"}, ttl_seconds=900)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_stream_log"}\n\n'
                    b'data: {"type":"step","step":"stage1","status":"processing","message":"stage1"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"hello","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_stream_log"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/92/tasks/req_worker_stream_log/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/92/tasks/req_worker_stream_log/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-stream-log/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-stream-log", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-task-api-log",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    with caplog.at_level(logging.INFO):
        result = worker.run_dispatch_cycle()

    assert result.outcome == "completed"
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "gateway admission claim succeeded request_id=req_worker_stream_log" in text
    assert "gateway task upstream stream opened request_id=req_worker_stream_log" in text
    assert "gateway task first step request_id=req_worker_stream_log" in text
    assert "gateway task first content request_id=req_worker_stream_log" in text
    assert "gateway admission completed request_id=req_worker_stream_log" in text


def test_admission_worker_executes_thinking_task_stream_with_saved_authorization_header(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_thinking_stream",
            requested_mode="thinking",
            actual_mode="thinking",
            target_backend="thinking",
            route="thinking_qa",
            assistant_message_id="msg_worker_thinking_stream",
            quota_grant_id="grant-worker-thinking-stream",
            downstream_authorization="Bearer saved-task-token",
            execution_snapshot={
                "question": "Explain the paper",
                "conversation_id": 95,
                "user_id": 42,
                "chat_history": [],
                "requested_mode": "thinking",
                "actual_mode": "thinking",
                "route": "thinking_qa",
                "trace_id": "req_worker_thinking_stream",
                "downstream_authorization": "Bearer saved-task-token",
                "options": {},
            },
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_thinking_stream", {"type": "state", "status": "queued"}, ttl_seconds=900)
    backend_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/thinking/ask_stream":
            backend_headers.append({key.lower(): value for key, value in request.headers.items()})
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"thinking","route":"thinking_qa","trace_id":"req_worker_thinking_stream"}\n\n'
                    b'data: {"type":"done","final_answer":"deep answer","query_mode":"thinking","route":"thinking_qa","trace_id":"req_worker_thinking_stream"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/123/tasks/req_worker_thinking_stream/assistant-progress":
            return httpx.Response(200, json={"success": True, "status": payload.get("status")})
        if path == "/internal/conversations/123/tasks/req_worker_thinking_stream/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-thinking-stream/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-thinking-stream", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-thinking-task-api",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    assert result.outcome == "completed"
    assert queue_store.get_request("req_worker_thinking_stream")["status"] == "completed"
    assert backend_headers[0]["authorization"] == "Bearer saved-task-token"
    assert backend_headers[0]["x-gateway-task-execution"] == "1"
    assert backend_headers[0]["x-gateway-owned-persistence"] == "1"
    assert backend_headers[0]["x-internal-service-name"] == "gateway"
    assert backend_headers[0]["x-internal-service-token"] == "authority-test-token"


def test_admission_worker_does_not_requeue_when_progress_sync_fails(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_progress_failure"
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=97,
            assistant_message_id="msg_worker_progress_failure",
            quota_grant_id="grant-worker-progress-failure",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"hello"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == f"/internal/conversations/97/tasks/{request_id}/assistant-progress":
            return httpx.Response(500, json={"success": False, "error": "progress_sync_failed"})
        if path == f"/internal/conversations/97/tasks/{request_id}/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": "completed"})
        if path == "/internal/quota/grants/grant-worker-progress-failure/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-progress-failure", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-progress-failure",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    stored = queue_store.get_request(request_id)
    assert result.outcome == "completed"
    assert stored is not None
    assert stored["status"] == "completed"
    assert stored.get("last_dispatch_error") in {None, ""}
    assert stored.get("progress_sync_pending") in {None, False}
    assert "progress_sync_payload" not in stored
    finalize_calls = [payload for path, payload in calls if path == "/internal/quota/grants/grant-worker-progress-failure/finalize"]
    assert finalize_calls == [{"success": True}]


def test_admission_worker_cancel_midstream_stops_without_completed_terminal_or_success_finalize(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_cancel",
            conversation_id=92,
            assistant_message_id="msg_worker_cancel",
            quota_grant_id="grant-worker-cancel",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_cancel", {"type": "state", "status": "queued"}, ttl_seconds=900)
    first_chunk_released = threading.Event()
    continue_event = threading.Event()
    calls: list[tuple[str, dict]] = []
    result_holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_BlockingAsyncStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_cancel"}\n\n'
                        b'data: {"type":"content","content":"hello"}\n\n'
                    ),
                    second_chunk=b'data: {"type":"done","final_answer":"should_not_commit","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_cancel"}\n\n',
                    first_released=first_chunk_released,
                    continue_event=continue_event,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/92/tasks/req_worker_cancel/assistant-progress":
            return httpx.Response(200, json={"success": True, "conversation_id": 92, "task_id": "req_worker_cancel", "assistant_message_id": "msg_worker_cancel", "status": payload.get("status")})
        if path == "/internal/conversations/92/tasks/req_worker_cancel/assistant-terminal":
            return httpx.Response(200, json={"success": True, "conversation_id": 92, "task_id": "req_worker_cancel", "assistant_message_id": "msg_worker_cancel", "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-cancel/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-cancel", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-task-cancel",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        result_holder["result"] = worker.run_dispatch_cycle()

    thread = threading.Thread(target=_run_worker, daemon=True)
    thread.start()
    assert first_chunk_released.wait(timeout=5)

    client = TestClient(app)
    response = client.post("/api/v1/tasks/req_worker_cancel/cancel")
    assert response.status_code == 200
    continue_event.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    result = result_holder["result"]
    assert result.outcome == "canceled"
    assert queue_store.get_request("req_worker_cancel")["status"] == "cancelled"
    finalize_calls = [payload for path, payload in calls if path == "/internal/quota/grants/grant-worker-cancel/finalize"]
    assert finalize_calls == [{"success": False}]
    terminal_calls = [payload for path, payload in calls if path == "/internal/conversations/92/tasks/req_worker_cancel/assistant-terminal"]
    assert len(terminal_calls) == 1
    assert terminal_calls[0]["terminal_status"] == "canceled"


def test_admission_worker_cancel_aborts_upstream_stream_without_waiting_for_next_chunk(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_abort_cancel",
            conversation_id=96,
            assistant_message_id="msg_worker_abort_cancel",
            quota_grant_id="grant-worker-abort-cancel",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_abort_cancel", {"type": "state", "status": "queued"}, ttl_seconds=900)
    first_chunk_released = threading.Event()
    continue_event = threading.Event()
    closed_event = threading.Event()
    calls: list[tuple[str, dict]] = []
    result_holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_AbortAwareBlockingAsyncStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_abort_cancel"}\n\n'
                        b'data: {"type":"content","content":"hello"}\n\n'
                    ),
                    second_chunk=b'data: {"type":"done","final_answer":"should_not_commit","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_abort_cancel"}\n\n',
                    first_released=first_chunk_released,
                    continue_event=continue_event,
                    closed_event=closed_event,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/96/tasks/req_worker_abort_cancel/assistant-progress":
            return httpx.Response(200, json={"success": True, "conversation_id": 96, "task_id": "req_worker_abort_cancel", "assistant_message_id": "msg_worker_abort_cancel", "status": payload.get("status")})
        if path == "/internal/conversations/96/tasks/req_worker_abort_cancel/assistant-terminal":
            return httpx.Response(200, json={"success": True, "conversation_id": 96, "task_id": "req_worker_abort_cancel", "assistant_message_id": "msg_worker_abort_cancel", "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-abort-cancel/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-abort-cancel", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-abort-task-cancel",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        result_holder["result"] = worker.run_dispatch_cycle()

    thread = threading.Thread(target=_run_worker, daemon=True)
    thread.start()
    assert first_chunk_released.wait(timeout=5)

    client = TestClient(app)
    response = client.post("/api/v1/tasks/req_worker_abort_cancel/cancel")

    assert response.status_code == 200
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert closed_event.is_set()
    result = result_holder["result"]
    assert result.outcome == "canceled"
    finalize_calls = [payload for path, payload in calls if path == "/internal/quota/grants/grant-worker-abort-cancel/finalize"]
    assert finalize_calls == [{"success": False}]


def test_admission_worker_cancel_same_chunk_done_race_does_not_commit_completed_side_effects(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        _queued_task_record(
            request_id="req_worker_same_chunk_cancel",
            conversation_id=94,
            assistant_message_id="msg_worker_same_chunk_cancel",
            quota_grant_id="grant-worker-same-chunk-cancel",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame("req_worker_same_chunk_cancel", {"type": "state", "status": "queued"}, ttl_seconds=900)
    progress_gate = threading.Event()
    continue_progress = threading.Event()
    calls: list[tuple[str, dict]] = []
    result_holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_same_chunk_cancel"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"should_not_commit","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_same_chunk_cancel"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == "/internal/conversations/94/tasks/req_worker_same_chunk_cancel/assistant-progress":
            progress_gate.set()
            continue_progress.wait(timeout=5)
            return httpx.Response(200, json={"success": True})
        if path == "/internal/conversations/94/tasks/req_worker_same_chunk_cancel/assistant-terminal":
            return httpx.Response(200, json={"success": True, "status": payload.get("terminal_status")})
        if path == "/internal/quota/grants/grant-worker-same-chunk-cancel/finalize":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-same-chunk-cancel", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-same-chunk-cancel",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        result_holder["result"] = worker.run_dispatch_cycle()

    thread = threading.Thread(target=_run_worker, daemon=True)
    thread.start()
    assert progress_gate.wait(timeout=5)

    client = TestClient(app)
    response = client.post("/api/v1/tasks/req_worker_same_chunk_cancel/cancel")
    assert response.status_code == 200
    continue_progress.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    result = result_holder["result"]
    assert result.outcome == "canceled"
    finalize_calls = [payload for path, payload in calls if path == "/internal/quota/grants/grant-worker-same-chunk-cancel/finalize"]
    assert finalize_calls == [{"success": False}]
    terminal_calls = [payload for path, payload in calls if path == "/internal/conversations/94/tasks/req_worker_same_chunk_cancel/assistant-terminal"]
    assert len(terminal_calls) == 1
    assert terminal_calls[0]["terminal_status"] == "canceled"


@pytest.mark.parametrize(
    ("failing_path", "failure_status", "expected_finalize_calls"),
    [
        ("assistant-terminal", 500, []),
        ("quota-finalize", 503, [{"success": True}]),
    ],
)
def test_admission_worker_marks_terminal_sync_pending_when_post_done_side_effect_fails(
    monkeypatch,
    failing_path: str,
    failure_status: int,
    expected_finalize_calls: list[dict[str, bool]],
):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = f"req_worker_side_effect_{failing_path}"
    calls: list[tuple[str, dict]] = []
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=93,
            assistant_message_id="msg_worker_side_effect",
            quota_grant_id="grant-worker-side-effect",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"side-effect"}\n\n'
                    b'data: {"type":"done","final_answer":"ok","query_mode":"fast","route":"kb_qa","trace_id":"side-effect"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == f"/internal/conversations/93/tasks/{request_id}/assistant-progress":
            return httpx.Response(200, json={"success": True})
        if path == f"/internal/conversations/93/tasks/{request_id}/assistant-terminal":
            if failing_path == "assistant-terminal":
                return httpx.Response(failure_status, json={"success": False, "error": "terminal_write_failed"})
            return httpx.Response(200, json={"success": True})
        if path == "/internal/quota/grants/grant-worker-side-effect/finalize":
            if failing_path == "quota-finalize":
                return httpx.Response(failure_status, json={"success": False, "error": "quota_finalize_failed"})
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-worker-side-effect", "counted": payload["success"], "idempotent": False}})
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-side-effect",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    stored = queue_store.get_request(request_id)

    assert result.outcome == "completed"
    assert stored is not None
    assert stored["status"] == "completed"
    assert stored["terminal_sync_pending"] is True
    assert stored["terminal_sync_payload"]["terminal_status"] == "completed"
    assert stored["terminal_sync_payload"]["answer_text"] == "ok"
    assert stored["terminal_sync_payload"]["quota_success"] is True
    assert [path for path, _ in calls if path.endswith("/rollback-create")] == []
    finalize_calls = [payload for path, payload in calls if path == "/internal/quota/grants/grant-worker-side-effect/finalize"]
    assert finalize_calls == expected_finalize_calls


def test_get_task_retries_completed_terminal_sync_after_post_done_failure(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    request_id = "req_worker_terminal_repair"
    calls: list[tuple[str, dict]] = []
    queue_store.put_request(
        _queued_task_record(
            request_id=request_id,
            conversation_id=95,
            assistant_message_id="msg_worker_terminal_repair",
            quota_grant_id="grant-worker-terminal-repair",
        ),
        ttl_seconds=900,
    )
    relay_store.append_frame(request_id, {"type": "state", "status": "queued"}, ttl_seconds=900)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        calls.append((path, payload))
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"done","final_answer":"ok","query_mode":"fast","route":"kb_qa","trace_id":"terminal-repair"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path == f"/internal/conversations/95/tasks/{request_id}/assistant-progress":
            return httpx.Response(200, json={"success": True})
        if path == f"/internal/conversations/95/tasks/{request_id}/assistant-terminal":
            if len([item for item in calls if item[0] == path]) == 1:
                return httpx.Response(500, json={"success": False, "error": "terminal_write_failed"})
            return httpx.Response(200, json={"success": True, "status": "completed"})
        if path == "/internal/quota/grants/grant-worker-terminal-repair/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-worker-terminal-repair", "counted": payload["success"], "idempotent": False}},
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_task_transport(handler)
    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-terminal-repair",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    client = TestClient(app)
    detail = client.get(f"/api/v1/tasks/{request_id}")

    assert result.outcome == "completed"
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    stored = queue_store.get_request(request_id)
    assert stored is not None
    assert stored["status"] == "completed"
    assert stored["terminal_sync_pending"] is False
    terminal_calls = [payload for path, payload in calls if path == f"/internal/conversations/95/tasks/{request_id}/assistant-terminal"]
    assert len(terminal_calls) == 2
    assert terminal_calls[-1]["terminal_status"] == "completed"
    finalize_calls = [payload for path, payload in calls if path == "/internal/quota/grants/grant-worker-terminal-repair/finalize"]
    assert finalize_calls == [{"success": True}]
