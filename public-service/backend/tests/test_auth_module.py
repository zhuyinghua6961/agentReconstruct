import json
import os
import inspect

import pytest

from app.core.deps import AuthContext
from app.core.errors import DatabaseUnavailableError
from app.core.errors import AppError
from app.main import app
from app.modules.auth import api as auth_api_module
from app.modules.auth import deps as auth_deps_module
from app.modules.auth.repository import AuthRepository
from app.modules.auth import service as auth_service_module
from app.modules.auth.deps import get_bearer_token, get_optional_auth_context, require_auth_context
from app.modules.auth.schemas import LoginRequest, RegisterRequest, SecurityQuestionItem, SetSecurityQuestionsRequest
from app.modules.auth.service import AuthService, TokenService, _hash_password, auth_service


def _route_for(path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _decode(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_auth_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/v1/auth/login" in paths
    assert "/api/auth/login" in paths
    assert "/api/v1/auth/register" in paths
    assert "/api/auth/register" in paths
    assert "/api/v1/auth/me" in paths
    assert "/api/auth/me" in paths
    assert "/api/v1/auth/departments/tree" in paths
    assert "/api/auth/departments/tree" in paths
    assert "/api/v1/auth/department" in paths
    assert "/api/auth/department" in paths
    assert "/api/v1/auth/personnel-binding" in paths
    assert "/api/auth/personnel-binding" in paths
    assert "/api/v1/auth/username" in paths
    assert "/api/auth/username" in paths
    assert "/api/v1/auth/security-questions" in paths
    assert "/api/auth/security-questions" in paths

    me_route = _route_for("/api/v1/auth/me", "GET")
    department_tree_route = _route_for("/api/v1/auth/departments/tree", "GET")
    department_update_route = _route_for("/api/v1/auth/department", "PUT")
    personnel_binding_route = _route_for("/api/v1/auth/personnel-binding", "PUT")
    username_update_route = _route_for("/api/v1/auth/username", "PUT")
    security_route = _route_for("/api/v1/auth/security-questions", "PUT")
    password_route = _route_for("/api/v1/auth/password", "PUT")
    assert require_auth_context in {dep.call for dep in me_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in department_update_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in personnel_binding_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in username_update_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in security_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in password_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in department_tree_route.dependant.dependencies}


def test_login_route_contract(monkeypatch):
    def fake_login(username: str, password: str):
        assert username == "alice"
        assert password == "Secret123!"
        return {
            "success": True,
            "message": "login_success",
            "data": {
                "token": "token-1",
                "user": {"id": 1, "username": "alice", "role": "user", "user_type": 3},
                "is_first_login": False,
                "has_security_questions": True,
                "require_security_questions_setup": False,
            },
        }

    monkeypatch.setattr(auth_service_module.auth_service, "login", fake_login)
    response = auth_api_module.login(LoginRequest(username="alice", password="Secret123!"))
    assert response.status_code == 200
    body = _decode(response)
    assert body["success"] is True
    assert body["data"]["token"] == "token-1"


def test_login_route_exposes_first_login_flags(monkeypatch):
    def fake_login(username: str, password: str):
        assert username == "alice"
        assert password == "Secret123!"
        return {
            "success": True,
            "message": "login_success",
            "data": {
                "token": "token-1",
                "user": {"id": 1, "username": "alice", "role": "user", "user_type": 3},
                "is_first_login": True,
                "has_security_questions": False,
                "require_security_questions_setup": True,
            },
            "require_password_change": True,
            "require_security_questions_setup": True,
        }

    monkeypatch.setattr(auth_service_module.auth_service, "login", fake_login)
    response = auth_api_module.login(LoginRequest(username="alice", password="Secret123!"))
    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["is_first_login"] is True
    assert body["data"]["has_security_questions"] is False
    assert body["require_password_change"] is True
    assert body["require_security_questions_setup"] is True


def test_login_route_exposes_department_flags(monkeypatch):
    def fake_login(username: str, password: str):
        assert username == "alice"
        assert password == "Secret123!"
        return {
            "success": True,
            "message": "login_success",
            "data": {
                "token": "token-1",
                "user": {
                    "id": 1,
                    "username": "alice",
                    "role": "user",
                    "user_type": 3,
                    "primary_department_id": None,
                    "primary_department_name": None,
                    "secondary_department_id": None,
                    "secondary_department_name": None,
                },
                "is_first_login": False,
                "has_security_questions": True,
                "require_security_questions_setup": False,
                "require_department_setup": True,
            },
            "require_department_setup": True,
        }

    monkeypatch.setattr(auth_service_module.auth_service, "login", fake_login)
    response = auth_api_module.login(LoginRequest(username="alice", password="Secret123!"))
    body = _decode(response)
    assert response.status_code == 200
    assert body["data"]["user"]["primary_department_id"] is None
    assert body["data"]["require_department_setup"] is True
    assert body["require_department_setup"] is True


def test_login_route_exposes_personnel_flags(monkeypatch):
    def fake_login(username: str, password: str):
        assert username == "alice"
        assert password == "Secret123!"
        return {
            "success": True,
            "message": "login_success",
            "data": {
                "token": "token-1",
                "user": {
                    "id": 1,
                    "username": "alice",
                    "role": "user",
                    "user_type": 3,
                    "personnel_id": None,
                    "employee_no": None,
                    "full_name": None,
                    "personnel_binding_status": "unbound",
                    "require_personnel_setup": True,
                },
                "is_first_login": False,
                "has_security_questions": True,
                "require_security_questions_setup": False,
                "require_department_setup": False,
                "require_personnel_setup": True,
            },
            "require_department_setup": False,
            "require_personnel_setup": True,
        }

    monkeypatch.setattr(auth_service_module.auth_service, "login", fake_login)
    response = auth_api_module.login(LoginRequest(username="alice", password="Secret123!"))
    body = _decode(response)
    assert response.status_code == 200
    assert body["data"]["user"]["personnel_binding_status"] == "unbound"
    assert body["data"]["require_personnel_setup"] is True
    assert body["require_personnel_setup"] is True


def test_register_route_complete_profile_contract(monkeypatch):
    def fake_register(**kwargs):
        assert kwargs == {
            "username": "alice",
            "password": "Secret123!",
            "employee_no": "T2024001",
            "full_name": "张三",
            "verification_code": "ABC123",
            "security_questions": [
            {"question": "我最喜欢的水果是什么？", "answer": "苹果"}
            ],
        }
        return {
            "success": True,
            "message": "register_success",
            "data": {
                "token": "token-2",
                "user": {
                    "id": 2,
                    "username": "alice",
                    "role": "user",
                    "user_type": 2,
                    "primary_department_id": 1,
                    "primary_department_name": "计算机学院",
                    "secondary_department_id": 11,
                    "secondary_department_name": "软件工程系",
                    "tertiary_department_id": 111,
                    "tertiary_department_name": "软件工程教研室",
                    "department_completion_level": "complete",
                    "require_department_setup": False,
                    "personnel_id": 501,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "personnel_binding_status": "bound_active",
                    "require_personnel_setup": False,
                    "has_security_questions": True,
                    "require_security_questions_setup": False,
                    "is_first_login": False,
                },
                "is_first_login": False,
                "has_security_questions": True,
                "require_security_questions_setup": False,
                "require_department_setup": False,
                "require_personnel_setup": False,
            },
        }

    monkeypatch.setattr(auth_service_module.auth_service, "register", fake_register)
    response = auth_api_module.register(
        RegisterRequest(
            username="alice",
            password="Secret123!",
            employee_no="T2024001",
            full_name="张三",
            verification_code="ABC123",
            security_questions=[
                SecurityQuestionItem(question="我最喜欢的水果是什么？", answer="苹果")
            ],
        )
    )
    assert response.status_code == 201
    body = _decode(response)
    user = body["data"]["user"]
    assert user["role"] == "user"
    assert user["user_type"] == 2
    assert user["primary_department_name"] == "计算机学院"
    assert user["secondary_department_name"] == "软件工程系"
    assert user["tertiary_department_name"] == "软件工程教研室"
    assert user["department_completion_level"] == "complete"
    assert user["employee_no"] == "T2024001"
    assert user["full_name"] == "张三"
    assert user["personnel_binding_status"] == "bound_active"
    assert user["has_security_questions"] is True
    assert user["require_security_questions_setup"] is False
    assert user["require_department_setup"] is False
    assert user["require_personnel_setup"] is False
    assert user["is_first_login"] is False
    assert body["data"]["is_first_login"] is False
    assert body["data"]["require_security_questions_setup"] is False
    assert body["data"]["require_department_setup"] is False
    assert body["data"]["require_personnel_setup"] is False


def test_forgot_password_initiate_contract(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "initiate_password_reset",
        lambda username: {
            "success": True,
            "data": {
                "has_security_questions": True,
                "questions": ["我最喜欢的水果是什么？", "我出生在哪个城市？"],
            },
        },
    )
    response = auth_api_module.forgot_password_initiate(auth_api_module.ForgotPasswordInitiateRequest(username="alice"))
    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["has_security_questions"] is True
    assert len(body["data"]["questions"]) == 2


def test_me_requires_valid_token_contract(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_user_info",
        lambda user_id: {"success": True, "data": {"id": user_id, "username": "alice", "role": "user"}},
    )

    response = auth_api_module.me(AuthContext(user_id=7, role="user", username="alice"))
    assert response.status_code == 200
    assert _decode(response)["data"]["id"] == 7


def test_me_route_exposes_department_fields(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_user_info",
        lambda user_id: {
            "success": True,
            "data": {
                "id": user_id,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": 111,
                "tertiary_department_name": "人工智能实验室",
                "department_completion_level": "complete",
                "require_department_setup": False,
            },
        },
    )

    response = auth_api_module.me(AuthContext(user_id=7, role="user", username="alice"))
    body = _decode(response)
    assert response.status_code == 200
    assert body["data"]["primary_department_name"] == "计算机学院"
    assert body["data"]["secondary_department_name"] == "软件工程系"
    assert body["data"]["tertiary_department_name"] == "人工智能实验室"
    assert body["data"]["department_completion_level"] == "complete"
    assert body["data"]["require_department_setup"] is False


def test_me_route_exposes_personnel_fields(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_user_info",
        lambda user_id: {
            "success": True,
            "data": {
                "id": user_id,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "personnel_id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            },
        },
    )

    response = auth_api_module.me(AuthContext(user_id=7, role="user", username="alice"))
    body = _decode(response)
    assert response.status_code == 200
    assert body["data"]["employee_no"] == "T2024001"
    assert body["data"]["personnel_binding_status"] == "bound_active"
    assert body["data"]["require_personnel_setup"] is False


def test_security_questions_write_contract(monkeypatch):
    captured = {}

    def fake_set_security_questions(*, user_id: int, questions: list[dict]):
        captured["user_id"] = user_id
        captured["questions"] = questions
        return {"success": True, "message": "安全问题设置成功"}

    monkeypatch.setattr(auth_service_module.auth_service, "set_security_questions", fake_set_security_questions)
    response = auth_api_module.set_security_questions(
        SetSecurityQuestionsRequest(
            questions=[
                SecurityQuestionItem(question="q1", answer="a1"),
                SecurityQuestionItem(question="q2", answer="a2"),
            ]
        ),
        AuthContext(user_id=9, role="user", username="bob"),
    )
    assert response.status_code == 200
    assert captured["user_id"] == 9
    assert captured["questions"][0]["question"] == "q1"


def test_get_security_questions_contract(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_security_questions",
        lambda **kwargs: {
            "success": True,
            "data": {
                "questions": ["我最喜欢的水果是什么？", "我出生在哪个城市？"],
            },
        },
    )
    response = auth_api_module.get_security_questions(AuthContext(user_id=9, role="user", username="bob"))
    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["questions"][0] == "我最喜欢的水果是什么？"


def test_auth_department_tree_contract(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_selectable_department_tree",
        lambda: {
            "success": True,
            "data": {"items": [{"id": 1, "name": "计算机学院", "secondary_items": []}]},
        },
    )

    response = auth_api_module.get_department_tree()
    assert response.status_code == 200
    assert _decode(response)["data"]["items"][0]["name"] == "计算机学院"


def test_auth_department_update_contract(monkeypatch):
    captured = {}

    def fake_update_department(
        *,
        user_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None,
    ):
        captured["user_id"] = user_id
        captured["primary_department_id"] = primary_department_id
        captured["secondary_department_id"] = secondary_department_id
        captured["tertiary_department_id"] = tertiary_department_id
        return {
            "success": False,
            "error": "部门由人员信息维护，请联系管理员或修改绑定人员",
            "code": "DEPARTMENT_MANAGED_BY_PERSONNEL",
        }

    monkeypatch.setattr(auth_service_module.auth_service, "update_department", fake_update_department)

    response = auth_api_module.update_department(
        auth_api_module.DepartmentUpdateRequest(
            primary_department_id=1,
            secondary_department_id=11,
            tertiary_department_id=111,
        ),
        AuthContext(user_id=9, role="user", username="bob"),
    )
    assert response.status_code == 400
    assert captured == {
        "user_id": 9,
        "primary_department_id": 1,
        "secondary_department_id": 11,
        "tertiary_department_id": 111,
    }
    assert _decode(response)["code"] == "DEPARTMENT_MANAGED_BY_PERSONNEL"


def test_auth_personnel_binding_update_contract(monkeypatch):
    assert hasattr(auth_api_module, "update_personnel_binding")

    def fake_update_personnel_binding(*, user_id: int, employee_no: str, full_name: str, verification_code: str):
        assert user_id == 9
        assert employee_no == "T2024001"
        assert full_name == "张三"
        assert verification_code == "ABC123"
        return {
            "success": True,
            "data": {
                "personnel_id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            },
        }

    monkeypatch.setattr(auth_service_module.auth_service, "update_personnel_binding", fake_update_personnel_binding)

    payload_cls = getattr(auth_api_module, "PersonnelBindingUpdateRequest", None)
    assert payload_cls is not None
    response = auth_api_module.update_personnel_binding(
        payload_cls(
            employee_no="T2024001",
            full_name="张三",
            verification_code="ABC123",
        ),
        AuthContext(user_id=9, role="user", username="bob"),
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["personnel_binding_status"] == "bound_active"


def test_auth_update_username_contract(monkeypatch):
    assert hasattr(auth_api_module, "update_username")
    captured = {}

    def fake_update_username(*, user_id: int, username: str):
        captured["user_id"] = user_id
        captured["username"] = username
        return {"success": True, "data": {"id": user_id, "username": username, "role": "user", "user_type": 3}}

    monkeypatch.setattr(auth_service_module.auth_service, "update_username", fake_update_username)

    payload = type("Payload", (), {"username": "alice-renamed"})()
    response = auth_api_module.update_username(
        payload,
        AuthContext(user_id=9, role="user", username="alice"),
    )

    assert response.status_code == 200
    assert captured == {"user_id": 9, "username": "alice-renamed"}
    assert _decode(response)["data"]["username"] == "alice-renamed"


def test_get_bearer_token_supports_header_and_query():
    assert get_bearer_token("Bearer abc", None) == "abc"
    assert get_bearer_token(None, "xyz") == "xyz"


def test_require_auth_context_reports_missing_token():
    with pytest.raises(AppError) as exc_info:
        require_auth_context(None)
    assert exc_info.value.code == "TOKEN_MISSING"


def test_token_service_requires_explicit_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET is required"):
        TokenService()
    monkeypatch.setenv("JWT_SECRET", os.environ.get("JWT_SECRET", "test-jwt-secret"))


def test_optional_auth_context_surfaces_db_unavailable(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "decode_token", lambda token: {"user_id": 7, "role": "user"})

    def _fail_lookup(_user_id: int):
        raise DatabaseUnavailableError("db_unavailable")

    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", _fail_lookup)

    with pytest.raises(AppError) as exc_info:
        get_optional_auth_context("token-1")

    assert exc_info.value.code == "DB_UNAVAILABLE"
    assert exc_info.value.status_code == 503


def test_require_auth_context_surfaces_db_unavailable(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "decode_token", lambda token: {"user_id": 7, "role": "user"})

    def _fail_lookup(_user_id: int):
        raise DatabaseUnavailableError("db_unavailable")

    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", _fail_lookup)

    with pytest.raises(AppError) as exc_info:
        require_auth_context("token-1")

    assert exc_info.value.code == "DB_UNAVAILABLE"
    assert exc_info.value.status_code == 503


def test_require_auth_context_rejects_active_account_bound_to_disabled_personnel(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "decode_token", lambda token: {"user_id": 7, "role": "user"})
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_user_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "alice",
            "role": "user",
            "user_type": 3,
            "status": "active",
            "personnel_id": 17,
        },
    )
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "build_disabled_personnel_login_error",
        lambda user: {
            "success": False,
            "error": "账号所属人员已停用，请联系管理员",
            "code": "PERSONNEL_DISABLED",
            "data": {
                "personnel": {
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "department_display": "磷酸铁锂事业部 / 材料研发部",
                }
            },
        },
        raising=False,
    )

    with pytest.raises(AppError) as exc_info:
        require_auth_context("token-1")

    assert exc_info.value.code == "PERSONNEL_DISABLED"
    assert exc_info.value.status_code == 403
    assert exc_info.value.extra_payload["data"]["personnel"]["employee_no"] == "T2024001"


def test_auth_service_login_lockout_contract():
    class FakeRepo:
        def __init__(self):
            self.user = {
                "id": 1,
                "username": "alice",
                "password_hash": "pbkdf2_sha256$120000$salt$deadbeef",
                "role": "user",
                "status": "active",
                "failed_login_attempts": 4,
            }

        def get_by_username(self, username):
            return dict(self.user) if username == "alice" else None

        def increment_login_attempts(self, *, user_id: int, lock_threshold: int, lock_minutes: int):
            return {"failed_login_attempts": lock_threshold}

        def reset_login_attempts(self, *, user_id: int):
            return 1

        def has_security_questions(self, *, user_id: int):
            return False

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    result = service.login("alice", "wrong-password")
    assert result["success"] is False
    assert result["code"] == "ACCOUNT_LOCKED_DUE_TO_FAILURES"


def test_auth_service_change_password_rejects_password_history_reuse():
    class FakeRepo:
        def __init__(self):
            self.current_hash = _hash_password("OldPassword1!")
            self.history_hash = _hash_password("ReusePassword1!")

        def get_by_id(self, user_id):
            if user_id != 1:
                return None
            return {"id": 1, "username": "alice", "password_hash": self.current_hash, "role": "user"}

        def list_recent_password_hashes(self, *, user_id: int, limit: int):
            assert user_id == 1
            assert limit == 3
            return [self.history_hash]

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    result = service.change_password(user_id=1, old_password="OldPassword1!", new_password="ReusePassword1!")
    assert result["success"] is False
    assert result["code"] == "PASSWORD_REUSED"


def test_auth_service_register_creates_super_user_with_completed_profile():
    register_payload = {
        "username": " alice ",
        "password": "Secret123!",
        "employee_no": "T2024001",
        "full_name": "张三",
        "verification_code": "ABC123",
        "security_questions": [
            {"question": "我最喜欢的水果是什么？", "answer": " 苹果 "},
        ],
    }

    class FakeRepo:
        def __init__(self):
            self.created_payload = None
            self.get_by_id_calls = 0

        def get_by_username(self, username):
            assert username == "alice"
            return None

        def create_user(self, **kwargs):
            raise AssertionError(f"legacy create_user path should not be used: {kwargs}")

        def add_password_history(self, **kwargs):
            raise AssertionError(f"legacy add_password_history path should not be used: {kwargs}")

        def trim_password_history(self, **kwargs):
            raise AssertionError(f"legacy trim_password_history path should not be used: {kwargs}")

        def create_registered_user(self, **kwargs):
            self.created_payload = dict(kwargs)
            return 27

        def get_by_id(self, user_id):
            self.get_by_id_calls += 1
            if user_id != 27 or not self.created_payload:
                return None
            return {
                "id": 27,
                "username": self.created_payload["username"],
                "role": "user",
                "user_type": 2,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "primary_department_id": self.created_payload["primary_department_id"],
                "secondary_department_id": self.created_payload["secondary_department_id"],
                "tertiary_department_id": self.created_payload["tertiary_department_id"],
                "personnel_id": self.created_payload["personnel_id"],
            }

        def has_security_questions(self, *, user_id: int):
            assert user_id == 27
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            assert kwargs["primary_department_id"] == 1
            assert kwargs["secondary_department_id"] == 11
            assert kwargs["tertiary_department_id"] == 111
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": 111,
                "tertiary_department_name": "软件工程教研室",
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            assert kwargs == {
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code": "ABC123",
            }
            return {
                "success": True,
                "data": {
                    "id": 501,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "status": "active",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": 111,
                },
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 501
            return {
                "personnel_id": 501,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    class FakeTokenService:
        def issue_access_token(self, *, user_id: int, role: str):
            assert user_id == 27
            assert role == "user"
            return "token-registered"

    repo = FakeRepo()
    service = AuthService(
        repo=repo,
        token_service=FakeTokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
    )

    result = service.register(**register_payload)

    assert result["success"] is True
    assert result["message"] == "register_success"
    assert "require_password_change" not in result
    assert repo.created_payload is not None
    assert repo.get_by_id_calls == 1
    assert repo.created_payload["username"] == "alice"
    assert repo.created_payload["user_type"] == 2
    assert repo.created_payload["primary_department_id"] == 1
    assert repo.created_payload["secondary_department_id"] == 11
    assert repo.created_payload["tertiary_department_id"] == 111
    assert repo.created_payload["personnel_id"] == 501
    assert repo.created_payload["password_hash"]
    question_items = repo.created_payload["security_question_items"]
    assert len(question_items) == 1
    assert question_items[0]["question"] == "我最喜欢的水果是什么？"
    assert question_items[0]["sort_order"] == 1
    assert question_items[0]["answer_hash"]

    user = result["data"]["user"]
    assert result["data"]["token"] == "token-registered"
    assert user["role"] == "user"
    assert user["user_type"] == 2
    assert user["primary_department_id"] == 1
    assert user["primary_department_name"] == "计算机学院"
    assert user["secondary_department_id"] == 11
    assert user["secondary_department_name"] == "软件工程系"
    assert user["tertiary_department_id"] == 111
    assert user["tertiary_department_name"] == "软件工程教研室"
    assert user["department_completion_level"] == "complete"
    assert user["require_department_setup"] is False
    assert user["personnel_id"] == 501
    assert user["employee_no"] == "T2024001"
    assert user["full_name"] == "张三"
    assert user["personnel_binding_status"] == "bound_active"
    assert user["require_personnel_setup"] is False
    assert user["has_security_questions"] is True
    assert user["require_security_questions_setup"] is False
    assert user["is_first_login"] is False
    assert result["data"]["is_first_login"] is False
    assert result["data"]["has_security_questions"] is True
    assert result["data"]["require_security_questions_setup"] is False
    assert result["data"]["require_department_setup"] is False
    assert result["data"]["require_personnel_setup"] is False


def test_auth_service_register_accepts_personnel_with_secondary_direct_department():
    class FakeRepo:
        def __init__(self):
            self.created = None

        def get_by_username(self, username):
            return None

        def create_registered_user(self, **kwargs):
            self.created = kwargs
            return 7

        def get_by_id(self, user_id):
            return {
                "id": user_id,
                "username": "alice",
                "role": "user",
                "user_type": 2,
                "status": "active",
                "primary_department_id": self.created["primary_department_id"],
                "secondary_department_id": self.created["secondary_department_id"],
                "tertiary_department_id": self.created["tertiary_department_id"],
                "personnel_id": self.created["personnel_id"],
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "created_at": None,
            }

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            assert kwargs == {
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": None,
            }
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": "legacy_two_level_complete",
                "department_display": "计算机学院 / 软件工程系",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {
                "success": True,
                "data": {
                    "id": 501,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "status": "active",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": None,
                },
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 501
            return {
                "personnel_id": 501,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    repo = FakeRepo()
    service = AuthService(
        repo=repo,
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
    )

    result = service.register(
        username="alice",
        password="Secret123!",
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        security_questions=[{"question": "我最喜欢的水果是什么？", "answer": "苹果"}],
    )

    assert result["success"] is True
    assert repo.created["secondary_department_id"] == 11
    assert repo.created["tertiary_department_id"] is None
    assert result["data"]["require_department_setup"] is False


def test_auth_service_register_rejects_invalid_personnel_identity():
    class FakeRepo:
        def get_by_username(self, username):
            return None

        def create_registered_user(self, **kwargs):
            raise AssertionError(f"unexpected create_registered_user call: {kwargs}")

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            raise AssertionError(f"unexpected describe_user_department call: {kwargs}")

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {"success": False, "error": "人员信息校验失败", "code": "PERSONNEL_BINDING_INVALID"}

    service = AuthService(
        repo=FakeRepo(),
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
    )

    result = service.register(
        username="alice",
        password="Secret123!",
        employee_no="T2024001",
        full_name="张三",
        verification_code="BAD",
        security_questions=[{"question": "我最喜欢的水果是什么？", "answer": "苹果"}],
    )

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_BINDING_INVALID"


def test_auth_service_register_rejects_disabled_personnel():
    class FakeRepo:
        def get_by_username(self, username):
            return None

        def create_registered_user(self, **kwargs):
            raise AssertionError(f"unexpected create_registered_user call: {kwargs}")

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            raise AssertionError(f"unexpected describe_user_department call: {kwargs}")

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {"success": False, "error": "该人员已停用", "code": "PERSONNEL_DISABLED"}

    service = AuthService(
        repo=FakeRepo(),
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
    )

    result = service.register(
        username="alice",
        password="Secret123!",
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        security_questions=[{"question": "我最喜欢的水果是什么？", "answer": "苹果"}],
    )

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_DISABLED"
    assert service.status_code_for(result, ok_status=201) == 400


def test_auth_service_register_requires_1_to_3_security_questions():
    class FakeRepo:
        def get_by_username(self, username):
            return None

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": 111,
                "tertiary_department_name": "软件工程教研室",
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {
                "success": True,
                "data": {
                    "id": 501,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "status": "active",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": 111,
                },
            }

    service = AuthService(
        repo=FakeRepo(),
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
    )

    empty_result = service.register(
        username="alice",
        password="Secret123!",
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        security_questions=[],
    )
    too_many_result = service.register(
        username="alice",
        password="Secret123!",
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        security_questions=[
            {"question": "q1", "answer": "a1"},
            {"question": "q2", "answer": "a2"},
            {"question": "q3", "answer": "a3"},
            {"question": "q4", "answer": "a4"},
        ],
    )

    assert empty_result["success"] is False
    assert empty_result["code"] == "VALIDATION_ERROR"
    assert too_many_result["success"] is False
    assert too_many_result["code"] == "VALIDATION_ERROR"


def test_auth_service_register_returns_username_exists_on_duplicate():
    class FakeRepo:
        def get_by_username(self, username):
            return {"id": 9, "username": username}

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    result = service.register(
        username="alice",
        password="Secret123!",
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        security_questions=[{"question": "我最喜欢的水果是什么？", "answer": "苹果"}],
    )

    assert result["success"] is False
    assert result["code"] == "USERNAME_EXISTS"


def test_auth_service_register_uses_atomic_repository_path():
    class FakeRepo:
        def __init__(self):
            self.atomic_called = 0

        def get_by_username(self, username):
            return None

        def create_user(self, **kwargs):
            raise AssertionError(f"legacy create_user path should not be used: {kwargs}")

        def add_password_history(self, **kwargs):
            raise AssertionError(f"legacy add_password_history path should not be used: {kwargs}")

        def trim_password_history(self, **kwargs):
            raise AssertionError(f"legacy trim_password_history path should not be used: {kwargs}")

        def create_registered_user(self, **kwargs):
            self.atomic_called += 1
            return 27

        def get_by_id(self, user_id):
            return {
                "id": 27,
                "username": "alice",
                "role": "user",
                "user_type": 2,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "personnel_id": 501,
            }

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": 111,
                "tertiary_department_name": "软件工程教研室",
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {
                "success": True,
                "data": {
                    "id": 501,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "status": "active",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": 111,
                },
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            return {
                "personnel_id": 501,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    class FakeTokenService:
        def issue_access_token(self, *, user_id: int, role: str):
            return "token-registered"

    repo = FakeRepo()
    service = AuthService(
        repo=repo,
        token_service=FakeTokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
    )
    result = service.register(
        username="alice",
        password="Secret123!",
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        security_questions=[{"question": "我最喜欢的水果是什么？", "answer": "苹果"}],
    )

    assert result["success"] is True
    assert repo.atomic_called == 1


def test_auth_repository_create_registered_user_requires_complete_schema_support():
    class FakeDatabase:
        def connection(self):
            raise AssertionError("connection should not be opened when required schema is missing")

    repo = AuthRepository(database=FakeDatabase())
    repo.has_column = lambda name: name != "personnel_id"
    repo.has_table = lambda name: True

    with pytest.raises(RuntimeError, match="registration_schema_incomplete:columns=personnel_id"):
        repo.create_registered_user(
            username="alice",
            password_hash="hash-1",
            primary_department_id=1,
            secondary_department_id=11,
            tertiary_department_id=111,
            personnel_id=501,
            security_question_items=[
                {"question": "我最喜欢的水果是什么？", "answer_hash": "hash-answer", "sort_order": 1},
            ],
            user_type=2,
        )


def test_auth_repository_create_registered_user_rolls_back_on_security_question_failure():
    class FakeCursor:
        def __init__(self, connection):
            self._connection = connection
            self.lastrowid = 0
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            query_text = " ".join(str(query).split())
            if "INSERT INTO users" in query_text:
                self.lastrowid = 91
                self.rowcount = 1
                self._connection.pending_usernames.append(str(params[0]))
                return
            if "INSERT INTO password_history" in query_text:
                self.lastrowid = 0
                self.rowcount = 1
                return
            if "INSERT INTO user_security_questions" in query_text:
                raise RuntimeError("question insert failed")
            raise AssertionError(f"unexpected query: {query_text}")

    class FakeConnection:
        def __init__(self):
            self.pending_usernames: list[str] = []
            self.persisted_usernames: list[str] = []
            self.begin_called = 0
            self.commit_called = 0
            self.rollback_called = 0

        def begin(self):
            self.begin_called += 1

        def commit(self):
            self.commit_called += 1
            self.persisted_usernames = list(self.pending_usernames)

        def rollback(self):
            self.rollback_called += 1
            self.pending_usernames = []
            self.persisted_usernames = []

        def cursor(self):
            return FakeCursor(self)

    class FakeConnectionManager:
        def __init__(self, connection):
            self._connection = connection

        def __enter__(self):
            return self._connection

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeDatabase:
        def __init__(self, connection):
            self._connection = connection

        def connection(self):
            return FakeConnectionManager(self._connection)

    connection = FakeConnection()
    repo = AuthRepository(database=FakeDatabase(connection))
    repo.has_column = lambda name: name in {
        "user_type",
        "is_first_login",
        "must_set_security_questions",
        "personnel_id",
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
        "password_updated_at",
    }
    repo.has_table = lambda name: name in {"password_history", "user_security_questions"}

    with pytest.raises(RuntimeError, match="question insert failed"):
        repo.create_registered_user(
            username="alice",
            password_hash="hash-1",
            primary_department_id=1,
            secondary_department_id=11,
            tertiary_department_id=111,
            personnel_id=501,
            security_question_items=[
                {"question": "我最喜欢的水果是什么？", "answer_hash": "hash-answer", "sort_order": 1},
            ],
            user_type=2,
        )

    assert connection.begin_called == 1
    assert connection.commit_called == 0
    assert connection.rollback_called == 1
    assert connection.persisted_usernames == []


def test_auth_service_login_includes_personnel_department_payload_and_flags():
    class FakeRepo:
        def __init__(self):
            self.password_hash = _hash_password("Secret123!")

        def get_by_username(self, username):
            if username != "alice":
                return None
            return {
                "id": 1,
                "username": "alice",
                "password_hash": self.password_hash,
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": 7,
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def reset_login_attempts(self, *, user_id: int):
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

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
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def get_personnel_by_id(self, *, personnel_id: int | None):
            assert personnel_id == 7
            return {
                "id": 7,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 7
            return {
                "personnel_id": 7,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    service = AuthService(
        repo=FakeRepo(),
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
    )
    result = service.login("alice", "Secret123!")

    assert result["success"] is True
    assert result["data"]["user"]["primary_department_id"] == 1
    assert result["data"]["user"]["secondary_department_id"] == 11
    assert result["data"]["user"]["tertiary_department_id"] == 111
    assert result["data"]["user"]["department_completion_level"] == "complete"
    assert result["data"]["require_department_setup"] is False
    assert result["require_department_setup"] is False


def test_auth_service_get_user_info_uses_legacy_department_fallback_when_strict_flag_disabled():
    class FakeRepo:
        def get_by_id(self, user_id):
            if user_id != 9:
                return None
            return {
                "id": 9,
                "username": "bob",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "personnel_id": 9,
            }

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(
            self,
            *,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            if tertiary_department_id is None:
                return {
                    "primary_department_id": None,
                    "primary_department_name": None,
                    "secondary_department_id": None,
                    "secondary_department_name": None,
                    "tertiary_department_id": None,
                    "tertiary_department_name": None,
                    "department_completion_level": "empty",
                    "require_department_setup": True,
                }
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": 111,
                "tertiary_department_name": "人工智能实验室",
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def get_personnel_by_id(self, *, personnel_id: int | None):
            assert personnel_id == 9
            return {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 9
            return {
                "personnel_id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    service = AuthService(
        repo=FakeRepo(),
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
        personnel_department_strict_source_enabled=False,
    )
    result = service.get_user_info(9)

    assert result["success"] is True
    assert result["data"]["primary_department_id"] == 1
    assert result["data"]["tertiary_department_id"] == 111
    assert result["data"]["department_completion_level"] == "complete"
    assert result["data"]["require_department_setup"] is False


def test_auth_service_login_requires_department_setup_when_strict_flag_enabled_and_personnel_department_missing():
    class FakeRepo:
        password_hash = _hash_password("Secret123!")

        def get_by_username(self, username):
            assert username == "alice"
            return {
                "id": 9,
                "username": "alice",
                "password_hash": self.password_hash,
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": 9,
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
            }

        def reset_login_attempts(self, *, user_id: int):
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None if kwargs.get("tertiary_department_id") is None else kwargs.get("primary_department_id"),
                "primary_department_name": None if kwargs.get("tertiary_department_id") is None else "计算机学院",
                "secondary_department_id": None if kwargs.get("tertiary_department_id") is None else kwargs.get("secondary_department_id"),
                "secondary_department_name": None if kwargs.get("tertiary_department_id") is None else "软件工程系",
                "tertiary_department_id": kwargs.get("tertiary_department_id"),
                "tertiary_department_name": None if kwargs.get("tertiary_department_id") is None else "人工智能实验室",
                "department_completion_level": "empty" if kwargs.get("tertiary_department_id") is None else "complete",
                "require_department_setup": kwargs.get("tertiary_department_id") is None,
            }

    class FakePersonnel:
        def get_personnel_by_id(self, *, personnel_id: int | None):
            assert personnel_id == 9
            return {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            return {
                "personnel_id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    service = AuthService(
        repo=FakeRepo(),
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
        personnel_department_strict_source_enabled=True,
    )
    result = service.login("alice", "Secret123!")

    assert result["success"] is True
    assert result["data"]["user"]["primary_department_id"] is None
    assert result["data"]["user"]["tertiary_department_id"] is None
    assert result["data"]["require_department_setup"] is True


def test_auth_service_legacy_department_fallback_only_uses_existing_complete_user_cache():
    class FakeRepo:
        password_hash = _hash_password("Secret123!")

        def get_by_username(self, username):
            assert username == "alice"
            return {
                "id": 9,
                "username": "alice",
                "password_hash": self.password_hash,
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": 9,
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": None,
            }

        def reset_login_attempts(self, *, user_id: int):
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None if kwargs.get("tertiary_department_id") is None else kwargs.get("primary_department_id"),
                "primary_department_name": None if kwargs.get("tertiary_department_id") is None else "计算机学院",
                "secondary_department_id": None if kwargs.get("tertiary_department_id") is None else kwargs.get("secondary_department_id"),
                "secondary_department_name": None if kwargs.get("tertiary_department_id") is None else "软件工程系",
                "tertiary_department_id": kwargs.get("tertiary_department_id"),
                "tertiary_department_name": None if kwargs.get("tertiary_department_id") is None else "人工智能实验室",
                "department_completion_level": "empty" if kwargs.get("tertiary_department_id") is None else "complete",
                "require_department_setup": kwargs.get("tertiary_department_id") is None,
            }

    class FakePersonnel:
        def get_personnel_by_id(self, *, personnel_id: int | None):
            return {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            return {
                "personnel_id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    service = AuthService(
        repo=FakeRepo(),
        token_service=TokenService(),
        department_service=FakeDepartments(),
        personnel_service=FakePersonnel(),
        personnel_department_strict_source_enabled=False,
    )
    result = service.login("alice", "Secret123!")

    assert result["success"] is True
    assert result["data"]["user"]["primary_department_id"] is None
    assert result["data"]["require_department_setup"] is True


def test_auth_service_update_department_is_rejected_when_managed_by_personnel():
    service = AuthService(repo=object(), token_service=TokenService())
    result = service.update_department(
        user_id=9,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_MANAGED_BY_PERSONNEL"
    assert result["error"] == "部门由人员信息维护，请联系管理员或修改绑定人员"


def test_auth_service_admin_without_department_is_not_forced_to_complete_department():
    class FakeRepo:
        def get_by_id(self, user_id):
            if user_id != 1:
                return None
            return {
                "id": 1,
                "username": "admin",
                "role": "admin",
                "user_type": 1,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def has_security_questions(self, *, user_id: int):
            return True

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
                "department_display": "未填写",
                "department_completion_level": "empty",
                "require_department_setup": True,
            }

    service = AuthService(repo=FakeRepo(), token_service=TokenService(), department_service=FakeDepartments())
    result = service.get_user_info(1)

    assert result["success"] is True
    assert result["data"]["role"] == "admin"
    assert result["data"]["require_department_setup"] is False


def test_auth_service_rejects_username_shorter_than_3():
    class FakeRepo:
        def get_by_username(self, username):
            return None

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    assert hasattr(service, "validate_username_candidate")
    result = service.validate_username_candidate(username="ab")

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"


def test_auth_service_rejects_username_with_admin_prefix_case_insensitive():
    class FakeRepo:
        def get_by_username(self, username):
            return None

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    assert hasattr(service, "validate_username_candidate")
    result = service.validate_username_candidate(username="AdminRoot")

    assert result["success"] is False
    assert result["code"] == "USERNAME_INVALID"


def test_auth_service_rejects_duplicate_username_when_owner_differs():
    class FakeRepo:
        def get_by_username(self, username):
            if username == "alice":
                return {"id": 5, "username": "alice"}
            return None

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    assert hasattr(service, "validate_username_candidate")
    result = service.validate_username_candidate(username="alice", owner_user_id=9)

    assert result["success"] is False
    assert result["code"] == "USERNAME_EXISTS"


def test_auth_service_accepts_same_username_as_noop_for_same_user():
    class FakeRepo:
        def get_by_username(self, username):
            if username == "alice":
                return {"id": 9, "username": "alice"}
            return None

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    assert hasattr(service, "validate_username_candidate")
    result = service.validate_username_candidate(username=" alice ", owner_user_id=9)

    assert result["success"] is True
    assert result["data"]["username"] == "alice"


def test_auth_service_update_username_rejects_admin_self_service():
    class FakeRepo:
        def get_by_id(self, user_id):
            return {
                "id": user_id,
                "username": "admin",
                "role": "admin",
                "user_type": 1,
                "status": "active",
            }

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    assert hasattr(service, "update_username")
    result = service.update_username(user_id=1, username="admin2")

    assert result["success"] is False
    assert result["code"] == "PERMISSION_DENIED"
    assert service.status_code_for(result, ok_status=200) == 403


def test_auth_service_update_username_updates_non_admin_user():
    class FakeRepo:
        def __init__(self):
            self.updated = None

        def get_by_id(self, user_id):
            if user_id != 9:
                return None
            if self.updated:
                return {
                    "id": 9,
                    "username": self.updated[1],
                    "role": "user",
                    "user_type": 3,
                    "status": "active",
                    "is_first_login": 0,
                    "must_set_security_questions": 0,
                    "primary_department_id": None,
                    "secondary_department_id": None,
                    "created_at": None,
                }
            return {
                "id": 9,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "primary_department_id": None,
                "secondary_department_id": None,
                "created_at": None,
            }

        def get_by_username(self, username):
            return None

        def update_username(self, *, user_id: int, username: str):
            self.updated = (user_id, username)
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

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
                "department_completion_level": "empty",
                "require_department_setup": False,
            }

    repo = FakeRepo()
    service = AuthService(repo=repo, token_service=TokenService(), department_service=FakeDepartments())
    assert hasattr(service, "update_username")
    result = service.update_username(user_id=9, username="alice-renamed")

    assert result["success"] is True
    assert repo.updated == (9, "alice-renamed")
    assert result["data"]["username"] == "alice-renamed"


def test_auth_service_update_username_returns_user_not_found():
    class FakeRepo:
        def get_by_id(self, user_id):
            return None

    service = AuthService(repo=FakeRepo(), token_service=TokenService())
    assert hasattr(service, "update_username")
    result = service.update_username(user_id=999, username="ghost")

    assert result["success"] is False
    assert result["code"] == "USER_NOT_FOUND"


def _build_auth_service_with_personnel(*, repo, departments, personnel, strict_source: bool = False):
    assert "personnel_service" in inspect.signature(AuthService).parameters
    return AuthService(
        repo=repo,
        token_service=TokenService(),
        department_service=departments,
        personnel_service=personnel,
        personnel_department_strict_source_enabled=strict_source,
    )


def test_auth_service_login_marks_unbound_user_as_require_personnel_setup():
    class FakeRepo:
        password_hash = _hash_password("Secret123!")

        def get_by_username(self, username):
            assert username == "alice"
            return {
                "id": 9,
                "username": "alice",
                "password_hash": self.password_hash,
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": None,
            }

        def reset_login_attempts(self, *, user_id: int):
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": "empty",
                "require_department_setup": False,
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

    service = _build_auth_service_with_personnel(
        repo=FakeRepo(),
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.login("alice", "Secret123!")

    assert result["success"] is True
    assert result["data"]["user"]["personnel_binding_status"] == "unbound"
    assert result["data"]["require_personnel_setup"] is True
    assert result["require_personnel_setup"] is True


def test_auth_service_login_rejects_disabled_personnel_after_password_is_verified():
    class FakeRepo:
        password_hash = _hash_password("Secret123!")
        reset_called = False

        def get_by_username(self, username):
            assert username == "alice"
            return {
                "id": 9,
                "username": "alice",
                "password_hash": self.password_hash,
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": 7,
            }

        def reset_login_attempts(self, *, user_id: int):
            self.reset_called = True
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_display": "磷酸铁锂事业部 / 材料研发部",
                "department_completion_level": "empty",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 7
            return {
                "personnel_id": 7,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_disabled",
                "require_personnel_setup": True,
            }

        def get_personnel_by_id(self, *, personnel_id: int | None):
            assert personnel_id == 7
            return {
                "id": 7,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "disabled",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": None,
            }

    repo = FakeRepo()
    service = _build_auth_service_with_personnel(
        repo=repo,
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.login("alice", "Secret123!")

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_DISABLED"
    assert result["http_status"] == 403
    assert service.status_code_for(result, ok_status=200) == 403
    assert result["error"] == "账号所属人员已停用，请联系管理员"
    assert result["data"]["personnel"] == {
        "employee_no": "T2024001",
        "full_name": "张三",
        "department_display": "磷酸铁锂事业部 / 材料研发部",
    }
    assert repo.reset_called is False


def test_auth_service_login_does_not_disclose_disabled_personnel_when_password_is_wrong():
    class FakeRepo:
        password_hash = _hash_password("Secret123!")

        def get_by_username(self, username):
            return {
                "id": 9,
                "username": "alice",
                "password_hash": self.password_hash,
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": 7,
            }

        def increment_login_attempts(self, *, user_id: int, lock_threshold: int, lock_minutes: int):
            return {"failed_login_attempts": 1}

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            raise AssertionError("department details must not be loaded before password verification")

    class FakePersonnel:
        def describe_user_personnel(self, *, personnel_id: int | None):
            raise AssertionError("personnel details must not be loaded before password verification")

        def get_personnel_by_id(self, *, personnel_id: int | None):
            raise AssertionError("personnel details must not be loaded before password verification")

    service = _build_auth_service_with_personnel(
        repo=FakeRepo(),
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.login("alice", "WrongPassword1!")

    assert result["success"] is False
    assert result["code"] == "INVALID_CREDENTIALS"
    assert "data" not in result


def test_auth_service_login_allows_admin_bound_to_disabled_personnel():
    class FakeRepo:
        password_hash = _hash_password("Secret123!")

        def get_by_username(self, username):
            return {
                "id": 1,
                "username": "admin",
                "password_hash": self.password_hash,
                "role": "admin",
                "user_type": 1,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": 7,
            }

        def reset_login_attempts(self, *, user_id: int):
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_display": "未填写",
                "department_completion_level": "empty",
                "require_department_setup": True,
            }

    class FakePersonnel:
        def describe_user_personnel(self, *, personnel_id: int | None):
            return {
                "personnel_id": 7,
                "employee_no": "T2024001",
                "full_name": "张三",
                "personnel_binding_status": "bound_disabled",
                "require_personnel_setup": True,
            }

        def get_personnel_by_id(self, *, personnel_id: int | None):
            raise AssertionError("admin should not be blocked by personnel status")

    service = _build_auth_service_with_personnel(
        repo=FakeRepo(),
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.login("admin", "Secret123!")

    assert result["success"] is True
    assert result["data"]["user"]["role"] == "admin"
    assert result["data"]["user"]["require_personnel_setup"] is False


def test_auth_service_get_user_info_exposes_bound_active_personnel_payload():
    class FakeRepo:
        def get_by_id(self, user_id):
            if user_id != 9:
                return None
            return {
                "id": 9,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "personnel_id": 9,
            }

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": "empty",
                "require_department_setup": False,
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

    service = _build_auth_service_with_personnel(
        repo=FakeRepo(),
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.get_user_info(9)

    assert result["success"] is True
    assert result["data"]["employee_no"] == "T2024001"
    assert result["data"]["personnel_binding_status"] == "bound_active"
    assert result["data"]["require_personnel_setup"] is False


def test_auth_service_update_personnel_binding_rejects_invalid_tuple_without_leaking_detail():
    class FakeRepo:
        def get_by_id(self, user_id):
            if user_id != 9:
                return None
            return {
                "id": 9,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "personnel_id": None,
                "is_first_login": 0,
                "must_set_security_questions": 0,
            }

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": "empty",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {"success": False, "error": "工号不存在", "code": "PERSONNEL_BINDING_INVALID"}

    service = _build_auth_service_with_personnel(
        repo=FakeRepo(),
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.update_personnel_binding(
        user_id=9,
        employee_no="T2024999",
        full_name="未知",
        verification_code="BAD",
    )

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_BINDING_INVALID"
    assert result["error"] == "人员信息校验失败"


def test_auth_service_update_personnel_binding_rejects_disabled_personnel():
    class FakeRepo:
        def get_by_id(self, user_id):
            if user_id != 9:
                return None
            return {
                "id": 9,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "personnel_id": None,
                "is_first_login": 0,
                "must_set_security_questions": 0,
            }

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": "empty",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {"success": False, "error": "该人员已停用", "code": "PERSONNEL_DISABLED"}

    service = _build_auth_service_with_personnel(
        repo=FakeRepo(),
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.update_personnel_binding(
        user_id=9,
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
    )

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_DISABLED"


def test_auth_service_update_personnel_binding_accepts_secondary_direct_department():
    class FakeRepo:
        def __init__(self):
            self.updated = False

        def get_by_id(self, user_id):
            return {
                "id": 9,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "personnel_id": None,
                "is_first_login": 0,
                "must_set_security_questions": 0,
            }

        def update_user_personnel(self, *, user_id: int, personnel_id: int | None):
            self.updated = True
            raise AssertionError(f"unexpected update_user_personnel call: {user_id}, {personnel_id}")

        def bind_user_personnel_with_departments(
            self,
            *,
            user_id: int,
            personnel_id: int,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None = None,
        ):
            self.bound = {
                "user_id": user_id,
                "personnel_id": personnel_id,
                "primary_department_id": primary_department_id,
                "secondary_department_id": secondary_department_id,
                "tertiary_department_id": tertiary_department_id,
            }
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": kwargs.get("primary_department_id"),
                "primary_department_name": "计算机学院" if kwargs.get("primary_department_id") else None,
                "secondary_department_id": kwargs.get("secondary_department_id"),
                "secondary_department_name": "软件工程系" if kwargs.get("secondary_department_id") else None,
                "tertiary_department_id": kwargs.get("tertiary_department_id"),
                "tertiary_department_name": "人工智能实验室" if kwargs.get("tertiary_department_id") else None,
                "department_completion_level": "legacy_two_level_complete" if kwargs.get("tertiary_department_id") is None else "complete",
                "department_display": "计算机学院 / 软件工程系",
                "require_department_setup": False,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {
                "success": True,
                "data": {
                    "id": 15,
                    "employee_no": "T2024002",
                    "full_name": "李四",
                    "status": "active",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": None,
                },
            }

        def get_personnel_by_id(self, *, personnel_id: int | None):
            assert personnel_id == 15
            return {
                "id": 15,
                "employee_no": "T2024002",
                "full_name": "李四",
                "status": "active",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": None,
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 15
            return {
                "personnel_id": 15,
                "employee_no": "T2024002",
                "full_name": "李四",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    repo = FakeRepo()
    service = _build_auth_service_with_personnel(
        repo=repo,
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.update_personnel_binding(
        user_id=9,
        employee_no="T2024002",
        full_name="李四",
        verification_code="XYZ789",
    )

    assert result["success"] is True
    assert repo.bound == {
        "user_id": 9,
        "personnel_id": 15,
        "primary_department_id": 1,
        "secondary_department_id": 11,
        "tertiary_department_id": None,
    }
    assert repo.updated is False


def test_auth_service_update_personnel_binding_allows_rebind_to_other_active_personnel():
    class FakeRepo:
        def __init__(self):
            self.bound = None

        def get_by_id(self, user_id):
            if user_id != 9:
                return None
            if self.bound:
                return {
                    "id": 9,
                    "username": "alice",
                    "role": "user",
                    "user_type": 3,
                    "status": "active",
                    "personnel_id": self.bound["personnel_id"],
                    "is_first_login": 0,
                    "must_set_security_questions": 0,
                }
            return {
                "id": 9,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "personnel_id": 7,
                "is_first_login": 0,
                "must_set_security_questions": 0,
            }

        def bind_user_personnel_with_departments(
            self,
            *,
            user_id: int,
            personnel_id: int,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None = None,
        ):
            self.bound = {
                "user_id": user_id,
                "personnel_id": personnel_id,
                "primary_department_id": primary_department_id,
                "secondary_department_id": secondary_department_id,
                "tertiary_department_id": tertiary_department_id,
            }
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": kwargs.get("primary_department_id"),
                "primary_department_name": "计算机学院" if kwargs.get("primary_department_id") else None,
                "secondary_department_id": kwargs.get("secondary_department_id"),
                "secondary_department_name": "软件工程系" if kwargs.get("secondary_department_id") else None,
                "tertiary_department_id": kwargs.get("tertiary_department_id"),
                "tertiary_department_name": "人工智能实验室" if kwargs.get("tertiary_department_id") else None,
                "department_completion_level": "complete" if kwargs.get("tertiary_department_id") else "empty",
                "require_department_setup": kwargs.get("tertiary_department_id") is None,
            }

    class FakePersonnel:
        def verify_personnel_identity(self, **kwargs):
            return {
                "success": True,
                "data": {
                    "id": 15,
                    "employee_no": "T2024002",
                    "full_name": "李四",
                    "status": "active",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": 111,
                },
            }

        def get_personnel_by_id(self, *, personnel_id: int | None):
            assert personnel_id == 15
            return {
                "id": 15,
                "employee_no": "T2024002",
                "full_name": "李四",
                "status": "active",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
            }

        def describe_user_personnel(self, *, personnel_id: int | None):
            assert personnel_id == 15
            return {
                "personnel_id": 15,
                "employee_no": "T2024002",
                "full_name": "李四",
                "personnel_binding_status": "bound_active",
                "require_personnel_setup": False,
            }

    repo = FakeRepo()
    service = _build_auth_service_with_personnel(
        repo=repo,
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    result = service.update_personnel_binding(
        user_id=9,
        employee_no="T2024002",
        full_name="李四",
        verification_code="XYZ789",
    )

    assert result["success"] is True
    assert repo.bound == {
        "user_id": 9,
        "personnel_id": 15,
        "primary_department_id": 1,
        "secondary_department_id": 11,
        "tertiary_department_id": 111,
    }
    assert result["data"]["primary_department_id"] == 1
    assert result["data"]["tertiary_department_id"] == 111
    assert result["data"]["personnel_binding_status"] == "bound_active"
    assert result["data"]["employee_no"] == "T2024002"


def test_auth_service_admin_user_is_exempt_from_personnel_requirement():
    class FakeRepo:
        password_hash = _hash_password("Secret123!")

        def get_by_id(self, user_id):
            if user_id != 1:
                return None
            return {
                "id": 1,
                "username": "admin",
                "role": "admin",
                "user_type": 1,
                "status": "active",
                "personnel_id": None,
                "is_first_login": 0,
                "must_set_security_questions": 0,
            }

        def get_by_username(self, username):
            assert username == "admin"
            return {
                "id": 1,
                "username": "admin",
                "password_hash": self.password_hash,
                "role": "admin",
                "user_type": 1,
                "status": "active",
                "personnel_id": None,
                "is_first_login": 0,
                "must_set_security_questions": 0,
            }

        def reset_login_attempts(self, *, user_id: int):
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": "empty",
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

    service = _build_auth_service_with_personnel(
        repo=FakeRepo(),
        departments=FakeDepartments(),
        personnel=FakePersonnel(),
    )
    me_result = service.get_user_info(1)
    login_result = service.login("admin", "Secret123!")

    assert me_result["success"] is True
    assert me_result["data"]["require_department_setup"] is False
    assert me_result["data"]["require_personnel_setup"] is False
    assert login_result["success"] is True
    assert login_result["data"]["require_department_setup"] is False
    assert login_result["require_department_setup"] is False
    assert login_result["data"]["require_personnel_setup"] is False
    assert login_result["require_personnel_setup"] is False


def test_auth_default_service_reports_db_unavailable():
    result = auth_service_module.auth_service.login("alice", "Secret123!")
    assert result["success"] is False
    assert result["code"] == "DB_UNAVAILABLE"


def test_auth_login_route_uses_live_service_object(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "login",
        lambda username, password: {
            "success": True,
            "message": "login_success",
            "data": {
                "token": "live-token",
                "user": {"id": 1, "username": username, "role": "user", "user_type": 3},
                "is_first_login": False,
                "has_security_questions": False,
                "require_security_questions_setup": False,
            },
        },
    )

    response = auth_api_module.login(LoginRequest(username="alice", password="Secret123!"))

    assert response.status_code == 200
    assert _decode(response)["data"]["token"] == "live-token"
