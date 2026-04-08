import json
import threading
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.redis.service import RedisService
from app.main import app
from app.services import qa_tasks as qa_task_module
from app.services.execution_admission import ExecutionAdmissionDispatcher, ExecutionAdmissionWorker
from app.services.execution_event_relay import ExecutionEventRelayStore
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


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
    try:
        yield
    finally:
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
        "question": "Explain refresh recovery",
        "requested_mode": "fast",
        "user_id": 42,
        "chat_history": [],
        "pdf_context": {},
        "options": {},
    }
    payload.update(overrides)
    return payload


def _set_transport(handler) -> None:
    transport = httpx.MockTransport(handler)
    app.state.proxy_service.set_transport(transport)
    app.state.conversation_persistence_service.set_transport(transport)
    app.state.quota_proxy_service.set_transport(transport)
    app.state.gateway_auth_service.set_transport(transport)


class _ThreeChunkBlockingStream(httpx.AsyncByteStream):
    def __init__(
        self,
        *,
        first_chunk: bytes,
        second_chunk: bytes,
        third_chunk: bytes,
        first_released: threading.Event,
        second_released: threading.Event,
        allow_second_chunk: threading.Event,
        allow_third_chunk: threading.Event,
    ) -> None:
        self._first_chunk = first_chunk
        self._second_chunk = second_chunk
        self._third_chunk = third_chunk
        self._first_released = first_released
        self._second_released = second_released
        self._allow_second_chunk = allow_second_chunk
        self._allow_third_chunk = allow_third_chunk

    async def __aiter__(self):
        yield self._first_chunk
        self._first_released.set()
        self._allow_second_chunk.wait(timeout=5)
        yield self._second_chunk
        self._second_released.set()
        self._allow_third_chunk.wait(timeout=5)
        yield self._third_chunk

    async def aclose(self) -> None:
        return None


