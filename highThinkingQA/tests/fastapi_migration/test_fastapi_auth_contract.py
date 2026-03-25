from fastapi.testclient import TestClient

from server_fastapi.app import create_app



def test_fastapi_auth_routes_are_not_exposed():
    client = TestClient(create_app())

    assert client.post("/api/v1/auth/login", json={"username": "demo", "password": "secret"}).status_code == 404
    assert client.get("/api/v1/auth/me").status_code == 404
