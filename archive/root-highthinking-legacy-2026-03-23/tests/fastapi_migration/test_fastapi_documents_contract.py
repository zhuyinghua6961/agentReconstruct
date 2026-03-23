from fastapi.testclient import TestClient

from server_fastapi.app import create_app
from server_fastapi.auth.deps import AuthContext, require_auth_context


def test_fastapi_kb_info_contract(monkeypatch):
    monkeypatch.setattr(
        "server.services.system_service.get_collection_count",
        lambda: 123,
    )
    client = TestClient(create_app())
    response = client.get("/api/v1/kb_info")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "kb_size": 0,
        "chromadb_size": 123,
        "source_stats": {
            "neo4j": 0,
            "neo4j_connected": False,
            "chromadb": 123,
        },
    }


def test_fastapi_translate_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.documents.documents_service.translate",
        lambda *, texts, logger: (
            {
                "success": True,
                "data": {"translations": ["你好"], "count": 1, "cache_hits": 0, "provider": "test"},
                "translations": ["你好"],
                "count": 1,
                "cache_hits": 0,
            },
            200,
        ),
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)
    response = client.post("/api/v1/translate", json={"texts": ["hello"]})

    assert response.status_code == 200
    assert response.json()["translations"] == ["你好"]
    assert response.json()["data"]["count"] == 1


def test_fastapi_translate_requires_token():
    client = TestClient(create_app())
    response = client.post("/api/v1/translate", json={"texts": ["hello"]})

    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_MISSING"


def test_fastapi_summarize_pdf_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.documents.documents_service.summarize_pdf",
        lambda doi, logger: (
            {
                "success": True,
                "data": {"doi": doi, "summary": "demo summary"},
                "doi": doi,
                "summary": "demo summary",
            },
            200,
        ),
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=5, role="user", username="demo")
    client = TestClient(app)
    response = client.post("/api/v1/summarize_pdf/10.1000/demo")

    assert response.status_code == 200
    assert response.json()["summary"] == "demo summary"
    assert response.json()["data"]["doi"] == "10.1000/demo"


def test_fastapi_view_pdf_found_contract(tmp_path, monkeypatch):
    sample = tmp_path / "demo.pdf"
    sample.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        "server_fastapi.routers.documents.documents_service.view_pdf_path",
        lambda doi, logger: (
            {"success": True, "doi": doi, "filename": "demo.pdf"},
            200,
            sample,
        ),
    )

    client = TestClient(create_app())
    response = client.get("/api/v1/view_pdf/10.1000/demo")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4"
    assert response.headers["content-disposition"] == 'inline; filename="demo.pdf"'
