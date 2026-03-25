from fastapi.testclient import TestClient

from server_fastapi.app import create_app



def test_fastapi_admin_routes_are_not_exposed():
    client = TestClient(create_app())

    assert client.get("/api/admin/users?page=1&page_size=10").status_code == 404
    assert client.post("/api/admin/users", json={"username": "u1", "password": "Pass123!", "user_type": "common"}).status_code == 404
    assert client.post("/api/admin/users/batch-import").status_code == 404
