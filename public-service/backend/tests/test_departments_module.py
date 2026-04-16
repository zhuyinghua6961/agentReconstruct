from __future__ import annotations

import json
from pathlib import Path

from app.core.deps import AuthContext
from app.main import app
from app.modules.departments import api as department_api_module
from app.modules.auth.repository import AuthRepository
from app.modules.departments import service as department_service
from app.modules.departments.repository import DepartmentRepository
from app.modules.departments.service import DepartmentService


def load_migration_sql(filename: str) -> str:
    repo_root = Path(__file__).resolve().parents[3]
    migration_path = repo_root / "highThinkingQA" / "server" / "database" / "migrations" / filename
    return migration_path.read_text(encoding="utf-8")


def _decode(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_department_repository_reads_primary_and_secondary_rows():
    repo = DepartmentRepository(database=object())
    rows = [
        {
            "primary_id": 1,
            "primary_name": "计算机学院",
            "primary_status": "active",
            "secondary_id": 11,
            "secondary_name": "软件工程系",
            "secondary_status": "active",
        },
        {
            "primary_id": 1,
            "primary_name": "计算机学院",
            "primary_status": "active",
            "secondary_id": 12,
            "secondary_name": "人工智能系",
            "secondary_status": "disabled",
        },
        {
            "primary_id": 2,
            "primary_name": "化学学院",
            "primary_status": "active",
            "secondary_id": 21,
            "secondary_name": "化工系",
            "secondary_status": "active",
        },
    ]
    repo._execute_query = lambda query, params=(): rows

    tree = repo.list_department_tree(include_disabled=True)

    assert tree[0]["primary_name"] == "计算机学院"
    assert tree[0]["secondary_items"][0]["name"] == "软件工程系"
    assert tree[0]["secondary_items"][1]["status"] == "disabled"
    assert tree[1]["primary_name"] == "化学学院"


def test_auth_repository_select_user_fields_include_department_columns_when_present():
    repo = AuthRepository(database=object())
    repo._columns_cache = {
        "id",
        "username",
        "password_hash",
        "role",
        "user_type",
        "status",
        "is_first_login",
        "must_set_security_questions",
        "primary_department_id",
        "secondary_department_id",
        "created_at",
        "updated_at",
    }

    fields = repo._select_user_fields(include_password=True)

    assert "primary_department_id" in fields
    assert "secondary_department_id" in fields


def test_department_schema_helpers_expect_unique_and_fk_structure():
    ddl = load_migration_sql("20260416_01_user_departments.sql")

    assert "UNIQUE" in ddl
    assert "FOREIGN KEY" in ddl
    assert "primary_department_id" in ddl
    assert "secondary_department_id" in ddl


def test_department_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/api/admin/departments/tree" in paths
    assert "/api/admin/departments/primary" in paths
    assert "/api/admin/departments/secondary/{secondary_id}/status" in paths


def test_admin_department_tree_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "get_admin_tree",
        lambda: {"success": True, "data": {"items": []}},
    )
    response = department_api_module.get_tree(AuthContext(user_id=1, role="admin", username="admin"))

    assert response.status_code == 200
    assert _decode(response)["data"]["items"] == []


def test_admin_department_mutation_contracts(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "create_primary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "rename_primary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "update_primary_status",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "create_secondary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "rename_secondary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "update_secondary_status",
        lambda **kwargs: {"success": True, "data": kwargs},
    )

    context = AuthContext(user_id=1, role="admin", username="admin")
    create_primary_response = department_api_module.create_primary(
        department_api_module.PrimaryDepartmentCreateRequest(name="计算机学院"),
        context,
    )
    rename_primary_response = department_api_module.rename_primary(
        1,
        department_api_module.PrimaryDepartmentRenameRequest(name="信息学院"),
        context,
    )
    update_primary_status_response = department_api_module.update_primary_status(
        1,
        department_api_module.DepartmentStatusUpdateRequest(status="disabled"),
        context,
    )
    create_secondary_response = department_api_module.create_secondary(
        department_api_module.SecondaryDepartmentCreateRequest(primary_department_id=1, name="软件工程系"),
        context,
    )
    rename_secondary_response = department_api_module.rename_secondary(
        11,
        department_api_module.SecondaryDepartmentRenameRequest(name="计算机系"),
        context,
    )
    update_secondary_status_response = department_api_module.update_secondary_status(
        11,
        department_api_module.DepartmentStatusUpdateRequest(status="disabled"),
        context,
    )

    assert create_primary_response.status_code == 201
    assert rename_primary_response.status_code == 200
    assert update_primary_status_response.status_code == 200
    assert create_secondary_response.status_code == 201
    assert rename_secondary_response.status_code == 200
    assert update_secondary_status_response.status_code == 200


