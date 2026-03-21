import httpx
from fastapi.testclient import TestClient

from app.main import app


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
    assert set(payload["backends"].keys()) == {"public", "fast", "thinking", "patent"}
    assert "backend_config_warnings" in payload
    assert payload["upstreams"]["public"]["ok"] is True
    assert payload["upstreams"]["thinking"]["payload"]["status"] == "ok"
    app.state.proxy_service.set_transport(None)
