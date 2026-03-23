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


def test_fastapi_ingest_validation_contract():
    client = TestClient(create_app())
    response = client.post("/api/v1/ingest", json={"parse_method": "unknown"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "VALIDATION_ERROR"


def test_fastapi_documents_not_found_contract():
    client = TestClient(create_app())
    response = client.get("/api/v1/view_pdf/10.1000/not-found")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": "pdf_not_found",
        "code": "NOT_FOUND",
        "doi": "10.1000/not-found",
    }
