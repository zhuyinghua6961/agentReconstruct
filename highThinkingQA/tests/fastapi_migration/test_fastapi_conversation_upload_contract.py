from fastapi.testclient import TestClient

from server_fastapi.app import create_app



def test_fastapi_conversation_and_upload_routes_are_not_exposed():
    client = TestClient(create_app())

    assert client.get("/api/v1/conversations").status_code == 404
    assert client.post("/api/v1/conversations", json={"title": "demo"}).status_code == 404
    assert client.get("/api/v1/conversations/3/files/5/download").status_code == 404
    assert client.post("/api/v1/upload_pdf").status_code == 404
    assert client.post("/api/v1/upload_excel").status_code == 404
