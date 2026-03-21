from fastapi.testclient import TestClient

from server_fastapi.app import create_app
from server_fastapi.auth.deps import AuthContext, require_admin_context, require_auth_context


def test_fastapi_quota_my_requires_token():
    client = TestClient(create_app())
    response = client.get("/api/v1/quota/my")

    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_MISSING"


def test_fastapi_quota_my_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.quota.quota_service.get_user_quotas",
        lambda *, user_id: {
            "success": True,
            "data": {
                "quotas": [
                    {
                        "quota_type": "ask_query",
                        "quota_name": "问答配额",
                        "period": "daily",
                        "period_days": None,
                        "current": 1,
                        "limit": 20,
                        "remaining": 19,
                        "reset_hint": "next_day_start",
                        "windows": [],
                        "multi_period_enabled": False,
                    }
                ],
                "warnings": [],
                "partial_failure": False,
            },
        },
    )

    app = create_app()
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=8, role="user", username="demo")
    client = TestClient(app)
    response = client.get("/api/v1/quota/my")

    assert response.status_code == 200
    assert response.json()["data"]["quotas"][0]["quota_type"] == "ask_query"


def test_fastapi_quota_configs_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.quota.quota_service.get_all_configs",
        lambda: {
            "success": True,
            "data": {
                "configs": [
                    {
                        "quota_type": "text_translate",
                        "quota_name": "翻译配额",
                        "default_limit": 50,
                        "daily_limit": 50,
                        "weekly_limit": None,
                        "monthly_limit": None,
                        "is_active": 1,
                    }
                ]
            },
        },
    )

    app = create_app()
    app.dependency_overrides[require_admin_context] = lambda: AuthContext(user_id=1, role="admin", username="admin")
    client = TestClient(app)
    response = client.get("/api/v1/quota/configs")

    assert response.status_code == 200
    assert response.json()["data"]["configs"][0]["quota_type"] == "text_translate"


def test_fastapi_quota_create_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.quota.quota_service.create_config",
        lambda **kwargs: {"success": True, "message": "quota_config_created", "data": kwargs},
    )

    app = create_app()
    app.dependency_overrides[require_admin_context] = lambda: AuthContext(user_id=1, role="admin", username="admin")
    client = TestClient(app)
    response = client.post(
        "/api/v1/quota/configs",
        json={
            "quota_type": "pdf_summary",
            "quota_name": "全文总结配额",
            "default_limit": 10,
            "daily_limit": 10,
            "is_active": True,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["quota_type"] == "pdf_summary"


def test_fastapi_quota_update_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.quota.quota_service.update_config",
        lambda **kwargs: {"success": True, "message": "quota_config_updated", "data": kwargs},
    )

    app = create_app()
    app.dependency_overrides[require_admin_context] = lambda: AuthContext(user_id=1, role="admin", username="admin")
    client = TestClient(app)
    response = client.put(
        "/api/v1/quota/configs/text_translate",
        json={"default_limit": 99, "daily_limit": 99, "is_active": True},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_fastapi_quota_reset_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.quota.quota_service.reset_user_quota",
        lambda *, user_id, quota_type: {"success": True, "data": {"user_id": user_id, "quota_type": quota_type}},
    )

    app = create_app()
    app.dependency_overrides[require_admin_context] = lambda: AuthContext(user_id=1, role="admin", username="admin")
    client = TestClient(app)
    response = client.post("/api/v1/quota/reset/9/text_translate")

    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": 9, "quota_type": "text_translate"}
