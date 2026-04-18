import json
import os

import pytest

from app.core.deps import AuthContext
from app.core.errors import DatabaseUnavailableError
from app.core.errors import AppError
from app.main import app
from app.modules.auth import api as auth_api_module
from app.modules.auth import deps as auth_deps_module
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
    assert "/api/v1/auth/me" in paths
    assert "/api/auth/me" in paths
    assert "/api/v1/auth/departments/tree" in paths
    assert "/api/auth/departments/tree" in paths
    assert "/api/v1/auth/department" in paths
    assert "/api/auth/department" in paths
    assert "/api/v1/auth/username" in paths
    assert "/api/auth/username" in paths
    assert "/api/v1/auth/security-questions" in paths
    assert "/api/auth/security-questions" in paths

    me_route = _route_for("/api/v1/auth/me", "GET")
    department_tree_route = _route_for("/api/v1/auth/departments/tree", "GET")
    department_update_route = _route_for("/api/v1/auth/department", "PUT")
    username_update_route = _route_for("/api/v1/auth/username", "PUT")
    security_route = _route_for("/api/v1/auth/security-questions", "PUT")
    password_route = _route_for("/api/v1/auth/password", "PUT")
    assert require_auth_context in {dep.call for dep in me_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in department_tree_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in department_update_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in username_update_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in security_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in password_route.dependant.dependencies}


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


def test_register_route_first_login_contract(monkeypatch):
    def fake_register(username: str, password: str):
        assert username == "alice"
        assert password == "Secret123!"
        return {
            "success": True,
            "message": "register_success",
            "data": {
                "token": "token-2",
                "user": {"id": 2, "username": "alice", "role": "user", "user_type": 3},
                "is_first_login": True,
                "has_security_questions": False,
                "require_security_questions_setup": True,
            },
            "require_password_change": True,
            "require_security_questions_setup": True,
        }

    monkeypatch.setattr(auth_service_module.auth_service, "register", fake_register)
    response = auth_api_module.register(RegisterRequest(username="alice", password="Secret123!"))
    assert response.status_code == 201
    body = _decode(response)
    assert body["require_password_change"] is True
    assert body["require_security_questions_setup"] is True
    assert body["data"]["is_first_login"] is True


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
        lambda **kwargs: {
            "success": True,
            "data": {"items": [{"id": 1, "name": "计算机学院", "secondary_items": []}]},
        },
    )

    response = auth_api_module.get_department_tree(AuthContext(user_id=9, role="user", username="bob"))
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
        return {"success": True, "data": {"require_department_setup": False}}

    monkeypatch.setattr(auth_service_module.auth_service, "update_department", fake_update_department)

    response = auth_api_module.update_department(
        auth_api_module.DepartmentUpdateRequest(
            primary_department_id=1,
            secondary_department_id=11,
            tertiary_department_id=111,
        ),
        AuthContext(user_id=9, role="user", username="bob"),
    )
    assert response.status_code == 200
    assert captured == {
        "user_id": 9,
        "primary_department_id": 1,
        "secondary_department_id": 11,
        "tertiary_department_id": 111,
    }
    assert _decode(response)["data"]["require_department_setup"] is False


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


def test_auth_service_login_includes_tertiary_department_payload_and_flags():
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
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
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

    service = AuthService(repo=FakeRepo(), token_service=TokenService(), department_service=FakeDepartments())
    result = service.login("alice", "Secret123!")

    assert result["success"] is True
    assert result["data"]["user"]["primary_department_id"] == 1
    assert result["data"]["user"]["secondary_department_id"] == 11
    assert result["data"]["user"]["tertiary_department_id"] == 111
    assert result["data"]["user"]["department_completion_level"] == "complete"
    assert result["data"]["require_department_setup"] is False
    assert result["require_department_setup"] is False


