import io
import importlib
from pathlib import Path

import config as config_module
from fastapi.testclient import TestClient

import server_fastapi.app as app_module
from server_fastapi.auth.deps import AuthContext, require_auth_context


def _create_app():
    importlib.reload(config_module)
    importlib.reload(app_module)
    return app_module.create_app()


def test_fastapi_conversation_requires_token():
    client = TestClient(_create_app())
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

    app = _create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)
    response = client.post("/api/v1/conversations", json={"title": "demo"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"conversation_id": 11, "title": "demo"}}


def test_fastapi_conversation_detail_includes_summary(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.conversation.conversation_service.get_conversation_detail",
        lambda **_kwargs: {
            "success": True,
            "data": {
                "conversation_id": 11,
                "user_id": 7,
                "title": "demo",
                "message_count": 2,
                "created_at": "2026-03-17T10:00:00+08:00",
                "updated_at": "2026-03-17T10:02:00+08:00",
                "messages": [],
                "summary": {"topic": "磷酸铁锂", "recent_focus": "低温性能"},
                "uploaded_files": [],
                "uploaded_files_all": [],
                "pdf_files": [],
                "excel_files": [],
            },
        },
    )

    app = _create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="demo")
    client = TestClient(app)
    response = client.get("/api/v1/conversations/11")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["summary"]["topic"] == "磷酸铁锂"


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

    app = _create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=9, role="user", username="demo")
    client = TestClient(app)
    response = client.get("/api/v1/conversations/3/files/5/download")

    assert response.status_code == 200
    assert response.content == b"pdf"
    assert 'attachment; filename="demo.pdf"' in response.headers["content-disposition"]


def test_fastapi_upload_missing_file_contracts():
    app = _create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=8, role="user", username="demo")
    client = TestClient(app)
    pdf_resp = client.post("/api/v1/upload_pdf")
    excel_resp = client.post("/api/v1/upload_excel")

    assert pdf_resp.status_code == 200
    assert pdf_resp.json() == {"error": "没有文件被上传"}

    assert excel_resp.status_code == 200
    assert excel_resp.json() == {"error": "没有文件被上传"}


def test_fastapi_upload_invalid_extension_contracts():
    app = _create_app()
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

    app = _create_app()
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


def test_fastapi_upload_pdf_defaults_to_service_state_root(tmp_path, monkeypatch):
    state_root = (tmp_path / "state").resolve()
    captured: dict[str, str] = {}

    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_STATE_ROOT", str(state_root))
    monkeypatch.delenv("UPLOAD_DIR", raising=False)

    def fake_mirror_file_to_object_storage(**kwargs):
        captured["mirror_local_path"] = str(kwargs["local_path"])
        return "minio://bucket/uploads/pdf/default-root.pdf"

    def fake_add_uploaded_file(**kwargs):
        captured["persist_local_path"] = str(kwargs["local_path"])
        return {"success": True, "data": {"file_id": 22}}

    monkeypatch.setattr(
        "server_fastapi.routers.upload.mirror_file_to_object_storage",
        fake_mirror_file_to_object_storage,
    )
    monkeypatch.setattr(
        "server_fastapi.routers.upload.conversation_service.add_uploaded_file",
        fake_add_uploaded_file,
    )

    app = _create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=8, role="user", username="demo")
    client = TestClient(app)
    response = client.post(
        "/api/v1/upload_pdf",
        files={"file": ("default-root.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        data={"conversation_id": "12"},
    )

    assert response.status_code == 200
    payload = response.json()
    file_path = Path(payload["filepath"]).resolve()
    assert str(file_path).startswith(str((state_root / "uploads" / "pdf").resolve()))
    assert file_path.exists()
    assert payload["file_id"] == 22
    assert captured["persist_local_path"] == str(file_path)
    assert captured["mirror_local_path"] == str(file_path)
