from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.core.deps import AuthContext
from app.main import app
from app.modules.auth.deps import get_optional_auth_context
from app.modules.conversation import service as conversation_service_module
from app.modules.quota import deps as quota_deps
from app.modules.quota import service as quota_service_module
from app.modules.storage.service import storage_service
from app.integrations.redis import RedisService


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = int(ex)
        return True

    def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
                self.values.pop(key, None)
                self.expirations.pop(key, None)
        return deleted

    def expire(self, key: str, seconds: int):
        if key not in self.values:
            return False
        self.expirations[key] = int(seconds)
        return True


def test_upload_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/upload_pdf" in paths
    assert "/api/v1/upload_pdf" in paths
    assert "/api/upload_excel" in paths
    assert "/api/v1/clear_pdf" in paths


def test_upload_pdf_success_binds_conversation(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            call_order: list[str] = []
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: call_order.append("add_uploaded_file") or {"success": True, "data": {"file_id": 8}},
            )
            monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "local://mirrored")

            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("sample.pdf", b"pdf-data", "application/pdf")},
                data={"conversation_id": "12"},
            )

            client.app.dependency_overrides.clear()

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["file_id"] == 8
        assert payload["storage_ref"] == "local://mirrored"
        assert Path(payload["filepath"]).exists()
        assert client.app.state.runtime.current_pdf_path is None
        assert call_order == ["add_uploaded_file"]


def test_upload_pdf_sanitizes_client_filename(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            mirrored: dict[str, object] = {}
            monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
            monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})
            monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: {"success": True, "data": {"file_id": 8}},
            )
            monkeypatch.setattr(
                storage_service,
                "mirror_file",
                lambda **kwargs: mirrored.update(kwargs) or "local://mirrored",
            )

            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("../../evil.pdf", b"pdf-data", "application/pdf")},
                data={"conversation_id": "12"},
            )

            client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["filename"] == "evil.pdf"
    assert Path(payload["filepath"]).parent == Path(tempdir)
    assert ".." not in Path(payload["filepath"]).name
    assert str(mirrored.get("object_name")).endswith("evil.pdf")
    assert ".." not in str(mirrored.get("object_name"))


def test_upload_pdf_without_conversation_context_returns_400_and_does_not_count_quota(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            mirrored = {"called": False}
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            quota_called = {"check": 0, "increment": 0}
            monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
            monkeypatch.setattr(
                quota_service_module.quota_service,
                "check_quota",
                lambda **kwargs: quota_called.__setitem__("check", quota_called["check"] + 1) or {"success": True, "allowed": True},
            )
            monkeypatch.setattr(
                quota_service_module.quota_service,
                "increment_quota",
                lambda **kwargs: quota_called.__setitem__("increment", quota_called["increment"] + 1) or {"success": True},
            )
            monkeypatch.setattr(
                storage_service,
                "mirror_file",
                lambda **kwargs: mirrored.__setitem__("called", True) or "local://mirrored",
            )
            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("sample.pdf", b"pdf-data", "application/pdf")},
            )
            client.app.dependency_overrides.clear()

        assert response.status_code == 400
        payload = response.json()
        assert payload == {
            "success": False,
            "error": "缺少会话上下文，无法关联上传文件",
            "code": "UPLOAD_CONVERSATION_CONTEXT_REQUIRED",
        }
        assert quota_called["check"] == 0
        assert quota_called["increment"] == 0
        assert mirrored["called"] is False
        assert list(Path(tempdir).iterdir()) == []


def test_upload_pdf_fails_when_storage_mirror_is_unavailable(monkeypatch):
    remaining_files: list[Path] = []
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            add_called = {"count": 0}
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: add_called.__setitem__("count", add_called["count"] + 1) or {"success": True, "data": {"file_id": 8}},
            )
            monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: None)

            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("sample.pdf", b"pdf-data", "application/pdf")},
                data={"conversation_id": "12"},
            )

            remaining_files = list(Path(tempdir).iterdir())
            client.app.dependency_overrides.clear()

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "UPLOAD_STORAGE_UNAVAILABLE"
    assert add_called["count"] == 0
    assert remaining_files == []


def test_upload_pdf_does_not_consume_user_visible_upload_quota(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            quota_calls = {"check": 0, "increment": 0}
            monkeypatch.setattr(
                quota_service_module.quota_service,
                "check_quota",
                lambda **kwargs: quota_calls.__setitem__("check", quota_calls["check"] + 1) or {"success": True, "allowed": True},
            )
            monkeypatch.setattr(
                quota_service_module.quota_service,
                "increment_quota",
                lambda **kwargs: quota_calls.__setitem__("increment", quota_calls["increment"] + 1) or {"success": True},
            )
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: {"success": True, "data": {"file_id": 8}},
            )
            monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "local://mirrored")

            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("sample.pdf", b"pdf-data", "application/pdf")},
                data={"conversation_id": "12"},
            )

            client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["file_id"] == 8
    assert "quota_counted" not in payload
    assert "quota_warning" not in payload
    assert quota_calls == {"check": 0, "increment": 0}


