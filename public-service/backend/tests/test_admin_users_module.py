from __future__ import annotations

import asyncio
import json

from app.core.spreadsheet import build_xlsx
from app.core.deps import AuthContext
from app.main import app
from app.modules.admin_users import api as admin_users_api_module
from app.modules.admin_users import import_service as admin_import_service_module
from app.modules.admin_users.import_service import admin_users_import_service
from app.modules.admin_users.service import admin_users_service
from app.modules.auth.deps import require_admin_context
from app.integrations.redis import RedisService
from app.modules.quota import deps as quota_deps


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


class _FakeRequest:
    def __init__(self, *, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = {"content-type": content_type}

    async def body(self) -> bytes:
        return self._body


def _decode(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _route_for(path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


def test_admin_user_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/admin/users" in paths
    assert "/api/admin/users/{user_id}/username" in paths
    assert "/api/admin/users/{user_id}/department" in paths
    assert "/api/admin/users/{user_id}/personnel-binding" in paths
    assert "/api/admin/users/batch-delete" in paths
    assert "/api/admin/users/batch-type" in paths
    assert "/api/admin/users/batch-import" in paths
    assert "/api/admin/users/import-template" in paths

    username_update_route = _route_for("/api/admin/users/{user_id}/username", "PUT")
    assert require_admin_context in {dep.call for dep in username_update_route.dependant.dependencies}


def test_admin_user_list_and_create_routes(monkeypatch):
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

    context = AuthContext(user_id=1, role="admin", username="admin")
    list_resp = admin_users_api_module.list_users(page=2, page_size=20, _context=context)
    create_resp = admin_users_api_module.create_user(
        admin_users_api_module.UserCreateRequest(username="bob", password="Pass123!", user_type="common"),
        context,
    )

    assert list_resp.status_code == 200
    assert _decode(list_resp)["pagination"] == {"page": 2, "page_size": 20}
    assert create_resp.status_code == 201
    assert _decode(create_resp)["data"]["username"] == "bob"


def test_admin_create_user_accepts_department_ids(monkeypatch):
    captured = {}

    def fake_create_user(**kwargs):
        captured.update(kwargs)
        return {"success": True, "data": kwargs}

    monkeypatch.setattr(admin_users_service, "create_user", fake_create_user)

    response = admin_users_api_module.create_user(
        admin_users_api_module.UserCreateRequest(
            username="bob",
            password="Pass123!",
            user_type="common",
            primary_department_id=1,
            secondary_department_id=11,
            tertiary_department_id=111,
        ),
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 201
    assert captured["primary_department_id"] == 1
    assert captured["secondary_department_id"] == 11
    assert captured["tertiary_department_id"] == 111


def test_admin_user_mutation_routes_contract(monkeypatch):
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

    context = AuthContext(user_id=1, role="admin", username="admin")
    password_resp = admin_users_api_module.reset_user_password(
        7,
        admin_users_api_module.UserPasswordResetRequest(new_password="Pass123!"),
        context,
    )
    status_resp = admin_users_api_module.update_user_status(
        7,
        admin_users_api_module.UserStatusUpdateRequest(status="disabled"),
        context,
    )
    type_resp = admin_users_api_module.update_user_type(
        7,
        admin_users_api_module.UserTypeUpdateRequest(user_type="super"),
        context,
    )
    delete_resp = admin_users_api_module.delete_user(7, context)
    batch_delete_resp = admin_users_api_module.batch_delete_users(
        admin_users_api_module.BatchDeleteUsersRequest(user_ids=[7, 8]),
        context,
    )
    batch_type_resp = admin_users_api_module.batch_change_user_type(
        admin_users_api_module.BatchChangeUserTypeRequest(user_ids=[7, 8], user_type="super"),
        context,
    )

    assert password_resp.status_code == 200
    assert _decode(password_resp)["message"] == "password_reset_ok"
    assert status_resp.status_code == 200
    assert _decode(status_resp)["message"] == "status_update_ok"
    assert type_resp.status_code == 200
    assert _decode(type_resp)["message"] == "type_update_ok"
    assert delete_resp.status_code == 200
    assert _decode(delete_resp)["message"] == "delete_ok"
    assert batch_delete_resp.status_code == 200
    assert _decode(batch_delete_resp)["message"] == "batch_delete_ok"
    assert batch_type_resp.status_code == 200
    assert _decode(batch_type_resp)["message"] == "batch_type_ok"


def test_admin_update_user_username_contract(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_username")
    captured = {}

    def fake_update_username(**kwargs):
        captured.update(kwargs)
        return {"success": True, "data": {"id": kwargs["target_user_id"], "username": kwargs["username"]}}

    monkeypatch.setattr(admin_users_service, "update_username", fake_update_username)

    payload = type("Payload", (), {"username": "alice-new"})()
    response = admin_users_api_module.update_user_username(
        7,
        payload,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert captured == {"target_user_id": 7, "username": "alice-new"}
    assert _decode(response)["data"]["username"] == "alice-new"


def test_admin_update_user_username_returns_403_for_permission_denied(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_username")
    monkeypatch.setattr(
        admin_users_service,
        "update_username",
        lambda **kwargs: {"success": False, "error": "不能修改管理员用户名", "code": "PERMISSION_DENIED"},
    )

    payload = type("Payload", (), {"username": "root2"})()
    response = admin_users_api_module.update_user_username(
        1,
        payload,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 403
    assert _decode(response)["code"] == "PERMISSION_DENIED"


def test_admin_update_user_username_returns_409_for_username_exists(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_username")
    monkeypatch.setattr(
        admin_users_service,
        "update_username",
        lambda **kwargs: {"success": False, "error": "用户名已存在", "code": "USERNAME_EXISTS"},
    )

    payload = type("Payload", (), {"username": "alice"})()
    response = admin_users_api_module.update_user_username(
        7,
        payload,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 409
    assert _decode(response)["code"] == "USERNAME_EXISTS"


def test_admin_update_user_username_returns_400_for_validation_error(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_username")
    monkeypatch.setattr(
        admin_users_service,
        "update_username",
        lambda **kwargs: {"success": False, "error": "用户名长度必须在3-50之间", "code": "VALIDATION_ERROR"},
    )

    payload = type("Payload", (), {"username": "ab"})()
    response = admin_users_api_module.update_user_username(
        7,
        payload,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 400
    assert _decode(response)["code"] == "VALIDATION_ERROR"


def test_admin_update_user_username_returns_400_for_admin_prefix(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_username")
    monkeypatch.setattr(
        admin_users_service,
        "update_username",
        lambda **kwargs: {"success": False, "error": "不能以 admin 开头", "code": "USERNAME_INVALID"},
    )

    payload = type("Payload", (), {"username": "AdminFoo"})()
    response = admin_users_api_module.update_user_username(
        7,
        payload,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 400
    assert _decode(response)["code"] == "USERNAME_INVALID"


def test_admin_update_user_department_contract(monkeypatch):
    captured = {}

    def fake_update_department(**kwargs):
        captured.update(kwargs)
        return {"success": True, "data": kwargs}

    monkeypatch.setattr(admin_users_service, "update_department", fake_update_department)

    response = admin_users_api_module.update_user_department(
        7,
        admin_users_api_module.UserDepartmentUpdateRequest(
            primary_department_id=1,
            secondary_department_id=11,
            tertiary_department_id=111,
        ),
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert captured["target_user_id"] == 7
    assert captured["primary_department_id"] == 1
    assert captured["secondary_department_id"] == 11
    assert captured["tertiary_department_id"] == 111


def test_admin_update_user_personnel_binding_contract(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_personnel_binding")
    captured = {}

    def fake_update_user_personnel_binding(**kwargs):
        captured.update(kwargs)
        return {"success": True, "data": {"id": kwargs["target_user_id"], "personnel_id": kwargs["personnel_id"]}}

    monkeypatch.setattr(admin_users_service, "update_user_personnel_binding", fake_update_user_personnel_binding)

    response = admin_users_api_module.update_user_personnel_binding(
        7,
        admin_users_api_module.UserPersonnelBindingUpdateRequest(personnel_id=9),
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert captured == {"target_user_id": 7, "actor_user_id": 1, "personnel_id": 9}
    assert _decode(response)["data"]["personnel_id"] == 9


def test_admin_clear_user_personnel_binding_contract(monkeypatch):
    assert hasattr(admin_users_api_module, "clear_user_personnel_binding")
    captured = {}

    def fake_clear_user_personnel_binding(**kwargs):
        captured.update(kwargs)
        return {"success": True, "data": {"id": kwargs["target_user_id"], "personnel_id": None}}

    monkeypatch.setattr(admin_users_service, "clear_user_personnel_binding", fake_clear_user_personnel_binding)

    response = admin_users_api_module.clear_user_personnel_binding(
        7,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert captured == {"target_user_id": 7, "actor_user_id": 1}
    assert _decode(response)["data"]["personnel_id"] is None


def test_admin_update_user_personnel_binding_returns_404_for_missing_personnel(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_personnel_binding")
    monkeypatch.setattr(
        admin_users_service,
        "update_user_personnel_binding",
        lambda **kwargs: {"success": False, "error": "人员不存在", "code": "PERSONNEL_NOT_FOUND"},
    )

    response = admin_users_api_module.update_user_personnel_binding(
        7,
        admin_users_api_module.UserPersonnelBindingUpdateRequest(personnel_id=99),
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 404
    assert _decode(response)["code"] == "PERSONNEL_NOT_FOUND"


def test_admin_update_user_personnel_binding_returns_400_for_disabled_personnel(monkeypatch):
    assert hasattr(admin_users_api_module, "update_user_personnel_binding")
    monkeypatch.setattr(
        admin_users_service,
        "update_user_personnel_binding",
        lambda **kwargs: {"success": False, "error": "该人员已停用", "code": "PERSONNEL_DISABLED"},
    )

    response = admin_users_api_module.update_user_personnel_binding(
        7,
        admin_users_api_module.UserPersonnelBindingUpdateRequest(personnel_id=9),
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 400
    assert _decode(response)["code"] == "PERSONNEL_DISABLED"


def test_admin_batch_import_and_template_routes(monkeypatch):
    monkeypatch.setattr(
        admin_users_import_service,
        "import_users",
        lambda **kwargs: {"success": True, "message": "导入完成", "data": kwargs},
    )

    request = _FakeRequest(
        body=(
            b'--boundary\r\n'
            b'Content-Disposition: form-data; name="file"; filename="users.csv"\r\n'
            b"Content-Type: text/csv\r\n\r\n"
            b"username,password\nalice,Pass123!\n\r\n"
            b"--boundary--\r\n"
        ),
        content_type="multipart/form-data; boundary=boundary",
    )
    import_resp = asyncio.run(
        admin_users_api_module.batch_import_users(
            request,
            AuthContext(user_id=1, role="admin", username="admin"),
        )
    )
    template_resp = admin_users_api_module.download_import_template(
        format="csv",
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert import_resp.status_code == 200
    assert _decode(import_resp)["success"] is True
    assert _decode(import_resp)["data"]["filename"] == "users.csv"
    assert template_resp.status_code == 200
    assert "attachment; filename=\"user_import_template.csv\"" == template_resp.headers["content-disposition"]


def test_admin_import_template_contains_department_columns():
    response = admin_users_import_service.template_response(fmt="csv")

    assert b"primary_department_name" in response.body
    assert b"secondary_department_name" in response.body
    assert b"tertiary_department_name" in response.body


def test_admin_import_rejects_half_filled_department_columns(monkeypatch):
    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(admin_users_service.users, "get_by_username", lambda username: None)

    csv_bytes = (
        b"username,password,user_type,primary_department_name,secondary_department_name,tertiary_department_name\n"
        b"user1,Pass123!,common,\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,\n"
    )
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)

    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "部门信息必须同时填写一级、二级和三级" in result["data"]["details"][0]["reason"]


def test_admin_import_rejects_case_insensitive_admin_prefix_via_shared_rules(monkeypatch):
    created = {"called": False}

    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(
        admin_users_service,
        "create_user",
        lambda **kwargs: created.__setitem__("called", True) or {"success": False, "error": "不能创建以 admin 为前缀的用户名", "code": "USERNAME_INVALID"},
    )
    monkeypatch.setattr(
        admin_users_service.users,
        "create_user",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("import should not call users.create_user directly")),
    )

    csv_bytes = (
        b"username,password,user_type,primary_department_name,secondary_department_name,tertiary_department_name\n"
        b"AdminFoo,Pass123!,common,,\n"
    )
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)

    assert created["called"] is True
    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "admin" in result["data"]["details"][0]["reason"].lower()


def test_admin_import_rejects_unknown_primary_department(monkeypatch):
    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(admin_users_service.users, "get_by_username", lambda username: None)
    monkeypatch.setattr(
        admin_import_service_module.department_service,
        "resolve_by_names",
        lambda **kwargs: {"success": False, "code": "PRIMARY_DEPARTMENT_NOT_FOUND", "error": "一级部门不存在"},
    )

    csv_bytes = (
        b"username,password,user_type,primary_department_name,secondary_department_name,tertiary_department_name\n"
        b"user1,Pass123!,common,\xe4\xb8\x8d\xe5\xad\x98\xe5\x9c\xa8\xe5\xad\xa6\xe9\x99\xa2,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4\n"
    )
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)

    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "一级部门不存在" in result["data"]["details"][0]["reason"]


def test_admin_import_rejects_unknown_secondary_department(monkeypatch):
    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(admin_users_service.users, "get_by_username", lambda username: None)
    monkeypatch.setattr(
        admin_import_service_module.department_service,
        "resolve_by_names",
        lambda **kwargs: {"success": False, "code": "SECONDARY_DEPARTMENT_NOT_FOUND", "error": "二级部门不存在"},
    )

    csv_bytes = (
        b"username,password,user_type,primary_department_name,secondary_department_name,tertiary_department_name\n"
        b"user1,Pass123!,common,\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,\xe4\xb8\x8d\xe5\xad\x98\xe5\x9c\xa8\xe7\xb3\xbb,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4\n"
    )
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)

    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "二级部门不存在" in result["data"]["details"][0]["reason"]


def test_admin_import_rejects_invalid_primary_secondary_relation(monkeypatch):
    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(admin_users_service.users, "get_by_username", lambda username: None)
    monkeypatch.setattr(
        admin_import_service_module.department_service,
        "resolve_by_names",
        lambda **kwargs: {"success": False, "code": "DEPARTMENT_RELATION_INVALID", "error": "二级部门不属于所选一级部门"},
    )

    csv_bytes = (
        b"username,password,user_type,primary_department_name,secondary_department_name,tertiary_department_name\n"
        b"user1,Pass123!,common,\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,\xe6\x9d\x90\xe6\x96\x99\xe7\xb3\xbb,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4\n"
    )
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)

    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "二级部门不属于所选一级部门" in result["data"]["details"][0]["reason"]


def test_admin_import_rejects_disabled_department(monkeypatch):
    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(admin_users_service.users, "get_by_username", lambda username: None)
    monkeypatch.setattr(
        admin_import_service_module.department_service,
        "resolve_by_names",
        lambda **kwargs: {"success": False, "code": "DEPARTMENT_DISABLED", "error": "部门已停用，无法选择"},
    )

    csv_bytes = (
        b"username,password,user_type,primary_department_name,secondary_department_name,tertiary_department_name\n"
        b"user1,Pass123!,common,\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4\n"
    )
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)

    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "部门已停用，无法选择" in result["data"]["details"][0]["reason"]


def test_admin_import_resolves_department_names_and_persists_ids(monkeypatch):
    created = []

    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(admin_users_service.users, "get_by_username", lambda username: None)
    monkeypatch.setattr(
        admin_import_service_module.department_service,
        "resolve_by_names",
        lambda **kwargs: {
            "success": True,
            "data": {
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
            },
        },
    )
    monkeypatch.setattr(
        admin_users_service,
        "create_user",
        lambda **kwargs: created.append(kwargs)
        or {"success": True, "data": {"id": 1, "username": kwargs["username"], **kwargs}},
    )

    csv_bytes = (
        b"username,password,user_type,primary_department_name,secondary_department_name,tertiary_department_name\n"
        b"user1,Pass123!,common,\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4\n"
    )
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)

    assert result["success"] is True
    assert result["data"]["summary"]["success"] == 1
    assert created[0]["primary_department_id"] == 1
    assert created[0]["secondary_department_id"] == 11
    assert created[0]["tertiary_department_id"] == 111


def test_admin_import_accepts_xlsx_and_persists_department_ids(monkeypatch):
    created = []

    monkeypatch.setattr(admin_users_import_service, "_precheck_excel_upload_quota", lambda **kwargs: (None, None))
    monkeypatch.setattr(admin_users_import_service, "_finalize_excel_upload_quota", lambda **kwargs: None)
    monkeypatch.setattr(admin_users_service.users, "get_by_username", lambda username: None)
    monkeypatch.setattr(
        admin_import_service_module.department_service,
        "resolve_by_names",
        lambda **kwargs: {
            "success": True,
            "data": {
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
            },
        },
    )
    monkeypatch.setattr(
        admin_users_service,
        "create_user",
        lambda **kwargs: created.append(kwargs)
        or {"success": True, "data": {"id": 1, "username": kwargs["username"], **kwargs}},
    )

    payload = build_xlsx(
        headers=[
            "username",
            "password",
            "user_type",
            "primary_department_name",
            "secondary_department_name",
            "tertiary_department_name",
        ],
        rows=[["user1", "Pass123!", "common", "计算机学院", "软件工程系", "人工智能实验室"]],
        sheet_name="用户导入",
    )

    result = admin_users_import_service.import_users(
        file_bytes=payload,
        filename="users.xlsx",
        actor_user_id=1,
    )

    assert result["success"] is True
    assert result["data"]["summary"]["success"] == 1
    assert created[0]["primary_department_id"] == 1
    assert created[0]["secondary_department_id"] == 11
    assert created[0]["tertiary_department_id"] == 111


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


def test_admin_service_create_user_accepts_department_ids():
    class FakeUsers:
        def __init__(self):
            self.created = None
            self.password_history = []
            self.trim_calls = []

        def get_by_username(self, username):
            return None

        def create_user(self, **kwargs):
            self.created = kwargs
            return 9

        def add_password_history(self, *, user_id: int, password_hash: str):
            self.password_history.append((user_id, password_hash))
            return 1

        def trim_password_history(self, *, user_id: int, keep_limit: int):
            self.trim_calls.append((user_id, keep_limit))
            return 1

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            return {
                "primary_department_id": primary_department_id,
                "primary_department_name": None,
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": None,
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": None,
                "require_department_setup": True,
            }

        def validate_department_selection(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
            require_active: bool,
            allow_empty: bool,
            allow_legacy_two_level: bool,
        ):
            assert primary_department_id == 1
            assert secondary_department_id == 11
            assert tertiary_department_id == 111
            assert require_active is True
            assert allow_empty is True
            assert allow_legacy_two_level is False
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "primary_department_name": "计算机学院",
                    "secondary_department_id": 11,
                    "secondary_department_name": "软件工程系",
                    "tertiary_department_id": 111,
                    "tertiary_department_name": "人工智能实验室",
                    "require_department_setup": False,
                },
            }

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users, department_service=FakeDepartments())
    result = service.create_user(
        username="bob",
        password="Pass123!",
        user_type="common",
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert result["success"] is True
    assert users.created["primary_department_id"] == 1
    assert users.created["secondary_department_id"] == 11
    assert users.created["tertiary_department_id"] == 111
    assert result["data"]["primary_department_name"] == "计算机学院"
    assert result["data"]["secondary_department_name"] == "软件工程系"
    assert result["data"]["tertiary_department_name"] == "人工智能实验室"


def test_admin_service_create_user_requires_tertiary_for_non_empty_department():
    class FakeUsers:
        def get_by_username(self, username):
            return None

    class FakeDepartments:
        def validate_department_selection(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
            require_active: bool,
            allow_empty: bool,
            allow_legacy_two_level: bool,
        ):
            assert primary_department_id == 1
            assert secondary_department_id == 11
            assert tertiary_department_id is None
            assert require_active is True
            assert allow_empty is True
            assert allow_legacy_two_level is False
            return {"success": False, "error": "一级、二级和三级部门必须同时填写", "code": "DEPARTMENT_REQUIRED"}

    service = admin_users_service.__class__(users_repo=FakeUsers(), department_service=FakeDepartments())
    result = service.create_user(
        username="bob",
        password="Pass123!",
        user_type="common",
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=None,
    )

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_REQUIRED"


def test_admin_service_update_username_rejects_target_admin(monkeypatch):
    class FakeUsers:
        def get_by_id(self, user_id):
            return {
                "id": user_id,
                "username": "root",
                "role": "admin",
                "user_type": 1,
                "status": "active",
            }

    service = admin_users_service.__class__(users_repo=FakeUsers())
    assert hasattr(service, "update_username")
    result = service.update_username(target_user_id=1, username="root-2")

    assert result["success"] is False
    assert result["code"] == "PERMISSION_DENIED"
    assert service.status_code_for(result, ok_status=200) == 403


def test_admin_service_update_username_updates_common_or_super_user(monkeypatch):
    class FakeUsers:
        def __init__(self):
            self.updated = None

        def get_by_id(self, user_id):
            if user_id != 7:
                return None
            if self.updated:
                return {
                    "id": 7,
                    "username": self.updated[1],
                    "role": "user",
                    "user_type": 2,
                    "status": "active",
                }
            return {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 2,
                "status": "active",
            }

        def get_by_username(self, username):
            return None

        def update_username(self, *, user_id: int, username: str):
            self.updated = (user_id, username)
            return 1

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users)
    assert hasattr(service, "update_username")
    result = service.update_username(target_user_id=7, username="alice-super")

    assert result["success"] is True
    assert users.updated == (7, "alice-super")
    assert result["data"]["username"] == "alice-super"


def test_admin_service_update_username_trims_username_before_persisting():
    class FakeUsers:
        def __init__(self):
            self.updated = None

        def get_by_id(self, user_id):
            if user_id != 7:
                return None
            if self.updated:
                return {
                    "id": 7,
                    "username": self.updated[1],
                    "role": "user",
                    "user_type": 3,
                    "status": "active",
                }
            return {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
            }

        def get_by_username(self, username):
            return None

        def update_username(self, *, user_id: int, username: str):
            self.updated = (user_id, username)
            return 1

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users)
    result = service.update_username(target_user_id=7, username=" alice-renamed ")

    assert result["success"] is True
    assert users.updated == (7, "alice-renamed")
    assert result["data"]["username"] == "alice-renamed"


def test_admin_service_update_username_rejects_shorter_than_3():
    class FakeUsers:
        def get_by_id(self, user_id):
            return {
                "id": user_id,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
            }

        def get_by_username(self, username):
            return None

    service = admin_users_service.__class__(users_repo=FakeUsers())
    result = service.update_username(target_user_id=7, username="ab")

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert service.status_code_for(result, ok_status=200) == 400


def test_admin_service_update_username_rejects_admin_prefix_case_insensitive():
    class FakeUsers:
        def get_by_id(self, user_id):
            return {
                "id": user_id,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
            }

        def get_by_username(self, username):
            return None

    service = admin_users_service.__class__(users_repo=FakeUsers())
    result = service.update_username(target_user_id=7, username="AdminRoot")

    assert result["success"] is False
    assert result["code"] == "USERNAME_INVALID"
    assert service.status_code_for(result, ok_status=200) == 400


def test_admin_service_update_username_returns_user_not_found():
    class FakeUsers:
        def get_by_id(self, user_id):
            return None

    service = admin_users_service.__class__(users_repo=FakeUsers())
    assert hasattr(service, "update_username")
    result = service.update_username(target_user_id=999, username="ghost")

    assert result["success"] is False
    assert result["code"] == "USER_NOT_FOUND"


def test_admin_service_update_username_accepts_same_username_as_noop(monkeypatch):
    class FakeUsers:
        def __init__(self):
            self.update_calls = 0

        def get_by_id(self, user_id):
            return {
                "id": user_id,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
            }

        def get_by_username(self, username):
            if str(username) == "alice":
                return {"id": 7, "username": "alice"}
            return None

        def update_username(self, *, user_id: int, username: str):
            self.update_calls += 1
            return 0

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users)
    assert hasattr(service, "update_username")
    result = service.update_username(target_user_id=7, username=" alice ")

    assert result["success"] is True
    assert users.update_calls == 1
    assert result["data"]["username"] == "alice"


def test_admin_service_update_department_persists_selected_departments():
    class FakeUsers:
        def __init__(self):
            self.updated = None

        def get_by_id(self, user_id):
            if user_id != 7:
                return None
            return {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def update_user_department(self, **kwargs):
            self.updated = kwargs
            return 1

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            return {
                "primary_department_id": primary_department_id,
                "primary_department_name": None,
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": None,
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": None,
                "require_department_setup": True,
            }

        def validate_department_selection(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
            require_active: bool,
            allow_empty: bool,
            allow_legacy_two_level: bool,
        ):
            assert primary_department_id == 1
            assert secondary_department_id == 11
            assert tertiary_department_id == 111
            assert require_active is True
            assert allow_empty is True
            assert allow_legacy_two_level is False
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "primary_department_name": "计算机学院",
                    "secondary_department_id": 11,
                    "secondary_department_name": "软件工程系",
                    "tertiary_department_id": 111,
                    "tertiary_department_name": "人工智能实验室",
                    "require_department_setup": False,
                },
            }

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users, department_service=FakeDepartments())
    result = service.update_department(
        target_user_id=7,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert result["success"] is True
    assert users.updated["user_id"] == 7
    assert users.updated["primary_department_id"] == 1
    assert users.updated["secondary_department_id"] == 11
    assert users.updated["tertiary_department_id"] == 111
    assert result["data"]["require_department_setup"] is False


def test_list_users_includes_personnel_summary_fields():
    class FakeUsers:
        def count_users(self):
            return 1

        def list_users(self, *, offset: int, limit: int):
            assert offset == 0
            assert limit == 10
            return [
                {
                    "id": 7,
                    "username": "alice",
                    "role": "user",
                    "user_type": 3,
                    "status": "active",
                    "personnel_id": 9,
                    "primary_department_id": None,
                    "secondary_department_id": None,
                    "tertiary_department_id": None,
                    "created_at": None,
                }
            ]

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            return {
                "primary_department_id": primary_department_id,
                "primary_department_name": None,
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": None,
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": None,
                "department_completion_level": 0,
                "department_display": "未填写",
                "require_department_setup": True,
            }

    class FakePersonnel:
        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 9
            return {
                "personnel_id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    service = admin_users_service.__class__(users_repo=FakeUsers(), department_service=FakeDepartments())
    service._personnel = FakePersonnel()

    result = service.list_users(page=1, page_size=10)

    assert result["success"] is True
    row = result["data"][0]
    assert row["personnel_id"] == 9
    assert row["employee_no"] == "T2024001"
    assert row["full_name"] == "张三"
    assert row["personnel_binding_status"] == "bound_active"
    assert row["personnel_display"] == "T2024001 / 张三"


def test_admin_bind_user_to_active_personnel():
    class FakeUsers:
        def __init__(self):
            self.updated = None

        def get_by_id(self, user_id):
            if int(user_id) != 7:
                return None
            payload = {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
                "created_at": None,
            }
            payload["personnel_id"] = None if self.updated is None else self.updated["personnel_id"]
            return payload

        def update_user_personnel(self, *, user_id: int, personnel_id: int | None):
            self.updated = {"user_id": user_id, "personnel_id": personnel_id}
            return 1

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": 0,
                "department_display": "未填写",
                "require_department_setup": True,
            }

    class FakePersonnel:
        def get_personnel_by_id(self, *, personnel_id: int | None):
            assert personnel_id == 9
            return {"id": 9, "employee_no": "T2024001", "full_name": "张三", "status": "active"}

        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 9
            return {
                "personnel_id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users, department_service=FakeDepartments())
    service._personnel = FakePersonnel()

    assert hasattr(service, "update_user_personnel_binding")
    result = service.update_user_personnel_binding(target_user_id=7, actor_user_id=1, personnel_id=9)

    assert result["success"] is True
    assert users.updated == {"user_id": 7, "personnel_id": 9}
    assert result["data"]["personnel_id"] == 9
    assert result["data"]["personnel_binding_status"] == "bound_active"
    assert result["data"]["require_personnel_setup"] is False


def test_admin_bind_rejects_disabled_personnel():
    class FakeUsers:
        def get_by_id(self, user_id):
            return {"id": 7, "username": "alice", "role": "user", "user_type": 3, "status": "active", "personnel_id": None}

    class FakePersonnel:
        def get_personnel_by_id(self, *, personnel_id: int | None):
            return {"id": 9, "employee_no": "T2024001", "full_name": "张三", "status": "disabled"}

    service = admin_users_service.__class__(users_repo=FakeUsers())
    service._personnel = FakePersonnel()

    assert hasattr(service, "update_user_personnel_binding")
    result = service.update_user_personnel_binding(target_user_id=7, actor_user_id=1, personnel_id=9)

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_DISABLED"
    assert service.status_code_for(result, ok_status=200) == 400


def test_admin_unbind_user_sets_require_personnel_setup_again():
    class FakeUsers:
        def __init__(self):
            self.updated = None

        def get_by_id(self, user_id):
            if int(user_id) != 7:
                return None
            payload = {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
                "created_at": None,
            }
            payload["personnel_id"] = 9 if self.updated is None else None
            return payload

        def update_user_personnel(self, *, user_id: int, personnel_id: int | None):
            self.updated = {"user_id": user_id, "personnel_id": personnel_id}
            return 1

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": 0,
                "department_display": "未填写",
                "require_department_setup": True,
            }

    class FakePersonnel:
        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id is None
            return {
                "personnel_id": None,
                "employee_no": None,
                "full_name": None,
                "personnel_binding_status": "unbound",
                "require_personnel_setup": True,
            }

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users, department_service=FakeDepartments())
    service._personnel = FakePersonnel()

    assert hasattr(service, "clear_user_personnel_binding")
    result = service.clear_user_personnel_binding(target_user_id=7, actor_user_id=1)

    assert result["success"] is True
    assert users.updated == {"user_id": 7, "personnel_id": None}
    assert result["data"]["personnel_id"] is None
    assert result["data"]["personnel_binding_status"] == "unbound"
    assert result["data"]["require_personnel_setup"] is True


def test_admin_bind_returns_404_for_missing_personnel():
    class FakeUsers:
        def get_by_id(self, user_id):
            return {"id": 7, "username": "alice", "role": "user", "user_type": 3, "status": "active", "personnel_id": None}

    class FakePersonnel:
        def get_personnel_by_id(self, *, personnel_id: int | None):
            return None

    service = admin_users_service.__class__(users_repo=FakeUsers())
    service._personnel = FakePersonnel()

    assert hasattr(service, "update_user_personnel_binding")
    result = service.update_user_personnel_binding(target_user_id=7, actor_user_id=1, personnel_id=99)

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_NOT_FOUND"
    assert service.status_code_for(result, ok_status=200) == 404


def test_admin_service_update_department_requires_full_three_level_chain():
    class FakeUsers:
        def get_by_id(self, user_id):
            if user_id != 7:
                return None
            return {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            return {
                "primary_department_id": primary_department_id,
                "primary_department_name": None,
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": None,
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": None,
                "require_department_setup": True,
            }

        def validate_department_selection(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
            require_active: bool,
            allow_empty: bool,
            allow_legacy_two_level: bool,
        ):
            assert primary_department_id == 1
            assert secondary_department_id == 11
            assert tertiary_department_id is None
            assert require_active is True
            assert allow_empty is True
            assert allow_legacy_two_level is False
            return {"success": False, "error": "一级、二级和三级部门必须同时填写", "code": "DEPARTMENT_REQUIRED"}

    service = admin_users_service.__class__(users_repo=FakeUsers(), department_service=FakeDepartments())
    result = service.update_department(target_user_id=7, primary_department_id=1, secondary_department_id=11, tertiary_department_id=None)

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_REQUIRED"


def test_admin_service_update_department_allows_unchanged_disabled_binding():
    class FakeUsers:
        def __init__(self):
            self.update_called = False

        def get_by_id(self, user_id):
            if user_id != 7:
                return None
            return {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
            }

        def update_user_department(self, **kwargs):
            self.update_called = True
            raise AssertionError(f"unexpected update: {kwargs}")

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            return {
                "primary_department_id": primary_department_id,
                "primary_department_name": "计算机学院",
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": "人工智能实验室",
                "department_effective_status": "disabled",
                "department_display": "计算机学院 / 软件工程系 / 人工智能实验室（已停用）",
                "require_department_setup": False,
            }

        def validate_department_selection(self, **kwargs):
            raise AssertionError(f"unexpected validation: {kwargs}")

    users = FakeUsers()
    service = admin_users_service.__class__(users_repo=users, department_service=FakeDepartments())
    result = service.update_department(target_user_id=7, primary_department_id=1, secondary_department_id=11, tertiary_department_id=111)

    assert result["success"] is True
    assert users.update_called is False
    assert result["data"]["department_display"] == "计算机学院 / 软件工程系 / 人工智能实验室（已停用）"


def test_admin_service_update_department_fails_when_write_does_not_persist():
    class FakeUsers:
        def get_by_id(self, user_id):
            if user_id != 7:
                return None
            return {
                "id": 7,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def update_user_department(self, **kwargs):
            return 0

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            return {
                "primary_department_id": primary_department_id,
                "primary_department_name": "计算机学院" if primary_department_id else None,
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": "软件工程系" if secondary_department_id else None,
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": "人工智能实验室" if tertiary_department_id else None,
                "require_department_setup": (
                    primary_department_id is None or secondary_department_id is None or tertiary_department_id is None
                ),
            }

        def validate_department_selection(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
            require_active: bool,
            allow_empty: bool,
            allow_legacy_two_level: bool,
        ):
            assert primary_department_id == 1
            assert secondary_department_id == 11
            assert tertiary_department_id == 111
            assert require_active is True
            assert allow_empty is True
            assert allow_legacy_two_level is False
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "primary_department_name": "计算机学院",
                    "secondary_department_id": 11,
                    "secondary_department_name": "软件工程系",
                    "tertiary_department_id": 111,
                    "tertiary_department_name": "人工智能实验室",
                    "require_department_setup": False,
                },
            }

    service = admin_users_service.__class__(users_repo=FakeUsers(), department_service=FakeDepartments())
    result = service.update_department(target_user_id=7, primary_department_id=1, secondary_department_id=11, tertiary_department_id=111)

    assert result["success"] is False
    assert result["code"] == "UPDATE_ERROR"


def test_admin_service_list_users_exposes_department_summary():
    class FakeUsers:
        def count_users(self):
            return 1

        def list_users(self, *, offset: int, limit: int):
            assert offset == 0
            assert limit == 10
            return [
                {
                    "id": 7,
                    "username": "alice",
                    "role": "user",
                    "user_type": 3,
                    "status": "active",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": 111,
                    "created_at": None,
                }
            ]

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            assert primary_department_id == 1
            assert secondary_department_id == 11
            assert tertiary_department_id == 111
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": 111,
                "tertiary_department_name": "人工智能实验室",
                "require_department_setup": False,
            }

    service = admin_users_service.__class__(users_repo=FakeUsers(), department_service=FakeDepartments())
    result = service.list_users(page=1, page_size=10)

    assert result["success"] is True
    assert result["data"][0]["primary_department_name"] == "计算机学院"
    assert result["data"][0]["secondary_department_name"] == "软件工程系"
    assert result["data"][0]["tertiary_department_name"] == "人工智能实验室"
    assert result["data"][0]["department_display"] == "计算机学院 / 软件工程系 / 人工智能实验室"