def test_refresh_survivable_task_end_to_end_replays_after_seq_then_cancels_without_duplicate_messages():
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    first_chunk_released = threading.Event()
    second_chunk_released = threading.Event()
    allow_second_chunk = threading.Event()
    allow_third_chunk = threading.Event()
    worker_result_holder: dict[str, object] = {}
    task_id_holder: dict[str, str] = {}
    state = {
        "messages": [],
        "active_task_id": None,
        "assistant_message_id": None,
        "assistant_terminal_calls": [],
        "user_create_calls": 0,
        "assistant_start_calls": 0,
    }

    def _assistant_message() -> dict:
        for message in state["messages"]:
            if message.get("role") == "assistant":
                return message
        raise AssertionError("assistant placeholder missing")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/v1/auth/me":
            return httpx.Response(
                200,
                json={"success": True, "data": {"id": 42, "username": "demo", "role": "user"}},
            )
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-e2e-task", "quota_type": "ask_query", "noop": False}},
            )
        if path == "/internal/quota/grants/grant-e2e-task/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-e2e-task", "counted": bool(payload.get("success")), "idempotent": False}},
            )
        if path == "/internal/conversations/123/messages/user":
            state["user_create_calls"] += 1
            if state["user_create_calls"] > 1:
                raise AssertionError("user message create path called more than once")
            state["messages"].append(
                {
                    "message_id": "m_user_e2e",
                    "role": "user",
                    "content": payload["message"]["content"],
                    "metadata": {
                        "route": payload["message"].get("route"),
                        "requested_mode": payload["message"].get("requested_mode"),
                        "actual_mode": payload["message"].get("actual_mode"),
                    },
                }
            )
            return httpx.Response(201, json={"success": True, "message_id": "m_user_e2e", "deduped": False})
        if path.endswith("/assistant-start"):
            state["assistant_start_calls"] += 1
            if state["assistant_start_calls"] > 1:
                raise AssertionError("assistant placeholder start path called more than once")
            task_id = path.split("/")[-2]
            task_id_holder["task_id"] = task_id
            state["active_task_id"] = task_id
            state["assistant_message_id"] = "m_assistant_e2e"
            state["messages"].append(
                {
                    "message_id": "m_assistant_e2e",
                    "role": "assistant",
                    "content": "",
                    "status": "queued",
                    "metadata": {
                        "task_id": task_id,
                        "task_status": "queued",
                        "last_seq": int(payload.get("last_seq") or 0),
                    },
                }
            )
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "assistant_message_id": "m_assistant_e2e",
                    "status": "queued",
                },
            )
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_ThreeChunkBlockingStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_e2e"}\n\n'
                        b'data: {"type":"content","content":"hello"}\n\n'
                    ),
                    second_chunk=b'data: {"type":"content","content":" world"}\n\n',
                    third_chunk=b'data: {"type":"done","final_answer":"hello world","query_mode":"fast","route":"kb_qa","trace_id":"req_e2e"}\n\n',
                    first_released=first_chunk_released,
                    second_released=second_chunk_released,
                    allow_second_chunk=allow_second_chunk,
                    allow_third_chunk=allow_third_chunk,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path.endswith("/assistant-progress"):
            assistant = _assistant_message()
            assistant["status"] = payload["status"]
            assistant["content"] = f"{assistant.get('content', '')}{payload.get('content_delta') or ''}"
            assistant["metadata"] = {
                **(assistant.get("metadata") or {}),
                "task_id": payload["task_id"],
                "task_status": payload["status"],
                "last_seq": int(payload["last_seq"]),
                "steps": payload.get("steps") or [],
            }
            state["active_task_id"] = payload["task_id"]
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": payload["task_id"],
                    "assistant_message_id": "m_assistant_e2e",
                    "status": payload["status"],
                },
            )
        if path.endswith("/assistant-terminal"):
            assistant = _assistant_message()
            assistant["status"] = payload["terminal_status"]
            if payload.get("answer_text"):
                assistant["content"] = payload["answer_text"]
            assistant["metadata"] = {
                **(assistant.get("metadata") or {}),
                "task_id": payload["task_id"],
                "task_status": payload["terminal_status"],
                "terminal_status": payload["terminal_status"],
                "last_seq": int(payload["last_seq"]),
                "failure": payload.get("failure") or {},
            }
            if state["active_task_id"] == payload["task_id"]:
                state["active_task_id"] = None
            state["assistant_terminal_calls"].append(payload["terminal_status"])
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": payload["task_id"],
                    "assistant_message_id": "m_assistant_e2e",
                    "status": payload["terminal_status"],
                },
            )
        if path == "/api/conversations/123":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "conversation_id": 123,
                        "user_id": 42,
                        "title": "refresh e2e",
                        "message_count": len(state["messages"]),
                        "created_at": "2026-04-06T09:00:00+00:00",
                        "updated_at": "2026-04-06T09:05:00+00:00",
                        "messages": list(state["messages"]),
                        "uploaded_files": [],
                        "uploaded_files_all": [],
                        "pdf_files": [],
                        "excel_files": [],
                    },
                },
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_transport(handler)
    client = TestClient(app)
    auth_headers = {"Authorization": "Bearer demo"}

    create_response = client.post("/api/v1/tasks", json=_request_body(), headers=auth_headers)
    assert create_response.status_code == 200
    create_payload = create_response.json()
    task_id = create_payload["task_id"]
    assert task_id_holder["task_id"] == task_id
    assert create_payload["status"] == "queued"

    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-refresh-e2e",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        worker_result_holder["result"] = worker.run_dispatch_cycle()

    worker_thread = threading.Thread(target=_run_worker, daemon=True)
    worker_thread.start()
    assert first_chunk_released.wait(timeout=5)

    first_replay = client.get(f"/api/v1/tasks/{task_id}/events", params={"after_seq": 0}, headers=auth_headers)
    assert first_replay.status_code == 200
    first_events = first_replay.json()["events"]
    assert [event["seq"] for event in first_events] == [1, 2, 3, 4]
    assert [event["type"] for event in first_events] == ["state", "state", "state", "content"]
    assert first_events[0]["status"] == "queued"
    assert first_events[1]["status"] == "admitted"
    assert first_events[2]["status"] == "running"
    last_seq = first_events[-1]["seq"]
    resume_after_seq = last_seq - 1

    detail_before_flush = client.get("/api/conversations/123", headers={"Authorization": "Bearer demo"})
    assert detail_before_flush.status_code == 200
    detail_before_flush_payload = detail_before_flush.json()["data"]
    assert detail_before_flush_payload["active_task"]["task_id"] == task_id
    assert len(detail_before_flush_payload["messages"]) == 2
    assistant_before_flush = detail_before_flush_payload["messages"][1]
    assert assistant_before_flush["content"] == ""
    assert assistant_before_flush["metadata"]["last_seq"] == 3

    resumed_stream_holder: dict[str, object] = {}
    cancel_result_holder: dict[str, object] = {}

    def _continue_and_cancel() -> None:
        time.sleep(0.05)
        allow_second_chunk.set()
        if not second_chunk_released.wait(timeout=5):
            return
        cancel_client = TestClient(app)
        cancel_response = cancel_client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth_headers)
        cancel_result_holder["status_code"] = cancel_response.status_code
        cancel_result_holder["payload"] = cancel_response.json()
        allow_third_chunk.set()

    continue_thread = threading.Thread(target=_continue_and_cancel, daemon=True)
    continue_thread.start()

    with TestClient(app).stream(
        "GET",
        f"/api/v1/tasks/{task_id}/events",
        params={"after_seq": resume_after_seq},
        headers={**auth_headers, "accept": "text/event-stream"},
    ) as response:
        resumed_stream_holder["status_code"] = response.status_code
        resumed_stream_holder["headers"] = dict(response.headers)
        resumed_stream_holder["body"] = b"".join(response.iter_bytes())

    continue_thread.join(timeout=5)
    assert not continue_thread.is_alive()
    worker_thread.join(timeout=5)
    assert not worker_thread.is_alive()

    result = worker_result_holder["result"]
    assert result.outcome == "canceled"
    assert cancel_result_holder["status_code"] == 200
    assert cancel_result_holder["payload"]["status"] == "canceled"

    assert resumed_stream_holder["status_code"] == 200
    assert str((resumed_stream_holder["headers"] or {}).get("content-type") or "").startswith("text/event-stream")
    resumed_events = _sse_payloads(resumed_stream_holder["body"])
    assert [event["seq"] for event in resumed_events] == [4, 5, 6]
    assert resumed_events[0]["type"] == "content"
    assert resumed_events[0]["assistant_message_id"] == "m_assistant_e2e"
    assert resumed_events[1]["type"] == "content"
    assert resumed_events[2]["type"] == "state"
    assert resumed_events[2]["status"] == "canceled"

    detail_response = client.get("/api/conversations/123", headers={"Authorization": "Bearer demo"})
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()["data"]
    assert detail_payload["active_task"] is None
    assert len(detail_payload["messages"]) == 2
    assert [message["role"] for message in detail_payload["messages"]] == ["user", "assistant"]
    assert detail_payload["messages"][0]["content"] == "Explain refresh recovery"
    assistant = detail_payload["messages"][1]
    assert assistant["message_id"] == "m_assistant_e2e"
    assert assistant["status"] == "canceled"
    assert assistant["content"] == "hello world"
    assert assistant["metadata"]["terminal_status"] == "canceled"
    assert state["user_create_calls"] == 1
    assert state["assistant_start_calls"] == 1
    assert state["assistant_terminal_calls"] == ["canceled"]
    assert relay_store.describe_request(task_id)["latest_sequence"] == 6


