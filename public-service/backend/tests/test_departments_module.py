from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.core.spreadsheet import build_xlsx
from app.core.deps import AuthContext
from app.main import app
from app.modules.departments import api as department_api_module
from app.modules.departments import import_service as department_import_service_module
from app.modules.auth.repository import AuthRepository
from app.modules.departments import service as department_service
from app.modules.departments.import_service import DepartmentImportService, department_import_service
from app.modules.departments.repository import DepartmentRepository
from app.modules.departments.service import DepartmentService


def load_migration_sql(filename: str) -> str:
    repo_root = Path(__file__).resolve().parents[3]
    migration_path = repo_root / "highThinkingQA" / "server" / "database" / "migrations" / filename
    return migration_path.read_text(encoding="utf-8")


def _decode(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class _FakeRequest:
    def __init__(self, *, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = {"content-type": content_type}

    async def body(self) -> bytes:
        return self._body


def test_department_repository_reads_primary_and_secondary_rows():
    repo = DepartmentRepository(database=object())
    repo.has_user_column = lambda column_name: column_name == "secondary_department_id"
    rows = [
        {
            "primary_id": 1,
            "primary_name": "计算机学院",
            "primary_status": "active",
            "secondary_id": 11,
            "secondary_name": "软件工程系",
            "secondary_status": "active",
            "secondary_user_count": 7,
        },
        {
            "primary_id": 1,
            "primary_name": "计算机学院",
            "primary_status": "active",
            "secondary_id": 12,
            "secondary_name": "人工智能系",
            "secondary_status": "disabled",
            "secondary_user_count": 0,
        },
        {
            "primary_id": 2,
            "primary_name": "化学学院",
            "primary_status": "active",
            "secondary_id": 21,
            "secondary_name": "化工系",
            "secondary_status": "active",
            "secondary_user_count": 2,
        },
    ]
    repo._execute_query = lambda query, params=(): rows

    tree = repo.list_department_tree(include_disabled=True)

    assert tree[0]["primary_name"] == "计算机学院"
    assert tree[0]["secondary_items"][0]["name"] == "软件工程系"
    assert tree[0]["secondary_items"][0]["user_count"] == 7
    assert tree[0]["secondary_items"][1]["status"] == "disabled"
    assert tree[1]["primary_name"] == "化学学院"
    assert tree[1]["secondary_items"][0]["user_count"] == 2


def test_department_repository_defaults_user_count_when_secondary_department_column_missing():
    repo = DepartmentRepository(database=object())
    repo.has_user_column = lambda column_name: False

    def fake_execute(query, params=()):
        assert "secondary_department_id" not in query
        return [
            {
                "primary_id": 1,
                "primary_name": "计算机学院",
                "primary_status": "active",
                "secondary_id": 11,
                "secondary_name": "软件工程系",
                "secondary_status": "active",
            }
        ]

    repo._execute_query = fake_execute

    tree = repo.list_department_tree(include_disabled=True)

    assert tree[0]["secondary_items"][0]["user_count"] == 0


def test_department_service_admin_tree_maps_secondary_user_count():
    class FakeRepository:
        def list_department_tree(self, *, include_disabled: bool):
            assert include_disabled is True
            return [
                {
                    "primary_id": 1,
                    "primary_name": "计算机学院",
                    "primary_status": "active",
                    "secondary_items": [
                        {
                            "id": 11,
                            "name": "软件工程系",
                            "status": "active",
                            "user_count": 7,
                        }
                    ],
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.get_admin_tree()

    assert result["success"] is True
    assert result["data"]["items"][0]["secondary_items"][0]["user_count"] == 7


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
    assert "/api/admin/departments/secondary/{secondary_id}/users" in paths
    assert "/api/admin/departments/batch-import" in paths
    assert "/api/admin/departments/import-template" in paths


def test_admin_department_tree_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "get_admin_tree",
        lambda: {"success": True, "data": {"items": []}},
    )
    response = department_api_module.get_tree(AuthContext(user_id=1, role="admin", username="admin"))

    assert response.status_code == 200
    assert _decode(response)["data"]["items"] == []


def test_admin_secondary_department_users_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "list_secondary_users",
        lambda *, secondary_id: {
            "success": True,
            "data": {
                "secondary_department_id": secondary_id,
                "user_count": 0,
                "users": [],
            },
        },
    )

    response = department_api_module.get_secondary_users(
        11,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["secondary_department_id"] == 11


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


def test_department_import_template_contains_status_columns():
    response = department_import_service.template_response(fmt="csv")

    assert b"primary_status" in response.body
    assert b"secondary_status" in response.body


def test_department_batch_import_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_import_service_module.department_import_service,
        "import_departments",
        lambda **kwargs: {"success": True, "message": "导入完成", "data": kwargs},
    )

    request = _FakeRequest(
        body=(
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="departments.csv"\r\n'
            b"Content-Type: text/csv\r\n\r\n"
            b"primary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n\r\n"
            b"--boundary--\r\n"
        ),
        content_type="multipart/form-data; boundary=boundary",
    )

    response = asyncio.run(
        department_api_module.batch_import_departments(
            request,
            AuthContext(user_id=1, role="admin", username="admin"),
        )
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["filename"] == "departments.csv"


def test_department_import_updates_existing_statuses_and_preserves_omitted_rows():
    class FakeRepository:
        def __init__(self) -> None:
            self.primary_by_id = {
                1: {"id": 1, "name": "计算机学院", "status": "disabled"},
                2: {"id": 2, "name": "化学学院", "status": "active"},
            }
            self.primary_by_name = {item["name"]: item for item in self.primary_by_id.values()}
            self.secondary_by_id = {
                11: {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "disabled"},
                21: {"id": 21, "primary_department_id": 2, "name": "材料系", "status": "active"},
            }
            self.secondary_by_key = {
                (item["primary_department_id"], item["name"]): item
                for item in self.secondary_by_id.values()
            }
            self.next_primary_id = 3
            self.next_secondary_id = 22

        def get_primary_by_name(self, name: str):
            return self.primary_by_name.get(name)

        def get_primary_by_id(self, primary_id: int):
            return self.primary_by_id.get(primary_id)

        def create_primary(self, *, name: str):
            primary_id = self.next_primary_id
            self.next_primary_id += 1
            item = {"id": primary_id, "name": name, "status": "active"}
            self.primary_by_id[primary_id] = item
            self.primary_by_name[name] = item
            return primary_id

        def update_primary_status(self, *, primary_id: int, status: str):
            self.primary_by_id[primary_id]["status"] = status
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return self.secondary_by_key.get((primary_department_id, name))

        def get_secondary_by_id(self, secondary_id: int):
            return self.secondary_by_id.get(secondary_id)

        def create_secondary(self, *, primary_department_id: int, name: str):
            secondary_id = self.next_secondary_id
            self.next_secondary_id += 1
            item = {
                "id": secondary_id,
                "primary_department_id": primary_department_id,
                "name": name,
                "status": "active",
            }
            self.secondary_by_id[secondary_id] = item
            self.secondary_by_key[(primary_department_id, name)] = item
            return secondary_id

        def update_secondary_status(self, *, secondary_id: int, status: str):
            self.secondary_by_id[secondary_id]["status"] = status
            return 1

    service = DepartmentImportService(repository=FakeRepository())
    result = service.import_departments(
        file_bytes=(
            b"primary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n"
        ),
        filename="departments.csv",
    )

    assert result["success"] is True
    assert service._repository.primary_by_name["计算机学院"]["status"] == "active"
    assert service._repository.secondary_by_key[(2, "材料系")]["status"] == "active"


def test_department_repository_lists_users_by_secondary_department():
    repo = DepartmentRepository(database=object())
    repo.has_user_column = lambda column_name: True
    captured: dict[str, object] = {}

    def fake_execute(query, params=()):
        captured["query"] = query
        captured["params"] = params
        return [
            {
                "id": 101,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "status": "active",
            }
        ]

    repo._execute_query = fake_execute

    rows = repo.list_users_by_secondary_department(secondary_id=11)

    assert rows[0]["username"] == "alice"
    assert captured["params"] == (11,)


def test_department_repository_skips_user_lookup_when_secondary_department_column_missing():
    repo = DepartmentRepository(database=object())
    repo.has_user_column = lambda column_name: column_name != "secondary_department_id"
    repo._execute_query = lambda query, params=(): (_ for _ in ()).throw(AssertionError("should not query users"))

    rows = repo.list_users_by_secondary_department(secondary_id=11)

    assert rows == []


def test_department_repository_user_listing_drops_user_type_select_when_column_missing():
    repo = DepartmentRepository(database=object())
    repo.has_user_column = lambda column_name: column_name != "user_type"

    def fake_execute(query, params=()):
        assert "user_type" not in query
        return [
            {
                "id": 101,
                "username": "alice",
                "role": "user",
                "status": "active",
            }
        ]

    repo._execute_query = fake_execute

    rows = repo.list_users_by_secondary_department(secondary_id=11)

    assert rows[0]["username"] == "alice"


def test_department_import_accepts_xlsx_upload():
    class FakeRepository:
        def __init__(self) -> None:
            self.primary_by_id = {
                1: {"id": 1, "name": "计算机学院", "status": "disabled"},
            }
            self.primary_by_name = {item["name"]: item for item in self.primary_by_id.values()}
            self.secondary_by_id = {
                11: {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "disabled"},
            }
            self.secondary_by_key = {
                (item["primary_department_id"], item["name"]): item
                for item in self.secondary_by_id.values()
            }
            self.next_primary_id = 2
            self.next_secondary_id = 12

        def get_primary_by_name(self, name: str):
            return self.primary_by_name.get(name)

        def get_primary_by_id(self, primary_id: int):
            return self.primary_by_id.get(primary_id)

        def create_primary(self, *, name: str):
            primary_id = self.next_primary_id
            self.next_primary_id += 1
            item = {"id": primary_id, "name": name, "status": "active"}
            self.primary_by_id[primary_id] = item
            self.primary_by_name[name] = item
            return primary_id

        def update_primary_status(self, *, primary_id: int, status: str):
            self.primary_by_id[primary_id]["status"] = status
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return self.secondary_by_key.get((primary_department_id, name))

        def get_secondary_by_id(self, secondary_id: int):
            return self.secondary_by_id.get(secondary_id)

        def create_secondary(self, *, primary_department_id: int, name: str):
            secondary_id = self.next_secondary_id
            self.next_secondary_id += 1
            item = {
                "id": secondary_id,
                "primary_department_id": primary_department_id,
                "name": name,
                "status": "active",
            }
            self.secondary_by_id[secondary_id] = item
            self.secondary_by_key[(primary_department_id, name)] = item
            return secondary_id

        def update_secondary_status(self, *, secondary_id: int, status: str):
            self.secondary_by_id[secondary_id]["status"] = status
            return 1

    payload = build_xlsx(
        headers=[
            "primary_department_name",
            "primary_status",
            "secondary_department_name",
            "secondary_status",
        ],
        rows=[["计算机学院", "active", "软件工程系", "active"]],
        sheet_name="部门导入",
    )
    service = DepartmentImportService(repository=FakeRepository())

    result = service.import_departments(file_bytes=payload, filename="departments.xlsx")

    assert result["success"] is True
    assert service._repository.primary_by_name["计算机学院"]["status"] == "active"
    assert service._repository.secondary_by_key[(1, "软件工程系")]["status"] == "active"


def test_department_import_rejects_conflicting_primary_status_in_same_file():
    class FakeRepository:
        def __init__(self) -> None:
            self.primary_by_id: dict[int, dict] = {}
            self.primary_by_name: dict[str, dict] = {}
            self.secondary_by_id: dict[int, dict] = {}
            self.secondary_by_key: dict[tuple[int, str], dict] = {}
            self.next_primary_id = 1
            self.next_secondary_id = 1

        def get_primary_by_name(self, name: str):
            return self.primary_by_name.get(name)

        def get_primary_by_id(self, primary_id: int):
            return self.primary_by_id.get(primary_id)

        def create_primary(self, *, name: str):
            primary_id = self.next_primary_id
            self.next_primary_id += 1
            item = {"id": primary_id, "name": name, "status": "active"}
            self.primary_by_id[primary_id] = item
            self.primary_by_name[name] = item
            return primary_id

        def update_primary_status(self, *, primary_id: int, status: str):
            self.primary_by_id[primary_id]["status"] = status
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return self.secondary_by_key.get((primary_department_id, name))

        def get_secondary_by_id(self, secondary_id: int):
            return self.secondary_by_id.get(secondary_id)

        def create_secondary(self, *, primary_department_id: int, name: str):
            secondary_id = self.next_secondary_id
            self.next_secondary_id += 1
            item = {
                "id": secondary_id,
                "primary_department_id": primary_department_id,
                "name": name,
                "status": "active",
            }
            self.secondary_by_id[secondary_id] = item
            self.secondary_by_key[(primary_department_id, name)] = item
            return secondary_id

        def update_secondary_status(self, *, secondary_id: int, status: str):
            self.secondary_by_id[secondary_id]["status"] = status
            return 1

    service = DepartmentImportService(repository=FakeRepository())
    result = service.import_departments(
        file_bytes=(
            b"primary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,disabled,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe7\xb3\xbb,active\n"
        ),
        filename="departments.csv",
    )

    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "一级部门状态不一致" in result["data"]["details"][1]["reason"]


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


def test_department_service_lists_all_users_for_secondary_department():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            if secondary_id == 11:
                return {
                    "id": 11,
                    "primary_department_id": 1,
                    "name": "软件工程系",
                    "status": "active",
                }
            return None

        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def list_users_by_secondary_department(self, *, secondary_id: int):
            assert secondary_id == 11
            return [
                {
                    "id": 101,
                    "username": "alice",
                    "role": "user",
                    "user_type": 3,
                    "status": "active",
                },
                {
                    "id": 102,
                    "username": "bob",
                    "role": "user",
                    "user_type": 2,
                    "status": "disabled",
                },
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_users(secondary_id=11)

    assert result["success"] is True
    assert result["data"]["secondary_department_id"] == 11
    assert result["data"]["user_count"] == 2
    assert result["data"]["primary_department_name"] == "计算机学院"
    assert result["data"]["secondary_department_name"] == "软件工程系"
    assert result["data"]["users"][0]["username"] == "alice"
    assert result["data"]["users"][0]["user_type_label"] == "普通用户"
    assert result["data"]["users"][1]["user_type_label"] == "超级用户"
    assert result["data"]["users"][1]["status"] == "disabled"


def test_department_service_derives_user_type_from_role_when_column_missing():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            return {
                "id": 11,
                "primary_department_id": 1,
                "name": "软件工程系",
                "status": "active",
            }

        def get_primary_by_id(self, primary_id: int):
            return {"id": 1, "name": "计算机学院", "status": "active"}

        def list_users_by_secondary_department(self, *, secondary_id: int):
            return [
                {
                    "id": 201,
                    "username": "admin_like",
                    "role": "admin",
                    "status": "active",
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_users(secondary_id=11)

    assert result["success"] is True
    assert result["data"]["users"][0]["user_type"] == 1
    assert result["data"]["users"][0]["user_type_label"] == "管理员"


def test_department_service_derives_super_user_type_from_role_when_column_missing():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            return {
                "id": 11,
                "primary_department_id": 1,
                "name": "软件工程系",
                "status": "active",
            }

        def get_primary_by_id(self, primary_id: int):
            return {"id": 1, "name": "计算机学院", "status": "active"}

        def list_users_by_secondary_department(self, *, secondary_id: int):
            return [
                {
                    "id": 202,
                    "username": "legacy_super",
                    "role": "super",
                    "status": "active",
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_users(secondary_id=11)

    assert result["success"] is True
    assert result["data"]["users"][0]["user_type"] == 2
    assert result["data"]["users"][0]["user_type_label"] == "超级用户"


def test_department_service_returns_not_found_for_missing_secondary_department():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            return None

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_users(secondary_id=999)

    assert result["success"] is False
    assert result["code"] == "SECONDARY_DEPARTMENT_NOT_FOUND"
