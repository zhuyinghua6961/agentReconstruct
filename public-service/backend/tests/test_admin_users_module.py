from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import AuthContext
from app.main import app
from app.modules.admin_users import import_service as admin_import_service_module
from app.modules.admin_users.import_service import admin_users_import_service
from app.modules.admin_users.service import admin_users_service
from app.modules.quota import deps as quota_deps
from app.modules.auth.deps import require_admin_context
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


def test_admin_user_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/admin/users" in paths
    assert "/api/admin/users/batch-delete" in paths
    assert "/api/admin/users/batch-type" in paths
    assert "/api/admin/users/batch-import" in paths
    assert "/api/admin/users/import-template" in paths


def test_admin_user_list_and_create_routes(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_admin_context] = lambda: AuthContext(
            user_id=1,
            role="admin",
            username="admin",
        )
        monkeypatch.setattr(
            admin_users_service,
            "list_users",
            lambda **kwargs: {"success": True, "data": [{"id": 7, "username": "alice"}], "pagination": kwargs},
        )
        monkeypatch.setattr(
            admin_users_service,
            "create_user",
            lambda **kwargs: {"success": True, "message": "ok", "data": {"id": 9, **kwargs}},
        )

        list_resp = client.get("/api/admin/users?page=2&page_size=20")
        create_resp = client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "Pass123!", "user_type": "common"},
        )
        client.app.dependency_overrides.clear()

    assert list_resp.status_code == 200
    assert list_resp.json()["pagination"] == {"page": 2, "page_size": 20}
    assert create_resp.status_code == 201
    assert create_resp.json()["data"]["username"] == "bob"


def test_admin_user_mutation_routes_contract(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_admin_context] = lambda: AuthContext(
            user_id=1,
            role="admin",
            username="admin",
        )
        monkeypatch.setattr(
            admin_users_service,
            "reset_password",
            lambda **kwargs: {"success": True, "message": "password_reset_ok", "data": kwargs},
        )
        monkeypatch.setattr(
            admin_users_service,
            "update_status",
            lambda **kwargs: {"success": True, "message": "status_update_ok", "data": kwargs},
        )
        monkeypatch.setattr(
            admin_users_service,
            "update_type",
            lambda **kwargs: {"success": True, "message": "type_update_ok", "data": kwargs},
        )
        monkeypatch.setattr(
            admin_users_service,
            "delete_user",
            lambda **kwargs: {"success": True, "message": "delete_ok", "data": kwargs},
        )
        monkeypatch.setattr(
            admin_users_service,
            "batch_delete_users",
            lambda **kwargs: {"success": True, "message": "batch_delete_ok", "data": kwargs},
        )
        monkeypatch.setattr(
            admin_users_service,
            "batch_change_user_type",
            lambda **kwargs: {"success": True, "message": "batch_type_ok", "data": kwargs},
        )

        password_resp = client.put(
            "/api/admin/users/7/password",
            json={"new_password": "Pass123!"},
        )
        status_resp = client.put(
            "/api/admin/users/7/status",
            json={"status": "disabled"},
        )
        type_resp = client.put(
            "/api/admin/users/7/type",
            json={"user_type": "super"},
        )
        delete_resp = client.delete("/api/admin/users/7")
        batch_delete_resp = client.post(
            "/api/admin/users/batch-delete",
            json={"user_ids": [7, 8]},
        )
        batch_type_resp = client.post(
            "/api/admin/users/batch-type",
            json={"user_ids": [7, 8], "user_type": "super"},
        )
        client.app.dependency_overrides.clear()

    assert password_resp.status_code == 200
    assert password_resp.json()["message"] == "password_reset_ok"
    assert status_resp.status_code == 200
    assert status_resp.json()["message"] == "status_update_ok"
    assert type_resp.status_code == 200
    assert type_resp.json()["message"] == "type_update_ok"
    assert delete_resp.status_code == 200
    assert delete_resp.json()["message"] == "delete_ok"
    assert batch_delete_resp.status_code == 200
    assert batch_delete_resp.json()["message"] == "batch_delete_ok"
    assert batch_type_resp.status_code == 200
    assert batch_type_resp.json()["message"] == "batch_type_ok"


def test_admin_batch_import_and_template_routes(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_admin_context] = lambda: AuthContext(
            user_id=1,
            role="admin",
            username="admin",
        )
        monkeypatch.setattr(
            admin_users_import_service,
            "import_users",
            lambda **kwargs: {"success": True, "message": "导入完成", "data": kwargs},
        )

        import_resp = client.post(
            "/api/admin/users/batch-import",
            files={"file": ("users.csv", b"username,password\nalice,Pass123!\n", "text/csv")},
        )
        template_resp = client.get("/api/admin/users/import-template?format=csv")
        client.app.dependency_overrides.clear()

    assert import_resp.status_code == 200
    assert import_resp.json()["success"] is True
    assert import_resp.json()["data"]["filename"] == "users.csv"
    assert template_resp.status_code == 200
    assert "attachment; filename=\"user_import_template.csv\"" == template_resp.headers["content-disposition"]


