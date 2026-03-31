from dataclasses import replace

import pytest
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import GatewaySettings
from app.integrations.redis.service import RedisService
from app.services.execution_event_relay import ExecutionEventRelayStore
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


@pytest.fixture(autouse=True)
def _fresh_admission_state():
    previous_settings = app.state.settings
    previous_queue = app.state.execution_queue_status_store
    previous_relay = app.state.execution_event_relay_store
    previous_slot_leases = app.state.execution_slot_lease_store
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
    try:
        yield
    finally:
        app.state.settings = previous_settings
        app.state.execution_queue_status_store = previous_queue
        app.state.execution_event_relay_store = previous_relay
        app.state.execution_slot_lease_store = previous_slot_leases
        app.state.proxy_service.set_transport(None)


def _set_health_transport() -> None:
    app.state.proxy_service.set_transport(
        httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok", "path": request.url.path}))
    )


def _set_admission_settings(*, environment: str, token: str = "") -> None:
    settings: GatewaySettings = app.state.settings
    app.state.settings = replace(
        settings,
        environment=environment,
        admission=replace(settings.admission, control_api_token=token),
    )


def test_admission_status_exposes_live_store_counts():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    slot_store = app.state.execution_slot_lease_store
    queue_store.put_request(
        {"request_id": "req_status", "status": "queued", "enqueued_at": "1970-01-01T00:01:10+00:00"},
        ttl_seconds=900,
    )
    relay_store.append_frame("req_status", {"type": "metadata"}, ttl_seconds=600)
    slot_store.acquire(
        request_id="req_live",
        capacity_key="fast_or_patent",
        owner_id="worker_a",
        ttl_seconds=30,
        acquired_at="1970-01-01T00:01:30+00:00",
    )

    client = TestClient(app)
    response = client.get("/api/admission/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["queue_status_store"]["queued_requests"] == 1
    assert payload["event_relay_store"]["frames_tracked"] == 1
    assert payload["slot_lease_store"]["active_leases"] == 1
    assert payload["admission"]["queue_metrics"]["backlog"] == 1


def test_admission_request_detail_exposes_request_result_and_relay_state():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    relay_store = app.state.execution_event_relay_store
    queue_store.put_request(
        {"request_id": "req_detail", "status": "completed", "actual_mode": "fast"},
        ttl_seconds=900,
    )
    queue_store.put_result("req_detail", {"answer": "ok"}, ttl_seconds=600)
    relay_store.append_frame("req_detail", {"type": "metadata"}, ttl_seconds=600)
    relay_store.append_frame("req_detail", {"type": "done"}, ttl_seconds=600)

    client = TestClient(app)
    response = client.get("/api/admission/requests/req_detail")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["request"]["actual_mode"] == "fast"
    assert payload["result"] == {"answer": "ok"}
    assert payload["relay"]["latest_sequence"] == 2


def test_admission_cancel_updates_only_queued_requests():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {"request_id": "req_cancel_api", "status": "queued", "cancel_allowed": True},
        ttl_seconds=900,
    )

    client = TestClient(app)
    response = client.post("/api/admission/requests/req_cancel_api/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["request"]["status"] == "cancelled"


def test_admission_cancel_rejects_non_cancellable_queue_record():
    _set_health_transport()
    queue_store = app.state.execution_queue_status_store
    queue_store.put_request(
        {"request_id": "req_locked_api", "status": "queued", "cancel_allowed": False},
        ttl_seconds=900,
    )

    client = TestClient(app)
    response = client.post("/api/admission/requests/req_locked_api/cancel")

    assert response.status_code == 409


def test_admission_frames_support_after_sequence():
    _set_health_transport()
    relay_store = app.state.execution_event_relay_store
    relay_store.append_frame("req_frames", {"type": "metadata"}, ttl_seconds=600)
    relay_store.append_frame("req_frames", {"type": "content", "content": "hello"}, ttl_seconds=600)

    client = TestClient(app)
    response = client.get("/api/admission/requests/req_frames/frames", params={"after_sequence": 1})

    assert response.status_code == 200
    payload = response.json()
    assert [item["sequence"] for item in payload["frames"]] == [2]


def test_admission_api_rejects_unauthorized_prod_access():
    _set_health_transport()
    _set_admission_settings(environment="prod", token="prod-secret")
    client = TestClient(app)

    response = client.get("/api/admission/status")

    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "ADMISSION_CONTROL_FORBIDDEN"


def test_admission_api_allows_token_authorized_prod_access():
    _set_health_transport()
    _set_admission_settings(environment="prod", token="prod-secret")
    client = TestClient(app)

    response = client.get(
        "/api/admission/status",
        headers={"X-Admission-Control-Token": "prod-secret"},
    )

    assert response.status_code == 200