def test_department_effective_status_follows_disabled_primary(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "get_admin_tree",
        lambda: {
            "success": True,
            "data": {
                "items": [
                    {
                        "id": 1,
                        "name": "计算机学院",
                        "status": "disabled",
                        "secondary_items": [
                            {
                                "id": 11,
                                "name": "软件工程系",
                                "status": "active",
                                "effective_status": "disabled",
                            }
                        ],
                    }
                ]
            },
        },
    )
    response = department_api_module.get_tree(AuthContext(user_id=1, role="admin", username="admin"))

    assert response.status_code == 200
    assert _decode(response)["data"]["items"][0]["secondary_items"][0]["effective_status"] == "disabled"


def test_department_service_create_and_mutate_primary_and_secondary():
    class FakeRepository:
        def __init__(self) -> None:
            self.primary = {
                1: {"id": 1, "name": "计算机学院", "status": "active"},
            }
            self.secondary = {
                11: {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "active"},
            }

        def get_primary_by_name(self, name: str):
            return next((item for item in self.primary.values() if item["name"] == name), None)

        def get_primary_by_id(self, primary_id: int):
            return self.primary.get(primary_id)

        def create_primary(self, *, name: str):
            self.primary[2] = {"id": 2, "name": name, "status": "active"}
            return 2

        def update_primary_name(self, *, primary_id: int, name: str):
            self.primary[primary_id]["name"] = name
            return 1

        def update_primary_status(self, *, primary_id: int, status: str):
            self.primary[primary_id]["status"] = status
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return next(
                (
                    item
                    for item in self.secondary.values()
                    if item["primary_department_id"] == primary_department_id and item["name"] == name
                ),
                None,
            )

        def get_secondary_by_id(self, secondary_id: int):
            return self.secondary.get(secondary_id)

        def create_secondary(self, *, primary_department_id: int, name: str):
            self.secondary[12] = {
                "id": 12,
                "primary_department_id": primary_department_id,
                "name": name,
                "status": "active",
            }
            return 12

        def update_secondary_name(self, *, secondary_id: int, name: str):
            self.secondary[secondary_id]["name"] = name
            return 1

        def update_secondary_status(self, *, secondary_id: int, status: str):
            self.secondary[secondary_id]["status"] = status
            return 1

    service = DepartmentService(repository=FakeRepository())

    created_primary = service.create_primary(name=" 化学学院 ")
    renamed_primary = service.rename_primary(primary_id=1, name="信息学院")
    disabled_primary = service.update_primary_status(primary_id=1, status="disabled")
    created_secondary = service.create_secondary(primary_department_id=1, name="人工智能系")
    renamed_secondary = service.rename_secondary(secondary_id=11, name="计算机系")
    disabled_secondary = service.update_secondary_status(secondary_id=11, status="disabled")

    assert created_primary["success"] is True
    assert created_primary["data"]["name"] == "化学学院"
    assert renamed_primary["data"]["name"] == "信息学院"
    assert disabled_primary["data"]["status"] == "disabled"
    assert created_secondary["data"]["name"] == "人工智能系"
    assert renamed_secondary["data"]["name"] == "计算机系"
    assert disabled_secondary["data"]["status"] == "disabled"


def test_department_service_rejects_duplicate_names_and_invalid_status():
    class FakeRepository:
        def get_primary_by_name(self, name: str):
            if name == "计算机学院":
                return {"id": 1, "name": name, "status": "active"}
            return None

        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            if primary_department_id == 1 and name == "软件工程系":
                return {"id": 11, "primary_department_id": 1, "name": name, "status": "active"}
            return None

        def get_secondary_by_id(self, secondary_id: int):
            if secondary_id == 11:
                return {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "active"}
            return None

    service = DepartmentService(repository=FakeRepository())

    duplicate_primary = service.create_primary(name="计算机学院")
    duplicate_secondary = service.create_secondary(primary_department_id=1, name="软件工程系")
    invalid_status = service.update_primary_status(primary_id=1, status="archived")

    assert duplicate_primary["success"] is False
    assert duplicate_primary["code"] == "PRIMARY_DEPARTMENT_NAME_EXISTS"
    assert duplicate_secondary["success"] is False
    assert duplicate_secondary["code"] == "SECONDARY_DEPARTMENT_NAME_EXISTS"
    assert invalid_status["success"] is False
    assert invalid_status["code"] == "VALIDATION_ERROR"


def test_department_service_describe_user_department_marks_disabled_binding_without_forcing_reset():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "disabled"}
            return None

        def get_secondary_by_id(self, secondary_id: int):
            if secondary_id == 11:
                return {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "active"}
            return None

    service = DepartmentService(repository=FakeRepository())
    payload = service.describe_user_department(primary_department_id=1, secondary_department_id=11)

    assert payload["require_department_setup"] is False
    assert payload["department_effective_status"] == "disabled"
    assert payload["department_display"] == "计算机学院 / 软件工程系（已停用）"
