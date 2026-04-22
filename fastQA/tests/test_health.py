import json
from types import SimpleNamespace

from app.main import app
from app.routers.health import healthz


def test_healthz_exposes_service_roots():
    request = SimpleNamespace(app=app, url=SimpleNamespace(path="/healthz"))

    response = healthz(request)
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["service"] == "fastQA"
    assert payload["api_prefix"] == "/api"
    assert payload["service_runtime_root"]
    assert "generation_runtime" in payload["components"]
    assert payload["runtime_mode"] in {"placeholder", "generation"}
    assert payload["supported_routes"] == ["kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"]
    assert payload["ask_stream_max_concurrent"] >= 1
    assert "graph_kb" in payload["components"]
    assert "graph_kb_enabled" in payload
    assert "graph_kb_ready" in payload


def test_api_health_reflects_runtime_readiness():
    request = SimpleNamespace(app=app, url=SimpleNamespace(path="/api/health"))

    response = healthz(request)
    payload = json.loads(response.body)

    expected_ready = bool(getattr(app.state, "generation_runtime_ready", False))
    assert response.status_code == (200 if expected_ready else 503)
    assert payload["success"] is expected_ready
    assert payload["runtime_mode"] == ("generation" if expected_ready else "placeholder")
    assert payload["graph_kb_ready"] is bool(getattr(app.state, "graph_kb_ready", False))


def _make_health_request(
    *,
    path: str,
    generation_runtime_ready: bool,
    shared_llm_pool_status: dict[str, object],
):
    settings = SimpleNamespace(
        app_env="test",
        api_prefix="/api",
        generation_runtime_enabled=True,
        graph_kb_enabled=False,
        allow_placeholder_fallback=True,
        file_context_fallback_enabled=True,
        ask_stream_max_concurrent=20,
        sse_heartbeat_sec=15,
    )
    state = SimpleNamespace(
        settings=settings,
        component_status={
            "redis": {"status": "ok"},
            "generation_runtime": {"status": "ok" if generation_runtime_ready else "degraded"},
            "graph_kb": {"status": "skipped"},
            "shared_llm_pool": dict(shared_llm_pool_status),
        },
        generation_runtime_ready=generation_runtime_ready,
        graph_kb_ready=False,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), url=SimpleNamespace(path=path))


def test_healthz_exposes_shared_llm_pool_component():
    request = _make_health_request(
        path="/healthz",
        generation_runtime_ready=True,
        shared_llm_pool_status={
            "status": "ok",
            "ready": True,
            "pool_owner": "app",
            "client_owner": "shared",
            "shared_client_id": "shared-1",
            "bootstrap_source": "startup",
            "max_connections": 160,
            "max_keepalive_connections": 64,
            "keepalive_expiry_seconds": 90.0,
        },
    )

    response = healthz(request)
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["components"]["shared_llm_pool"]["status"] == "ok"
    assert payload["components"]["shared_llm_pool"]["ready"] is True
    assert payload["components"]["shared_llm_pool"]["client_owner"] == "shared"
    assert payload["components"]["shared_llm_pool"]["max_connections"] == 160


def test_healthz_marks_shared_llm_pool_skipped_when_disabled():
    request = _make_health_request(
        path="/api/health",
        generation_runtime_ready=True,
        shared_llm_pool_status={
            "status": "skipped",
            "ready": False,
            "pool_owner": "app",
            "client_owner": "private",
            "bootstrap_source": "startup",
            "max_connections": 160,
            "max_keepalive_connections": 64,
            "keepalive_expiry_seconds": 90.0,
        },
    )

    response = healthz(request)
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["components"]["shared_llm_pool"]["status"] == "skipped"
    assert payload["components"]["shared_llm_pool"]["ready"] is False


def test_healthz_shared_llm_pool_ready_is_true_only_for_ok_status():
    ok_request = _make_health_request(
        path="/healthz",
        generation_runtime_ready=True,
        shared_llm_pool_status={"status": "ok", "ready": True},
    )
    degraded_request = _make_health_request(
        path="/healthz",
        generation_runtime_ready=True,
        shared_llm_pool_status={"status": "degraded", "ready": False},
    )

    ok_payload = json.loads(healthz(ok_request).body)
    degraded_payload = json.loads(healthz(degraded_request).body)

    assert ok_payload["components"]["shared_llm_pool"]["ready"] is True
    assert degraded_payload["components"]["shared_llm_pool"]["ready"] is False


def test_api_health_stays_ready_when_shared_llm_pool_is_degraded_but_generation_runtime_is_ready():
    request = _make_health_request(
        path="/api/health",
        generation_runtime_ready=True,
        shared_llm_pool_status={
            "status": "degraded",
            "ready": False,
            "pool_owner": "app",
            "client_owner": "private",
            "bootstrap_source": "startup",
        },
    )

    response = healthz(request)
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["generation_runtime_ready"] is True
    assert payload["components"]["shared_llm_pool"]["status"] == "degraded"


def test_healthz_prefers_live_shared_pool_snapshot_for_dynamic_metrics():
    request = _make_health_request(
        path="/healthz",
        generation_runtime_ready=True,
        shared_llm_pool_status={
            "status": "ok",
            "ready": True,
            "pool_owner": "app",
            "client_owner": "shared",
            "shared_client_id": "stale-client",
            "bootstrap_source": "startup",
            "pool_timeout_count": 1,
            "pool_wait_ms": 2.0,
            "max_connections": 160,
            "max_keepalive_connections": 64,
            "keepalive_expiry_seconds": 90.0,
        },
    )
    request.app.state.shared_llm_http_pool = SimpleNamespace(
        snapshot=lambda: {
            "shared_client_id": "live-client",
            "pid": 12345,
            "bootstrap_source": "startup",
            "pool_timeout_count": 7,
            "pool_wait_ms": 88.5,
            "max_connections": 192,
            "max_keepalive_connections": 96,
            "keepalive_expiry_seconds": 120.0,
        }
    )

    payload = json.loads(healthz(request).body)

    shared = payload["components"]["shared_llm_pool"]
    assert shared["status"] == "ok"
    assert shared["ready"] is True
    assert shared["shared_client_id"] == "live-client"
    assert shared["pool_timeout_count"] == 7
    assert shared["pool_wait_ms"] == 88.5
    assert shared["max_connections"] == 192
    assert shared["max_keepalive_connections"] == 96
    assert shared["keepalive_expiry_seconds"] == 120.0
