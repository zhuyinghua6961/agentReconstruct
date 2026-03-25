from fastapi.testclient import TestClient

from server_fastapi.app import create_app



def test_fastapi_health_contract():
    client = TestClient(create_app())
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["service"] == "highThinking-api"
    assert payload["version"] == "v1"
    assert payload["status"] == "ok"
    assert str(payload["trace_id"]).startswith("req_")
    assert str(response.headers["X-Trace-ID"]).startswith("req_")



def test_fastapi_ingest_and_documents_routes_are_not_exposed():
    client = TestClient(create_app())

    assert client.post("/api/v1/ingest", json={"parse_method": "unknown"}).status_code == 404
    assert client.get("/api/v1/view_pdf/10.1000/not-found").status_code == 404
