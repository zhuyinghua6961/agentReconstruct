from fastapi.testclient import TestClient

from server_fastapi.app import create_app



def test_fastapi_quota_routes_are_not_exposed():
    client = TestClient(create_app())

    assert client.get("/api/v1/quota/my").status_code == 404
    assert client.get("/api/v1/quota/configs").status_code == 404
    assert client.post("/api/v1/quota/reset/9/text_translate").status_code == 404
