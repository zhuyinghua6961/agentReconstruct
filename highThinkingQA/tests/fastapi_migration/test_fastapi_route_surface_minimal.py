from fastapi.testclient import TestClient

from server_fastapi.app import create_app


def test_fastapi_route_surface_is_minimal_for_thinking_service():
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/health" in paths
    assert "/api/v1/ask" in paths
    assert "/api/v1/ask_stream" in paths
    assert "/api/v1/{mode}/ask" in paths
    assert "/api/v1/{mode}/ask_stream" in paths

    assert "/api/v1/upload_pdf" not in paths
    assert "/api/v1/upload_excel" not in paths
    assert "/api/v1/conversations" not in paths
    assert "/api/v1/view_pdf/{doi:path}" not in paths
    assert "/api/v1/quota/my" not in paths
    assert "/api/v1/auth/login" not in paths
    assert "/api/admin/users" not in paths
    assert "/api/v1/ingest" not in paths



def test_removed_legacy_routes_return_404():
    client = TestClient(create_app())

    assert client.post("/api/v1/upload_pdf").status_code == 404
    assert client.get("/api/v1/conversations").status_code == 404
    assert client.get("/api/v1/view_pdf/10.1000/demo").status_code == 404
    assert client.get("/api/v1/quota/my").status_code == 404
    assert client.post("/api/v1/auth/login", json={"username": "demo", "password": "secret"}).status_code == 404
    assert client.post("/api/v1/ingest", json={"parse_method": "unknown"}).status_code == 404
