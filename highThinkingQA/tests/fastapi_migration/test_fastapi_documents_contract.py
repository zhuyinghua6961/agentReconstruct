from fastapi.testclient import TestClient

from server_fastapi.app import create_app



def test_fastapi_document_routes_are_not_exposed():
    client = TestClient(create_app())

    assert client.get("/api/v1/kb_info").status_code == 404
    assert client.post("/api/v1/translate", json={"texts": ["hello"]}).status_code == 404
    assert client.post("/api/v1/summarize_pdf/10.1000/demo").status_code == 404
    assert client.get("/api/v1/view_pdf/10.1000/demo").status_code == 404