def test_admin_import_quota_precheck_returns_db_unavailable_on_actor_lookup_failure(monkeypatch):
    monkeypatch.setattr(
        admin_users_service.users,
        "get_by_id",
        lambda _user_id: (_ for _ in ()).throw(RuntimeError("db_down")),
    )

    grant, error = admin_users_import_service._precheck_excel_upload_quota(actor_user_id=7)

    assert grant is None
    assert error == {"success": False, "error": "db_down", "code": "DB_UNAVAILABLE"}


def test_admin_import_releases_quota_lease_on_validation_error(monkeypatch):
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    monkeypatch.setattr(quota_deps.quota_service_module.quota_service, "_get_redis_service", lambda: redis_service)
    monkeypatch.setattr(
        admin_users_service.users,
        "get_by_id",
        lambda user_id: {"id": user_id, "user_type": 3},
    )
    monkeypatch.setattr(
        quota_deps.auth_service_module.auth_service,
        "get_user_by_id",
        lambda user_id: {"id": user_id, "user_type": 3},
    )
    monkeypatch.setattr(
        quota_deps.quota_service_module.quota_service,
        "check_quota",
        lambda **kwargs: {"success": True, "allowed": True, "config_active": True},
    )
    increment_calls = {"count": 0}
    monkeypatch.setattr(
        quota_deps.quota_service_module.quota_service,
        "increment_quota",
        lambda **kwargs: increment_calls.__setitem__("count", increment_calls["count"] + 1) or {"success": True},
    )

    result = admin_users_import_service.import_users(
        file_bytes=b"not-used",
        filename="users.txt",
        actor_user_id=7,
    )

    assert result["code"] == "INVALID_FILE_TYPE"
    assert increment_calls["count"] == 0
    lock_key = quota_deps._quota_lock_key(user_id=7, quota_type="excel_upload")
    assert redis_service.client.get(lock_key) is None


def test_admin_batch_delete_users_partial_success(monkeypatch):
    users = {
        1: {"id": 1, "username": "admin", "role": "admin", "user_type": 1, "status": "active"},
        2: {"id": 2, "username": "alice", "role": "user", "user_type": 3, "status": "active"},
        3: {"id": 3, "username": "bob", "role": "admin", "user_type": 1, "status": "active"},
    }
    deleted: list[int] = []

    monkeypatch.setattr(admin_users_service.users, "get_by_id", lambda user_id: users.get(int(user_id)))
    monkeypatch.setattr(admin_users_service.users, "delete_user", lambda **kwargs: deleted.append(int(kwargs["user_id"])) or 1)

    result = admin_users_service.batch_delete_users(target_user_ids=[2, 3, 9, 1], actor_user_id=1)

    assert result["success"] is True
    assert result["data"]["summary"] == {"total": 4, "success": 1, "failed": 3, "skipped": 0}
    assert deleted == [2]
    assert result["data"]["details"][0]["status"] == "success"
    assert result["data"]["details"][1]["status"] == "failed"
    assert "管理员" in result["data"]["details"][1]["message"]
    assert result["data"]["details"][2]["status"] == "failed"
    assert result["data"]["details"][3]["status"] == "failed"


def test_admin_batch_change_user_type_partial_success(monkeypatch):
    users = {
        2: {"id": 2, "username": "alice", "role": "user", "user_type": 3, "status": "active"},
        3: {"id": 3, "username": "bob", "role": "user", "user_type": 2, "status": "active"},
        4: {"id": 4, "username": "root", "role": "admin", "user_type": 1, "status": "active"},
    }
    updated: list[tuple[int, int]] = []

    monkeypatch.setattr(admin_users_service.users, "has_user_type_column", lambda: True)
    monkeypatch.setattr(admin_users_service.users, "get_by_id", lambda user_id: users.get(int(user_id)))
    monkeypatch.setattr(
        admin_users_service.users,
        "update_user_type",
        lambda **kwargs: updated.append((int(kwargs["user_id"]), int(kwargs["user_type"]))) or 1,
    )

    result = admin_users_service.batch_change_user_type(target_user_ids=[2, 3, 4, 9], target_type_raw="super")

    assert result["success"] is True
    assert result["data"]["summary"] == {"total": 4, "success": 1, "failed": 2, "skipped": 1}
    assert updated == [(2, 2)]
    assert result["data"]["details"][0]["status"] == "success"
    assert result["data"]["details"][1]["status"] == "skipped"
    assert result["data"]["details"][2]["status"] == "failed"
    assert result["data"]["details"][3]["status"] == "failed"
