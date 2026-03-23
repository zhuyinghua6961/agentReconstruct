from fastapi.testclient import TestClient

from server_fastapi.app import create_app
from server_fastapi.auth.deps import AuthContext, require_auth_context


def test_fastapi_auth_login_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.auth.auth_service.login",
        lambda username, password: {
            "success": True,
            "data": {
                "token": "demo-token",
                "user": {"id": 3, "username": username, "role": "user", "user_type": 3},
                "is_first_login": False,
                "has_security_questions": True,
                "require_security_questions_setup": False,
            },
        },
    )

    client = TestClient(create_app())
    response = client.post("/api/v1/auth/login", json={"username": "demo", "password": "secret"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["token"] == "demo-token"
    assert payload["data"]["user"]["username"] == "demo"


def test_fastapi_auth_me_requires_token():
    client = TestClient(create_app())
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "TOKEN_MISSING"


def test_fastapi_auth_me_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.auth.auth_service.get_user_info",
        lambda user_id: {
            "success": True,
            "data": {"id": user_id, "username": "demo", "role": "user"},
        },
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=8, role="user", username="demo")
    client = TestClient(app)
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"id": 8, "username": "demo", "role": "user"},
    }