def test_refresh_survivable_task_end_to_end_replays_after_seq_then_completes_without_duplicate_messages():
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    first_chunk_released = threading.Event()
    second_chunk_released = threading.Event()
    allow_second_chunk = threading.Event()
    allow_third_chunk = threading.Event()
    worker_result_holder: dict[str, object] = {}
    task_id_holder: dict[str, str] = {}
    state = {
        "messages": [],
        "active_task_id": None,
        "assistant_message_id": None,
        "assistant_terminal_calls": [],
        "user_create_calls": 0,
        "assistant_start_calls": 0,
    }

    def _assistant_message() -> dict:
        for message in state["messages"]:
            if message.get("role") == "assistant":
                return message
        raise AssertionError("assistant placeholder missing")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = _json_request_body(request)
        if path == "/api/v1/auth/me":
            return httpx.Response(200, json={"success": True, "data": {"id": 42, "username": "demo", "role": "user"}})
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-e2e-done-task", "quota_type": "ask_query", "noop": False}},
            )
        if path == "/internal/quota/grants/grant-e2e-done-task/finalize":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-e2e-done-task", "counted": bool(payload.get("success")), "idempotent": False}},
            )
        if path == "/internal/conversations/123/messages/user":
            state["user_create_calls"] += 1
            if state["user_create_calls"] > 1:
                raise AssertionError("user message create path called more than once")
            state["messages"].append(
                {
                    "message_id": "m_user_e2e_done",
                    "role": "user",
                    "content": payload["message"]["content"],
                    "metadata": {
                        "route": payload["message"].get("route"),
                        "requested_mode": payload["message"].get("requested_mode"),
                        "actual_mode": payload["message"].get("actual_mode"),
                    },
                }
            )
            return httpx.Response(201, json={"success": True, "message_id": "m_user_e2e_done", "deduped": False})
        if path.endswith("/assistant-start"):
            state["assistant_start_calls"] += 1
            if state["assistant_start_calls"] > 1:
                raise AssertionError("assistant placeholder start path called more than once")
            task_id = path.split("/")[-2]
            task_id_holder["task_id"] = task_id
            state["active_task_id"] = task_id
            state["assistant_message_id"] = "m_assistant_e2e_done"
            state["messages"].append(
                {
                    "message_id": "m_assistant_e2e_done",
                    "role": "assistant",
                    "content": "",
                    "status": "queued",
                    "metadata": {
                        "task_id": task_id,
                        "task_status": "queued",
                        "last_seq": int(payload.get("last_seq") or 0),
                    },
                }
            )
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": task_id,
                    "assistant_message_id": "m_assistant_e2e_done",
                    "status": "queued",
                },
            )
        if path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=_ThreeChunkBlockingStream(
                    first_chunk=(
                        b'data: {"type":"metadata","query_mode":"fast","route":"kb_qa","trace_id":"req_e2e_done"}\n\n'
                        b'data: {"type":"content","content":"hello"}\n\n'
                    ),
                    second_chunk=b'data: {"type":"content","content":" world"}\n\n',
                    third_chunk=b'data: {"type":"done","final_answer":"hello world","query_mode":"fast","route":"kb_qa","trace_id":"req_e2e_done"}\n\n',
                    first_released=first_chunk_released,
                    second_released=second_chunk_released,
                    allow_second_chunk=allow_second_chunk,
                    allow_third_chunk=allow_third_chunk,
                ),
                headers={"content-type": "text/event-stream"},
            )
        if path.endswith("/assistant-progress"):
            assistant = _assistant_message()
            assistant["status"] = payload["status"]
            assistant["content"] = f"{assistant.get('content', '')}{payload.get('content_delta') or ''}"
            assistant["metadata"] = {
                **(assistant.get("metadata") or {}),
                "task_id": payload["task_id"],
                "task_status": payload["status"],
                "last_seq": int(payload["last_seq"]),
                "steps": payload.get("steps") or [],
            }
            state["active_task_id"] = payload["task_id"]
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": payload["task_id"],
                    "assistant_message_id": "m_assistant_e2e_done",
                    "status": payload["status"],
                },
            )
        if path.endswith("/assistant-terminal"):
            assistant = _assistant_message()
            assistant["status"] = payload["terminal_status"]
            if payload.get("answer_text"):
                assistant["content"] = payload["answer_text"]
            assistant["metadata"] = {
                **(assistant.get("metadata") or {}),
                "task_id": payload["task_id"],
                "task_status": payload["terminal_status"],
                "terminal_status": payload["terminal_status"],
                "last_seq": int(payload["last_seq"]),
                "failure": payload.get("failure") or {},
            }
            if state["active_task_id"] == payload["task_id"]:
                state["active_task_id"] = None
            state["assistant_terminal_calls"].append(payload["terminal_status"])
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "conversation_id": 123,
                    "task_id": payload["task_id"],
                    "assistant_message_id": "m_assistant_e2e_done",
                    "status": payload["terminal_status"],
                },
            )
        if path == "/api/conversations/123":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "conversation_id": 123,
                        "user_id": 42,
                        "title": "refresh e2e done",
                        "message_count": len(state["messages"]),
                        "created_at": "2026-04-06T09:00:00+00:00",
                        "updated_at": "2026-04-06T09:05:00+00:00",
                        "messages": list(state["messages"]),
                        "uploaded_files": [],
                        "uploaded_files_all": [],
                        "pdf_files": [],
                        "excel_files": [],
                    },
                },
            )
        raise AssertionError(f"unexpected upstream path: {path}")

    _set_transport(handler)
    client = TestClient(app)
    auth_headers = {"Authorization": "Bearer demo"}

    create_response = client.post("/api/v1/tasks", json=_request_body(), headers=auth_headers)
    assert create_response.status_code == 200
    create_payload = create_response.json()
    task_id = create_payload["task_id"]
    assert task_id_holder["task_id"] == task_id
    assert create_payload["status"] == "queued"

    dispatcher = ExecutionAdmissionDispatcher(
        settings=app.state.settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-refresh-e2e-done",
        executor=qa_task_module.GatewayTaskExecutor(app).execute,
        timestamp_factory=lambda: "2026-04-06T10:00:05+00:00",
    )

    def _run_worker():
        worker_result_holder["result"] = worker.run_dispatch_cycle()

    worker_thread = threading.Thread(target=_run_worker, daemon=True)
    worker_thread.start()
    assert first_chunk_released.wait(timeout=5)

    first_replay = client.get(f"/api/v1/tasks/{task_id}/events", params={"after_seq": 0}, headers=auth_headers)
    assert first_replay.status_code == 200
    first_events = first_replay.json()["events"]
    assert [event["seq"] for event in first_events] == [1, 2, 3, 4]
    assert [event["type"] for event in first_events] == ["state", "state", "state", "content"]
    resume_after_seq = first_events[-1]["seq"] - 1

    detail_before_flush = client.get("/api/conversations/123", headers={"Authorization": "Bearer demo"})
    assert detail_before_flush.status_code == 200
    assistant_before_flush = detail_before_flush.json()["data"]["messages"][1]
    assert assistant_before_flush["content"] == ""
    assert assistant_before_flush["metadata"]["last_seq"] == 3

    resumed_stream_holder: dict[str, object] = {}

    def _finish_stream() -> None:
        time.sleep(0.05)
        allow_second_chunk.set()
        assert second_chunk_released.wait(timeout=5)
        allow_third_chunk.set()

    continue_thread = threading.Thread(target=_finish_stream, daemon=True)
    continue_thread.start()

    with TestClient(app).stream(
        "GET",
        f"/api/v1/tasks/{task_id}/events",
        params={"after_seq": resume_after_seq},
        headers={**auth_headers, "accept": "text/event-stream"},
    ) as response:
        resumed_stream_holder["status_code"] = response.status_code
        resumed_stream_holder["headers"] = dict(response.headers)
        resumed_stream_holder["body"] = b"".join(response.iter_bytes())

    continue_thread.join(timeout=5)
    assert not continue_thread.is_alive()
    worker_thread.join(timeout=5)
    assert not worker_thread.is_alive()

    result = worker_result_holder["result"]
    assert result.outcome == "completed"
    assert resumed_stream_holder["status_code"] == 200
    assert str((resumed_stream_holder["headers"] or {}).get("content-type") or "").startswith("text/event-stream")
    resumed_events = _sse_payloads(resumed_stream_holder["body"])
    assert [event["seq"] for event in resumed_events] == [4, 5, 6]
    assert resumed_events[0]["type"] == "content"
    assert resumed_events[1]["type"] == "content"
    assert resumed_events[2]["type"] == "done"
    assert resumed_events[2]["final_answer"] == "hello world"

    detail_response = client.get("/api/conversations/123", headers={"Authorization": "Bearer demo"})
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()["data"]
    assert detail_payload["active_task"] is None
    assert len(detail_payload["messages"]) == 2
    assert [message["role"] for message in detail_payload["messages"]] == ["user", "assistant"]
    assistant = detail_payload["messages"][1]
    assert assistant["message_id"] == "m_assistant_e2e_done"
    assert assistant["status"] == "completed"
    assert assistant["content"] == "hello world"
    assert assistant["metadata"]["terminal_status"] == "completed"
    assert state["user_create_calls"] == 1
    assert state["assistant_start_calls"] == 1
    assert state["assistant_terminal_calls"] == ["completed"]
    assert relay_store.describe_request(task_id)["latest_sequence"] == 6
