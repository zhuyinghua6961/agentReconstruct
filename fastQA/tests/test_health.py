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


def test_api_health_reflects_runtime_readiness():
    request = SimpleNamespace(app=app, url=SimpleNamespace(path="/api/health"))

    response = healthz(request)
    payload = json.loads(response.body)

    expected_ready = bool(getattr(app.state, "generation_runtime_ready", False))
    assert response.status_code == (200 if expected_ready else 503)
    assert payload["success"] is expected_ready
    assert payload["runtime_mode"] == ("generation" if expected_ready else "placeholder")
