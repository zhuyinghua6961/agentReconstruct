import json
import os

import pytest
from fastapi.testclient import TestClient

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
    assert "/api/v1/auth/security-questions" in paths
    assert "/api/auth/security-questions" in paths

    me_route = _route_for("/api/v1/auth/me", "GET")
    security_route = _route_for("/api/v1/auth/security-questions", "PUT")
    password_route = _route_for("/api/v1/auth/password", "PUT")
    assert require_auth_context in {dep.call for dep in me_route.dependant.dependencies}
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


def test_auth_default_service_reports_db_unavailable():
    result = auth_service_module.auth_service.login("alice", "Secret123!")
    assert result["success"] is False
    assert result["code"] == "DB_UNAVAILABLE"


def test_protected_auth_route_returns_503_when_repo_unavailable(monkeypatch):
    class FailingRepo:
        def get_by_id(self, user_id):
            raise DatabaseUnavailableError("db_unavailable")

    failing_service = AuthService(repo=FailingRepo(), token_service=TokenService())
    token = failing_service._tokens.issue_access_token(user_id=7, role="user")
    monkeypatch.setattr(auth_service_module, "auth_service", failing_service)

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json()["code"] == "DB_UNAVAILABLE"


def test_auth_runtime_service_is_bound_to_live_http_route():
    with TestClient(app) as client:
        assert client.app.state.auth_service is auth_service_module.auth_service

        client.app.state.auth_service.login = lambda username, password: {  # type: ignore[method-assign]
            "success": True,
            "message": "login_success",
            "data": {
                "token": "live-token",
                "user": {"id": 1, "username": username, "role": "user", "user_type": 3},
                "is_first_login": False,
                "has_security_questions": False,
                "require_security_questions_setup": False,
            },
        }
        response = client.post("/api/v1/auth/login", json={"username": "alice", "password": "Secret123!"})

    assert response.status_code == 200
    assert response.json()["data"]["token"] == "live-token"
