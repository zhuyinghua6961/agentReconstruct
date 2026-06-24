import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.patent_proxy import router as patent_proxy_router


class _TransportGuard:
    def __init__(self, handler):
        self._transport = httpx.MockTransport(handler)

    def __enter__(self):
        app.state.proxy_service.set_transport(self._transport)
        app.state.quota_proxy_service.set_transport(self._transport)
        return self

    def __exit__(self, exc_type, exc, tb):
        app.state.proxy_service.set_transport(None)
        app.state.quota_proxy_service.set_transport(None)
        return False


def test_patent_proxy_registers_patent_search_routes():
    methods_by_path = {
        route.path: set(getattr(route, "methods", set())) - {"HEAD"}
        for route in patent_proxy_router.routes
    }
    assert methods_by_path["/api/patent_search"] == {"GET", "POST"}
    assert methods_by_path["/api/v1/patent_search"] == {"GET", "POST"}


def test_patent_proxy_forwards_json_to_patent_backend(monkeypatch):
    class _Auth:
        async def require_auth_context(self, request):
            from app.core.auth import AuthContext

            return AuthContext(user_id=7)

    monkeypatch.setattr(app.state, "gateway_auth_service", _Auth())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/internal/quota/grants/precheck"):
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-1"}})
        if "/finalize" in request.url.path:
            return httpx.Response(200, json={"success": True, "data": {"counted": True}})
        assert request.method == "GET"
        assert request.url.path == "/api/patent_search"
        assert request.url.query == b"query=battery"
        return httpx.Response(
            200,
            json={
                "items": [{"canonical_patent_id": "CN123456789A"}],
                "count": 1,
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get(
            "/api/patent_search?query=battery",
            headers={"Authorization": "Bearer demo"},
        )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["quota"]["counted"] is True
    assert response.headers["x-gateway-backend"] == "patent"


def test_patent_proxy_skips_quota_without_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in {key.lower() for key in request.headers.keys()}
        return httpx.Response(200, json={"items": [], "count": 0})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get("/api/patent_search?query=battery")

    assert response.status_code == 200
    assert "quota" not in response.json()
