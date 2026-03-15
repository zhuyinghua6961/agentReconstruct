import io

from fastapi.testclient import TestClient

from server_fastapi.app import create_app
from server_fastapi.auth.deps import AuthContext, require_auth_context


def test_fastapi_conversation_requires_token():
    client = TestClient(create_app())
    response = client.get("/api/v1/conversations")

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "TOKEN_MISSING"


def test_fastapi_create_conversation_uses_auth_context(monkeypatch):
    def fake_create_conversation(*, user_id, title):
        assert user_id == 7
        assert title == "demo"
        return {"success": True, "data": {"conversation_id": 11, "title": title}}

    monkeypatch.setattr(
        "server_fastapi.routers.conversation.conversation_service.create_conversation",
        fake_create_conversation,
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)
    response = client.post("/api/v1/conversations", json={"title": "demo"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"conversation_id": 11, "title": "demo"}}


def test_fastapi_conversation_download_local_file(tmp_path, monkeypatch):
    sample = tmp_path / "demo.pdf"
    sample.write_bytes(b"pdf")

    def fake_get_uploaded_file(*, user_id, conversation_id, file_id):
        assert user_id == 9
        assert conversation_id == 3
        assert file_id == 5
        return {
            "success": True,
            "data": {
                "file_name": "demo.pdf",
                "local_path": str(sample),
                "storage_ref": "",
            },
        }

    monkeypatch.setattr(
        "server_fastapi.routers.conversation.conversation_service.get_uploaded_file",
        fake_get_uploaded_file,
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=9, role="user", username="demo")
    client = TestClient(app)
    response = client.get("/api/v1/conversations/3/files/5/download")

    assert response.status_code == 200
    assert response.content == b"pdf"
    assert 'attachment; filename="demo.pdf"' in response.headers["content-disposition"]


def test_fastapi_upload_missing_file_contracts():
    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=8, role="user", username="demo")
    client = TestClient(app)
    pdf_resp = client.post("/api/v1/upload_pdf")
    excel_resp = client.post("/api/v1/upload_excel")

    assert pdf_resp.status_code == 200
    assert pdf_resp.json() == {"error": "没有文件被上传"}

    assert excel_resp.status_code == 200
    assert excel_resp.json() == {"error": "没有文件被上传"}


def test_fastapi_upload_invalid_extension_contracts():
    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=8, role="user", username="demo")
    client = TestClient(app)

    bad_pdf = client.post(
        "/api/v1/upload_pdf",
        files={"file": ("demo.txt", io.BytesIO(b"not-pdf"), "text/plain")},
    )
    bad_excel = client.post(
        "/api/v1/upload_excel",
        files={"file": ("demo.txt", io.BytesIO(b"not-excel"), "text/plain")},
    )

    assert bad_pdf.status_code == 200
    assert bad_pdf.json() == {"error": "只支持PDF文件"}

    assert bad_excel.status_code == 200
    assert bad_excel.json() == {"error": "只支持 Excel 或 CSV 文件 (.xls/.xlsx/.csv)"}


def test_fastapi_upload_pdf_success_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))

    monkeypatch.setattr(
        "server_fastapi.routers.upload.mirror_file_to_object_storage",
        lambda **_kwargs: "minio://bucket/uploads/pdf/demo.pdf",
    )
    monkeypatch.setattr(
        "server_fastapi.routers.upload.conversation_service.add_uploaded_file",
        lambda **_kwargs: {"success": True, "data": {"file_id": 21}},
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=8, role="user", username="demo")
    client = TestClient(app)
    response = client.post(
        "/api/v1/upload_pdf",
        files={"file": ("demo.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        data={"conversation_id": "12"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "demo.pdf"
    assert payload["content_type"] == "application/pdf"
    assert payload["file_id"] == 21
    assert payload["parse_status"] == "uploaded"
    assert payload["storage_ref"] == "minio://bucket/uploads/pdf/demo.pdf"
