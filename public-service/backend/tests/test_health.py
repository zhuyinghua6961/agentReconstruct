from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.runtime import create_runtime
from app.main import app
from app.modules.qa_cache.metrics import reset_cache_metrics
from app.modules.system.service import system_service


def test_health_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/health" in paths
    assert "/api/health" in paths
    assert "/api/v1/health" in paths
    assert "/api/background_status" in paths


def test_health_returns_runtime_payload():
    reset_cache_metrics()
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"healthy", "starting", "degraded"}
    assert "qa_cache" in payload
    assert "components" in payload


def test_health_marks_pending_components_as_starting():
    runtime = create_runtime(get_settings())
    runtime.component_status["database"] = {"status": "ok"}
    runtime.component_status["redis"] = {"status": "ok"}
    runtime.component_status["auth"] = {"status": "ok"}
    runtime.component_status["quota"] = {"status": "ok"}
    runtime.component_status["storage"] = {"status": "ok"}
    runtime.component_status["conversation"] = {"status": "ok"}
    runtime.component_status["retrieval"] = {"status": "ok"}
    runtime.component_status["agent"] = {"status": "ok"}
    runtime.component_status["upload_processing"] = {"status": "ok"}
    runtime.component_status["bootstrap"] = {"status": "pending"}

    payload = system_service.build_health(runtime)

    assert payload["status"] == "starting"


def test_health_degraded_when_conversation_outbox_disabled():
    runtime = create_runtime(get_settings())
    runtime.component_status["conversation_outbox"] = {
        "status": "degraded",
        "detail": "conversation outbox disabled; required table missing",
        "enabled": False,
        "table_exists": False,
    }

    payload = system_service.build_health(runtime)

    assert payload["status"] == "degraded"
    assert payload["components"]["conversation_outbox"]["enabled"] is False