def test_auth_service_get_user_info_keeps_legacy_two_level_user_usable():
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
            assert primary_department_id == 1
            assert secondary_department_id == 11
            assert tertiary_department_id is None
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_completion_level": "legacy_two_level_complete",
                "require_department_setup": False,
            }

    service = AuthService(repo=FakeRepo(), token_service=TokenService(), department_service=FakeDepartments())
    result = service.get_user_info(9)

    assert result["success"] is True
    assert result["data"]["department_completion_level"] == "legacy_two_level_complete"
    assert result["data"]["require_department_setup"] is False


def test_auth_service_update_department_persists_selected_departments():
    class FakeRepo:
        def __init__(self):
            self.updated = None

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
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def update_user_department(
            self,
            *,
            user_id: int,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            self.updated = (user_id, primary_department_id, secondary_department_id, tertiary_department_id)
            return 1

        def has_security_questions(self, *, user_id: int):
            return True

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
                    "department_completion_level": "complete",
                    "require_department_setup": False,
                },
            }

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
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    repo = FakeRepo()
    service = AuthService(repo=repo, token_service=TokenService(), department_service=FakeDepartments())
    result = service.update_department(
        user_id=9,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert result["success"] is True
    assert repo.updated == (9, 1, 11, 111)
    assert result["data"]["primary_department_name"] == "计算机学院"
    assert result["data"]["tertiary_department_name"] == "人工智能实验室"
    assert result["data"]["department_completion_level"] == "complete"
    assert result["data"]["require_department_setup"] is False


def test_auth_service_update_department_requires_tertiary_for_non_empty_write():
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
                "department_completion_level": "empty",
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

    service = AuthService(repo=FakeRepo(), token_service=TokenService(), department_service=FakeDepartments())
    result = service.update_department(user_id=9, primary_department_id=1, secondary_department_id=11, tertiary_department_id=None)

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_REQUIRED"


def test_auth_service_update_department_allows_unchanged_disabled_binding():
    class FakeRepo:
        def __init__(self):
            self.update_called = False

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
            }

        def update_user_department(self, **kwargs):
            self.update_called = True
            raise AssertionError(f"unexpected update: {kwargs}")

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
                "primary_department_name": "计算机学院",
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": "人工智能实验室",
                "department_effective_status": "disabled",
                "department_display": "计算机学院 / 软件工程系 / 人工智能实验室（已停用）",
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

        def validate_department_selection(self, **kwargs):
            raise AssertionError(f"unexpected validation: {kwargs}")

    repo = FakeRepo()
    service = AuthService(repo=repo, token_service=TokenService(), department_service=FakeDepartments())
    result = service.update_department(
        user_id=9,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert result["success"] is True
    assert repo.update_called is False
    assert result["data"]["department_effective_status"] == "disabled"
    assert result["data"]["department_display"] == "计算机学院 / 软件工程系 / 人工智能实验室（已停用）"


def test_auth_service_update_department_fails_when_write_does_not_persist():
    class FakeRepo:
        def __init__(self):
            self.read_count = 0

        def get_by_id(self, user_id):
            if user_id != 9:
                return None
            self.read_count += 1
            return {
                "id": 9,
                "username": "bob",
                "role": "user",
                "user_type": 3,
                "status": "active",
                "is_first_login": 0,
                "must_set_security_questions": 0,
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }

        def update_user_department(
            self,
            *,
            user_id: int,
            primary_department_id: int | None,
            secondary_department_id: int | None,
            tertiary_department_id: int | None,
        ):
            return 0

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
                "primary_department_name": "计算机学院" if primary_department_id else None,
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": "软件工程系" if secondary_department_id else None,
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": "人工智能实验室" if tertiary_department_id else None,
                "department_completion_level": (
                    "complete" if primary_department_id and secondary_department_id and tertiary_department_id else "empty"
                ),
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
                    "department_completion_level": "complete",
                    "require_department_setup": False,
                },
            }

    service = AuthService(repo=FakeRepo(), token_service=TokenService(), department_service=FakeDepartments())
    result = service.update_department(
        user_id=9,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert result["success"] is False
    assert result["code"] == "UPDATE_ERROR"


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