def test_upload_excel_success_returns_frontend_required_fields(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: {"success": True, "data": {"file_id": 18}},
            )
            monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "local://excel-mirrored")

            response = client.post(
                "/api/v1/upload_excel",
                files={"file": ("sample.xlsx", b"excel-data", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"conversation_id": "12"},
            )

            client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["file_id"] == 18
    assert payload["filename"] == "sample.xlsx"
    assert payload["storage_ref"] == "local://excel-mirrored"
    assert Path(payload["filepath"]).name.endswith("sample.xlsx")
    assert payload["parse_status"] == "uploaded"
    assert payload["index_status"] == "pending"
    assert payload["processing_stage"] == "uploaded"
    assert payload["conversation_bound"] is True


def test_upload_pdf_persist_failure_cleans_orphaned_file(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            cleaned: dict[str, object] = {}
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: {"success": False, "code": "DB_UNAVAILABLE"},
            )
            monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "local://mirrored")
            monkeypatch.setattr(
                storage_service,
                "cleanup_resources",
                lambda **kwargs: cleaned.update(kwargs) or {"local_deleted": True, "storage_deleted": True, "errors": []},
            )

            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("sample.pdf", b"pdf-data", "application/pdf")},
                data={"conversation_id": "12"},
            )

            client.app.dependency_overrides.clear()

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "DB_UNAVAILABLE"
    assert str((cleaned.get("file_row") or {}).get("storage_ref")) == "local://mirrored"


def test_upload_pdf_persist_failure_with_path_like_filename_still_cleans_safely(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            cleaned: dict[str, object] = {}
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: {"success": False, "code": "DB_UNAVAILABLE"},
            )
            monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "local://mirrored")
            monkeypatch.setattr(
                storage_service,
                "cleanup_resources",
                lambda **kwargs: cleaned.update(kwargs) or {"local_deleted": True, "storage_deleted": True, "errors": []},
            )

            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("..\\..\\evil.pdf", b"pdf-data", "application/pdf")},
                data={"conversation_id": "12"},
            )

            client.app.dependency_overrides.clear()

    assert response.status_code == 503
    cleaned_local_path = str((cleaned.get("file_row") or {}).get("local_path") or "")
    assert cleaned_local_path
    assert str(Path(cleaned_local_path).parent) == tempdir


def test_upload_pdf_does_not_create_upload_quota_lease(monkeypatch):
    with TemporaryDirectory() as tempdir:
        with TestClient(app) as client:
            client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
                user_id=7,
                role="user",
                username="alice",
            )
            client.app.state.runtime.upload_folder = Path(tempdir)
            client.app.state.runtime.upload_processing_worker = None
            monkeypatch.setattr(
                conversation_service_module.conversation_service,
                "add_uploaded_file",
                lambda **kwargs: {"success": True, "data": {"file_id": 8}},
            )
            monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "local://mirrored")

            response = client.post(
                "/api/v1/upload_pdf",
                files={"file": ("sample.pdf", b"pdf-data", "application/pdf")},
                data={"conversation_id": "12"},
            )

            client.app.dependency_overrides.clear()

    assert response.status_code == 200


def test_clear_pdf_resets_runtime_path():
    with TestClient(app) as client:
        client.app.state.runtime.current_pdf_path = "/tmp/example.pdf"
        response = client.post("/api/v1/clear_pdf")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.app.state.runtime.current_pdf_path is None


def test_upload_processing_config_prefers_new_worker_env_names(monkeypatch):
    from app.modules.conversation.upload_processing_worker import UploadProcessingConfig

    monkeypatch.setenv("UPLOAD_PROCESSING_WORKER_MAX_WORKERS", "5")
    monkeypatch.setenv("UPLOAD_FILE_PROCESSING_MAX_WORKERS", "2")
    monkeypatch.setenv("UPLOAD_PROCESSING_MAX_PDF_PAGES", "33")
    monkeypatch.setenv("UPLOAD_FILE_PROCESSING_MAX_PDF_PAGES", "11")

    config = UploadProcessingConfig.from_env()

    assert config.max_workers == 5
    assert config.pdf_max_pages == 33


def test_upload_processing_config_accepts_legacy_env_names(monkeypatch):
    from app.modules.conversation.upload_processing_worker import UploadProcessingConfig

    monkeypatch.delenv("UPLOAD_PROCESSING_WORKER_MAX_WORKERS", raising=False)
    monkeypatch.setenv("UPLOAD_FILE_PROCESSING_MAX_WORKERS", "4")
    monkeypatch.delenv("UPLOAD_PROCESSING_MAX_PDF_PAGES", raising=False)
    monkeypatch.setenv("UPLOAD_FILE_PROCESSING_MAX_PDF_PAGES", "22")

    config = UploadProcessingConfig.from_env()

    assert config.max_workers == 4
    assert config.pdf_max_pages == 22
