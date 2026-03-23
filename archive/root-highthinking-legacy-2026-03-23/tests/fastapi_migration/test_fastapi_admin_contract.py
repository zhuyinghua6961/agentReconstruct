import io

from fastapi.testclient import TestClient

from server_fastapi.app import create_app
from server_fastapi.auth.deps import AuthContext, require_admin_context


def _admin_app():
    app = create_app()
    app.dependency_overrides[require_admin_context] = lambda: AuthContext(user_id=1, role="admin", username="admin")
    return app


def test_fastapi_admin_list_users_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.admin.admin_users_service.list_users",
        lambda *, page, page_size: {
            "success": True,
            "data": [{"id": 2, "username": "demo", "role": "user", "user_type": 3, "status": "active"}],
            "pagination": {"page": page, "page_size": page_size, "total": 1},
        },
    )
    client = TestClient(_admin_app())
    response = client.get("/api/admin/users?page=1&page_size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"][0]["username"] == "demo"
    assert payload["pagination"]["total"] == 1


def test_fastapi_admin_create_user_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.admin.admin_users_service.create_user",
        lambda **kwargs: {"success": True, "data": {"id": 9, **kwargs}, "message": "created"},
    )
    client = TestClient(_admin_app())
    response = client.post("/api/admin/users", json={"username": "u1", "password": "Pass123!", "user_type": "common"})

    assert response.status_code == 201
    assert response.json()["data"]["username"] == "u1"


def test_fastapi_admin_batch_delete_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.admin.admin_users_service.batch_delete_users",
        lambda *, target_user_ids, actor_user_id: {
            "success": True,
            "data": {"summary": {"deleted": len(target_user_ids), "skipped": 0, "failed": 0}},
        },
    )
    client = TestClient(_admin_app())
    response = client.post("/api/admin/users/batch-delete", json={"user_ids": [2, 3]})

    assert response.status_code == 200
    assert response.json()["data"]["summary"]["deleted"] == 2


def test_fastapi_admin_batch_type_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.admin.admin_users_service.batch_change_user_type",
        lambda *, target_user_ids, target_type_raw: {
            "success": True,
            "data": {"summary": {"changed": len(target_user_ids), "skipped": 0, "failed": 0}, "target": target_type_raw},
        },
    )
    client = TestClient(_admin_app())
    response = client.post("/api/admin/users/batch-type", json={"user_ids": [2], "user_type": "super"})

    assert response.status_code == 200
    assert response.json()["data"]["summary"]["changed"] == 1


def test_fastapi_admin_batch_import_contract(monkeypatch):
    monkeypatch.setattr(
        "server_fastapi.routers.admin.admin_users_import_service.import_users",
        lambda **kwargs: {
            "success": True,
            "data": {"summary": {"total": 1, "success": 1, "failed": 0, "skipped": 0}, "details": []},
        },
    )
    client = TestClient(_admin_app())
    response = client.post(
        "/api/admin/users/batch-import",
        files={"file": ("demo.csv", io.BytesIO(b"username,password\nu1,Pass123!"), "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["summary"]["success"] == 1


def test_fastapi_admin_import_template_contract():
    client = TestClient(_admin_app())
    response = client.get("/api/admin/users/import-template?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "username,password,user_type" in response.text
