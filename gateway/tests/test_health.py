import pytest
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.integrations.redis.service import GatewayRedisRuntimeStatus
from app.integrations.redis.service import RedisService
from app.services.execution_event_relay import ExecutionEventRelayStore
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


@pytest.fixture(autouse=True)
def _fresh_admission_state():
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
        app.state.execution_queue_status_store = previous_queue
        app.state.execution_event_relay_store = previous_relay
        app.state.execution_slot_lease_store = previous_slot_leases
        app.state.proxy_service.set_transport(None)


def test_healthz_contains_backend_registry_and_upstreams():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "path": request.url.path})

    app.state.proxy_service.set_transport(httpx.MockTransport(handler))
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["conversation_file_provider"] == "noop"
    assert payload["runtime_role"] == "web"
    assert payload["components"]["redis"]["enabled"] is True
    assert payload["components"]["admission"]["worker_script_supported"] is True
    assert payload["components"]["admission"]["request_path_cutover_enabled"] is True
    assert payload["components"]["admission"]["shared_state_ready"] is True
    assert payload["components"]["queue_status_store"]["storage_mode"] == "memory_fallback"
    assert payload["components"]["event_relay_store"]["storage_mode"] == "memory_fallback"
    assert set(payload["backends"].keys()) == {"public", "fast", "thinking", "patent"}
    assert "backend_config_warnings" in payload
    assert payload["upstreams"]["public"]["ok"] is True
    assert payload["upstreams"]["public"]["payload"]["path"] == "/health"
    assert payload["upstreams"]["fast"]["payload"]["path"] == "/api/health"
    assert payload["upstreams"]["thinking"]["payload"]["status"] == "ok"
    assert payload["upstreams"]["thinking"]["payload"]["path"] == "/api/health"
    assert payload["upstreams"]["patent"]["payload"]["path"] == "/api/health"
    app.state.proxy_service.set_transport(None)


def test_healthz_refreshes_live_admission_store_counts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "path": request.url.path})

    app.state.proxy_service.set_transport(httpx.MockTransport(handler))
    app.state.execution_queue_status_store.put_request(
        {"request_id": "req_health", "status": "queued", "enqueued_at": "1970-01-01T00:01:10+00:00"},
        ttl_seconds=900,
    )
    app.state.execution_event_relay_store.append_frame(
        "req_health",
        {"type": "metadata"},
        ttl_seconds=600,
    )
    app.state.execution_slot_lease_store.acquire(
        request_id="req_admitted",
        capacity_key="thinking",
        owner_id="worker_a",
        ttl_seconds=30,
        acquired_at="1970-01-01T00:01:30+00:00",
    )
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["queue_status_store"]["queued_requests"] == 1
    assert payload["components"]["event_relay_store"]["frames_tracked"] == 1
    assert payload["components"]["slot_lease_store"]["active_leases"] == 1
    assert payload["components"]["admission"]["queue_metrics"]["backlog"] == 1
    app.state.proxy_service.set_transport(None)


def test_healthz_refreshes_live_redis_component_status():
    class _DeadRedis:
        def ping(self):
            raise RuntimeError("redis down")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "path": request.url.path})

    previous_runtime = app.state.redis_runtime
    app.state.proxy_service.set_transport(httpx.MockTransport(handler))
    app.state.redis_runtime = type(previous_runtime)(
        client=_DeadRedis(),
        service=RedisService.from_prefix(client=_DeadRedis(), key_prefix="gateway"),
        status=GatewayRedisRuntimeStatus(
            enabled=True,
            available=True,
            dependency_available=True,
            client_source="host_port",
            key_prefix="gateway",
        ),
    )
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["redis"]["available"] is True
    assert payload["components"]["redis"]["live_available"] is False
    app.state.redis_runtime = previous_runtime
    app.state.proxy_service.set_transport(None)
