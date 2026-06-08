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
    repo.has_personnel_column = lambda column_name: column_name == "secondary_department_id"
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
    repo.has_personnel_column = lambda column_name: False

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


def test_department_repository_admin_tree_includes_secondary_and_tertiary_counts():
    repo = DepartmentRepository(database=object())
    repo.has_table = lambda table_name: table_name == "tertiary_departments"
    repo.has_personnel_column = lambda column_name: column_name in {
        "secondary_department_id",
        "tertiary_department_id",
    }
    repo._execute_query = lambda query, params=(): [
        {
            "primary_id": 1,
            "primary_name": "计算机学院",
            "primary_status": "active",
            "secondary_id": 11,
            "secondary_name": "软件工程系",
            "secondary_status": "active",
            "secondary_user_count": 7,
            "secondary_legacy_user_count": 2,
            "tertiary_id": 111,
            "tertiary_name": "软件工程教研室",
            "tertiary_status": "active",
            "tertiary_user_count": 5,
        }
    ]

    tree = repo.list_department_tree(include_disabled=True)

    assert tree[0]["secondary_items"][0]["user_count"] == 7
    assert tree[0]["secondary_items"][0]["legacy_user_count"] == 2
    assert tree[0]["secondary_items"][0]["tertiary_items"][0]["user_count"] == 5


def test_department_service_selectable_tree_allows_secondary_without_tertiary():
    class FakeRepository:
        def list_department_tree(self, *, include_disabled: bool):
            assert include_disabled is False
            return [
                {
                    "primary_id": 1,
                    "primary_name": "计算机学院",
                    "secondary_items": [
                        {
                            "id": 11,
                            "name": "软件工程系",
                            "status": "active",
                            "tertiary_items": [],
                        }
                    ],
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.get_selectable_tree()

    secondary = result["data"]["items"][0]["secondary_items"][0]
    assert secondary["selectable"] is True
    assert secondary["disabled_reason"] is None


def test_department_service_describes_legacy_two_level_user_without_forcing_completion():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def get_secondary_by_id(self, secondary_id: int):
            if secondary_id == 11:
                return {
                    "id": 11,
                    "primary_department_id": 1,
                    "name": "软件工程系",
                    "status": "active",
                }
            return None

        def get_tertiary_by_id(self, tertiary_id: int):
            return None

    service = DepartmentService(repository=FakeRepository())

    result = service.describe_user_department(
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=None,
    )

    assert result["department_completion_level"] == "legacy_two_level_complete"
    assert result["require_department_setup"] is False


def test_department_service_describes_primary_direct_user_without_forcing_completion():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def get_secondary_by_id(self, secondary_id: int):
            return None

        def get_tertiary_by_id(self, tertiary_id: int):
            return None

    service = DepartmentService(repository=FakeRepository())

    result = service.describe_user_department(
        primary_department_id=1,
        secondary_department_id=None,
        tertiary_department_id=None,
    )

    assert result["department_display"] == "计算机学院"
    assert result["department_completion_level"] == "primary_complete"
    assert result["require_department_setup"] is False


def test_department_service_validates_primary_only_selection_when_partial_levels_allowed():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def get_secondary_by_id(self, secondary_id: int):
            return None

        def get_tertiary_by_id(self, tertiary_id: int):
            return None

    service = DepartmentService(repository=FakeRepository())

    result = service.validate_department_selection(
        primary_department_id=1,
        secondary_department_id=None,
        tertiary_department_id=None,
        require_active=True,
        allow_empty=False,
        allow_legacy_two_level=True,
    )

    assert result["success"] is True
    assert result["data"]["primary_department_id"] == 1
    assert result["data"]["secondary_department_id"] is None
    assert result["data"]["require_department_setup"] is False


def test_department_service_resolves_primary_only_name_when_partial_levels_allowed():
    class FakeRepository:
        def get_primary_by_name(self, name: str):
            if name == "计算机学院":
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def get_secondary_by_id(self, secondary_id: int):
            return None

        def get_tertiary_by_id(self, tertiary_id: int):
            return None

    service = DepartmentService(repository=FakeRepository())

    result = service.resolve_by_names(
        primary_name="计算机学院",
        secondary_name="",
        tertiary_name="",
        active_only=True,
        allow_legacy_two_level=True,
    )

    assert result["success"] is True
    assert result["data"]["primary_department_id"] == 1
    assert result["data"]["secondary_department_id"] is None


def test_department_service_resolves_or_creates_missing_department_path():
    class FakeRepository:
        def __init__(self):
            self.primary_by_name: dict[str, dict] = {}
            self.secondary_by_key: dict[tuple[int, str], dict] = {}
            self.tertiary_by_key: dict[tuple[int, str], dict] = {}

        def get_primary_by_name(self, name: str):
            return self.primary_by_name.get(name)

        def get_primary_by_id(self, primary_id: int):
            for primary in self.primary_by_name.values():
                if primary["id"] == primary_id:
                    return primary
            return None

        def create_primary(self, *, name: str):
            primary = {"id": 1, "name": name, "status": "active"}
            self.primary_by_name[name] = primary
            return primary["id"]

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return self.secondary_by_key.get((primary_department_id, name))

        def get_secondary_by_id(self, secondary_id: int):
            for secondary in self.secondary_by_key.values():
                if secondary["id"] == secondary_id:
                    return secondary
            return None

        def create_secondary(self, *, primary_department_id: int, name: str):
            secondary = {
                "id": 11,
                "primary_department_id": primary_department_id,
                "name": name,
                "status": "active",
            }
            self.secondary_by_key[(primary_department_id, name)] = secondary
            return secondary["id"]

        def get_tertiary_by_name(self, *, secondary_department_id: int, name: str):
            return self.tertiary_by_key.get((secondary_department_id, name))

        def get_tertiary_by_id(self, tertiary_id: int):
            for tertiary in self.tertiary_by_key.values():
                if tertiary["id"] == tertiary_id:
                    return tertiary
            return None

        def create_tertiary(self, *, secondary_department_id: int, name: str):
            tertiary = {"id": 111, "secondary_department_id": secondary_department_id, "name": name, "status": "active"}
            self.tertiary_by_key[(secondary_department_id, name)] = tertiary
            return tertiary["id"]

    service = DepartmentService(repository=FakeRepository())

    result = service.resolve_or_create_by_names(
        primary_name="新能源事业部",
        secondary_name="电芯研发部",
        tertiary_name="材料实验室",
        active_only=True,
        allow_legacy_two_level=True,
    )

    assert result["success"] is True
    assert result["data"]["primary_department_id"] == 1
    assert result["data"]["secondary_department_id"] == 11
    assert result["data"]["tertiary_department_id"] == 111
    assert result["data"]["created_departments"] == {
        "primary": 1,
        "secondary": 1,
        "tertiary": 1,
        "total": 3,
    }


def test_department_service_resolve_or_create_rejects_disabled_existing_department():
    class FakeRepository:
        def get_primary_by_name(self, name: str):
            return {"id": 1, "name": name, "status": "disabled"}

    service = DepartmentService(repository=FakeRepository())

    result = service.resolve_or_create_by_names(
        primary_name="计算机学院",
        secondary_name="",
        tertiary_name="",
        active_only=True,
        allow_legacy_two_level=True,
    )

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_DISABLED"


def test_auth_repository_select_user_fields_include_department_columns_when_present():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
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


def test_auth_repository_select_user_fields_include_tertiary_department_column_when_present():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
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
        "tertiary_department_id",
        "created_at",
        "updated_at",
    }

    fields = repo._select_user_fields(include_password=True)

    assert "tertiary_department_id" in fields


def test_auth_repository_create_user_includes_tertiary_department_column_when_present():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
        "username",
        "password_hash",
        "role",
        "status",
        "user_type",
        "is_first_login",
        "must_set_security_questions",
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
    }
    captured: dict[str, object] = {}

    def fake_execute_update(query: str, params: tuple[object, ...] = ()) -> int:
        captured["query"] = query
        captured["params"] = params
        return 1

    repo._execute_update = fake_execute_update

    repo.create_user(
        username="alice",
        password_hash="hash",
        user_type=3,
        is_first_login=False,
        must_set_security_questions=True,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert "tertiary_department_id" in str(captured["query"])
    assert captured["params"] == ("alice", "hash", "user", "active", 3, 0, 1, 1, 11, 111)


def test_auth_repository_update_user_department_writes_tertiary_column_when_present():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
    }
    captured: dict[str, object] = {}

    def fake_execute_update(query: str, params: tuple[object, ...] = ()) -> int:
        captured["query"] = query
        captured["params"] = params
        return 1

    repo._execute_update = fake_execute_update

    repo.update_user_department(
        user_id=101,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert "tertiary_department_id" in str(captured["query"])
    assert captured["params"] == (1, 11, 111, 101)


def test_auth_repository_list_users_includes_tertiary_department_id_when_present():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
        "id",
        "username",
        "role",
        "status",
        "user_type",
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
        "created_at",
        "updated_at",
    }

    def fake_execute(query: str, params: tuple[object, ...] = ()):
        assert "tertiary_department_id" in query
        return []

    repo._execute_query = fake_execute

    assert repo.list_users(offset=0, limit=10) == []


def test_auth_repository_reuses_schema_columns_within_ttl_and_refreshes_after_expiry():
    repo = AuthRepository(database=object())
    current_time = [100.0]
    repo._now = lambda: current_time[0]
    repo._schema_cache_ttl_seconds = 1.0
    columns = [
        {
            "id",
            "username",
            "password_hash",
            "role",
            "status",
            "primary_department_id",
            "secondary_department_id",
            "created_at",
            "updated_at",
        },
        {
            "id",
            "username",
            "password_hash",
            "role",
            "status",
            "primary_department_id",
            "secondary_department_id",
            "tertiary_department_id",
            "created_at",
            "updated_at",
        },
    ]
    load_count = {"value": 0}

    def fake_load_columns():
        load_count["value"] += 1
        return columns.pop(0)

    repo._load_columns = fake_load_columns

    assert repo.has_column("tertiary_department_id") is False
    assert repo.has_column("secondary_department_id") is True
    assert load_count["value"] == 1

    current_time[0] = 101.5
    assert repo.has_column("tertiary_department_id") is True
    assert load_count["value"] == 2


def test_department_schema_helpers_expect_unique_and_fk_structure():
    ddl = load_migration_sql("20260416_01_user_departments.sql")

    assert "UNIQUE" in ddl
    assert "FOREIGN KEY" in ddl
    assert "primary_department_id" in ddl
    assert "secondary_department_id" in ddl


def test_tertiary_department_migration_adds_table_and_user_column():
    repo_root = Path(__file__).resolve().parents[3]
    migration_path = repo_root / "highThinkingQA" / "server" / "database" / "migrations" / "20260418_01_department_tertiary.sql"

    assert migration_path.exists()

    ddl = load_migration_sql("20260418_01_department_tertiary.sql")

    assert "CREATE TABLE IF NOT EXISTS tertiary_departments" in ddl
    assert "tertiary_department_id" in ddl
    assert "uq_tertiary_departments_secondary_name" in ddl
    assert "fk_users_tertiary_department" in ddl


def test_department_routes_registered():
    routes_by_path = {}
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        routes_by_path.setdefault(route.path, set()).update(set(getattr(route, "methods", set())) - {"HEAD"})
    paths = set(routes_by_path)

    assert "/api/admin/departments/tree" in paths
    assert "/api/admin/departments/primary" in paths
    assert routes_by_path["/api/admin/departments/primary/{primary_id}"] == {"PUT", "DELETE"}
    assert routes_by_path["/api/admin/departments/secondary/{secondary_id}"] == {"PUT", "DELETE"}
    assert "/api/admin/departments/secondary/{secondary_id}/users" in paths
    assert "/api/admin/departments/secondary/{secondary_id}/legacy-users" in paths
    assert "/api/admin/departments/tertiary" in paths
    assert routes_by_path["/api/admin/departments/tertiary/{tertiary_id}"] == {"PUT", "DELETE"}
    assert "/api/admin/departments/tertiary/{tertiary_id}/users" in paths
    assert "/api/admin/departments/batch-delete" in paths
    assert "/api/admin/departments/batch-import" in paths
    assert "/api/admin/departments/import-template" in paths
    assert "/api/admin/departments/primary/{primary_id}/status" not in paths
    assert "/api/admin/departments/secondary/{secondary_id}/status" not in paths
    assert "/api/admin/departments/tertiary/{tertiary_id}/status" not in paths


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
                "member_count": 0,
                "users": [],
                "members": [],
            },
        },
    )

    response = department_api_module.get_secondary_users(
        11,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["secondary_department_id"] == 11


def test_admin_secondary_legacy_users_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "list_secondary_legacy_users",
        lambda *, secondary_id: {
            "success": True,
            "data": {
                "secondary_department_id": secondary_id,
                "user_count": 0,
                "member_count": 0,
                "users": [],
                "members": [],
            },
        },
    )

    response = department_api_module.get_secondary_legacy_users(
        11,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["secondary_department_id"] == 11


def test_admin_primary_direct_users_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "list_primary_direct_users",
        lambda *, primary_id: {
            "success": True,
            "data": {
                "primary_department_id": primary_id,
                "user_count": 0,
                "member_count": 0,
                "users": [],
                "members": [],
            },
        },
    )

    response = department_api_module.get_primary_direct_users(
        1,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["primary_department_id"] == 1


def test_admin_secondary_direct_users_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "list_secondary_direct_users",
        lambda *, secondary_id: {
            "success": True,
            "data": {
                "secondary_department_id": secondary_id,
                "user_count": 0,
                "member_count": 0,
                "users": [],
                "members": [],
            },
        },
    )

    response = department_api_module.get_secondary_direct_users(
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
        "create_secondary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "rename_secondary",
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
    create_secondary_response = department_api_module.create_secondary(
        department_api_module.SecondaryDepartmentCreateRequest(primary_department_id=1, name="软件工程系"),
        context,
    )
    rename_secondary_response = department_api_module.rename_secondary(
        11,
        department_api_module.SecondaryDepartmentRenameRequest(name="计算机系"),
        context,
    )

    assert create_primary_response.status_code == 201
    assert rename_primary_response.status_code == 200
    assert create_secondary_response.status_code == 201
    assert rename_secondary_response.status_code == 200


def test_admin_tertiary_department_mutation_contracts(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "create_tertiary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "rename_tertiary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )

    context = AuthContext(user_id=1, role="admin", username="admin")
    create_tertiary_response = department_api_module.create_tertiary(
        department_api_module.TertiaryDepartmentCreateRequest(secondary_department_id=11, name="软件工程教研室"),
        context,
    )
    rename_tertiary_response = department_api_module.rename_tertiary(
        111,
        department_api_module.TertiaryDepartmentRenameRequest(name="人工智能教研室"),
        context,
    )

    assert create_tertiary_response.status_code == 201
    assert rename_tertiary_response.status_code == 200


def test_admin_tertiary_department_users_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "list_tertiary_users",
        lambda *, tertiary_id: {
            "success": True,
            "data": {
                "tertiary_department_id": tertiary_id,
                "user_count": 0,
                "users": [],
            },
        },
    )

    response = department_api_module.get_tertiary_users(
        111,
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["tertiary_department_id"] == 111


def test_admin_department_delete_route_contracts(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "delete_primary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "delete_secondary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )
    monkeypatch.setattr(
        department_service.department_service,
        "delete_tertiary",
        lambda **kwargs: {"success": True, "data": kwargs},
    )

    context = AuthContext(user_id=1, role="admin", username="admin")

    primary_response = department_api_module.delete_primary(1, context)
    secondary_response = department_api_module.delete_secondary(11, context)
    tertiary_response = department_api_module.delete_tertiary(111, context)

    assert primary_response.status_code == 200
    assert _decode(primary_response)["data"]["primary_id"] == 1
    assert secondary_response.status_code == 200
    assert _decode(secondary_response)["data"]["secondary_id"] == 11
    assert tertiary_response.status_code == 200
    assert _decode(tertiary_response)["data"]["tertiary_id"] == 111


def test_admin_department_batch_delete_route_contract(monkeypatch):
    captured = {}

    def fake_batch_delete_departments(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "data": {
                "summary": {"total": 2, "success": 2, "failed": 0, "skipped": 0},
                "details": [],
            },
        }

    monkeypatch.setattr(department_service.department_service, "batch_delete_departments", fake_batch_delete_departments)

    response = department_api_module.batch_delete_departments(
        department_api_module.DepartmentBatchDeleteRequest(
            items=[
                department_api_module.DepartmentBatchDeleteItem(level="primary", id=1),
                department_api_module.DepartmentBatchDeleteItem(level="tertiary", id=111),
            ]
        ),
        AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert captured["items"] == [
        {"level": "primary", "id": 1},
        {"level": "tertiary", "id": 111},
    ]


def test_admin_department_force_delete_route_contracts(monkeypatch):
    calls = []

    def fake_force_delete_department(**kwargs):
        calls.append(kwargs)
        return {"success": True, "data": {"summary": {"deleted_primary": 1}}}

    def fake_batch_force_delete_departments(**kwargs):
        calls.append(kwargs)
        return {"success": True, "data": {"summary": {"deleted_primary": 1}, "details": []}}

    monkeypatch.setattr(department_service.department_service, "force_delete_department", fake_force_delete_department)
    monkeypatch.setattr(
        department_service.department_service,
        "batch_force_delete_departments",
        fake_batch_force_delete_departments,
    )

    context = AuthContext(user_id=1, role="admin", username="admin")
    force_response = department_api_module.force_delete_department(
        level="primary",
        department_id=1,
        payload=department_api_module.DepartmentForceDeleteRequest(admin_password="secret"),
        _context=context,
    )
    batch_response = department_api_module.batch_force_delete_departments(
        payload=department_api_module.DepartmentBatchForceDeleteRequest(
            items=[
                department_api_module.DepartmentBatchDeleteItem(level="primary", id=1),
                department_api_module.DepartmentBatchDeleteItem(level="tertiary", id=111),
            ],
            admin_password="secret",
        ),
        _context=context,
    )

    assert force_response.status_code == 200
    assert batch_response.status_code == 200
    assert calls == [
        {"level": "primary", "department_id": 1, "actor_user_id": 1, "admin_password": "secret"},
        {
            "items": [{"level": "primary", "id": 1}, {"level": "tertiary", "id": 111}],
            "actor_user_id": 1,
            "admin_password": "secret",
        },
    ]


def test_department_service_batch_delete_requires_selection():
    service = DepartmentService(repository=object())

    result = service.batch_delete_departments(items=[])

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"


def test_department_service_batch_delete_mixed_levels_in_child_first_order():
    class FakeRepository:
        def __init__(self) -> None:
            self.delete_calls = []

        def get_primary_by_id(self, primary_id: int):
            return {"id": primary_id, "name": f"一级{primary_id}", "status": "active"} if primary_id == 1 else None

        def get_secondary_by_id(self, secondary_id: int):
            return {"id": secondary_id, "primary_department_id": 1, "name": f"二级{secondary_id}", "status": "active"} if secondary_id == 11 else None

        def get_tertiary_by_id(self, tertiary_id: int):
            return {"id": tertiary_id, "secondary_department_id": 11, "name": f"三级{tertiary_id}", "status": "active"} if tertiary_id == 111 else None

        def count_secondary_departments_by_primary(self, *, primary_id: int):
            return 0

        def count_users_by_primary_department(self, *, primary_id: int):
            return 0

        def count_personnel_by_primary_department(self, *, primary_id: int):
            return 0

        def count_tertiary_departments_by_secondary(self, *, secondary_id: int):
            return 0

        def count_users_by_secondary_department(self, *, secondary_id: int):
            return 0

        def count_personnel_by_secondary_department(self, *, secondary_id: int):
            return 0

        def count_users_by_tertiary_department(self, *, tertiary_id: int):
            return 0

        def count_personnel_by_tertiary_department(self, *, tertiary_id: int):
            return 0

        def delete_primary(self, *, primary_id: int):
            self.delete_calls.append(("primary", primary_id))
            return 1

        def delete_secondary(self, *, secondary_id: int):
            self.delete_calls.append(("secondary", secondary_id))
            return 1

        def delete_tertiary(self, *, tertiary_id: int):
            self.delete_calls.append(("tertiary", tertiary_id))
            return 1

    repository = FakeRepository()
    service = DepartmentService(repository=repository)

    result = service.batch_delete_departments(
        items=[
            {"level": "primary", "id": 1},
            {"level": "tertiary", "id": 111},
            {"level": "secondary", "id": 11},
            {"level": "primary", "id": 1},
            {"level": "bad", "id": 99},
        ]
    )

    assert result["success"] is True
    assert repository.delete_calls == [("tertiary", 111), ("secondary", 11), ("primary", 1)]
    assert result["data"]["summary"] == {"total": 4, "success": 3, "failed": 1, "skipped": 0}
    assert result["data"]["details"][0]["level"] == "tertiary"
    assert result["data"]["details"][-1]["code"] == "VALIDATION_ERROR"


def test_department_service_deletes_empty_tertiary_department():
    class FakeRepository:
        def __init__(self) -> None:
            self.deleted_id = None

        def get_tertiary_by_id(self, tertiary_id: int):
            return {"id": tertiary_id, "secondary_department_id": 11, "name": "人工智能教研室", "status": "active"}

        def count_users_by_tertiary_department(self, *, tertiary_id: int):
            return 0

        def count_personnel_by_tertiary_department(self, *, tertiary_id: int):
            return 0

        def delete_tertiary(self, *, tertiary_id: int):
            self.deleted_id = tertiary_id
            return 1

    service = DepartmentService(repository=FakeRepository())

    result = service.delete_tertiary(tertiary_id=111)

    assert result["success"] is True
    assert result["data"]["id"] == 111
    assert service._repository.deleted_id == 111


def test_department_service_rejects_secondary_delete_when_children_or_bindings_exist():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            return {"id": secondary_id, "primary_department_id": 1, "name": "软件工程系", "status": "active"}

        def count_tertiary_departments_by_secondary(self, *, secondary_id: int):
            return 1

        def count_users_by_secondary_department(self, *, secondary_id: int):
            return 0

        def count_personnel_by_secondary_department(self, *, secondary_id: int):
            return 0

        def delete_secondary(self, *, secondary_id: int):
            raise AssertionError("should not delete non-empty secondary department")

    service = DepartmentService(repository=FakeRepository())

    result = service.delete_secondary(secondary_id=11)

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_IN_USE"
    assert "三级部门" in result["error"]


def test_department_service_rejects_primary_delete_when_secondary_departments_exist():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            return {"id": primary_id, "name": "计算机学院", "status": "active"}

        def count_secondary_departments_by_primary(self, *, primary_id: int):
            return 1

        def count_users_by_primary_department(self, *, primary_id: int):
            return 0

        def count_personnel_by_primary_department(self, *, primary_id: int):
            return 0

        def delete_primary(self, *, primary_id: int):
            raise AssertionError("should not delete non-empty primary department")

    service = DepartmentService(repository=FakeRepository())

    result = service.delete_primary(primary_id=1)

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_IN_USE"
    assert "二级部门" in result["error"]


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

    first_line = response.body.decode("utf-8-sig").splitlines()[0]
    assert first_line == "一级部门名称,一级状态,二级部门名称,二级状态,三级部门名称,三级状态"
    assert b"primary_status" not in response.body
    assert b"secondary_status" not in response.body
    assert b"tertiary_department_name" not in response.body
    assert b"tertiary_status" not in response.body


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


def test_department_import_accepts_legacy_english_headers():
    class FakeRepository:
        def __init__(self) -> None:
            self.primary_names: list[str] = []
            self.secondary_names: list[str] = []

        def get_primary_by_name(self, name: str):
            return None

        def create_primary(self, *, name: str):
            self.primary_names.append(name)
            return 1

        def update_primary_status(self, *, primary_id: int, status: str):
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return None

        def create_secondary(self, *, primary_department_id: int, name: str):
            self.secondary_names.append(name)
            return 11

        def update_secondary_status(self, *, secondary_id: int, status: str):
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


def test_department_import_skips_existing_unchanged_department_path():
    class FakeRepository:
        def __init__(self) -> None:
            self.primary_by_id = {
                1: {"id": 1, "name": "计算机学院", "status": "active"},
            }
            self.primary_by_name = {item["name"]: item for item in self.primary_by_id.values()}
            self.secondary_by_id = {
                11: {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "active"},
            }
            self.secondary_by_key = {
                (item["primary_department_id"], item["name"]): item
                for item in self.secondary_by_id.values()
            }
            self.tertiary_by_id = {
                111: {"id": 111, "secondary_department_id": 11, "name": "智能软件实验室", "status": "active"},
            }
            self.tertiary_by_key = {
                (item["secondary_department_id"], item["name"]): item
                for item in self.tertiary_by_id.values()
            }
            self.create_calls = 0
            self.update_calls = 0

        def get_primary_by_name(self, name: str):
            return self.primary_by_name.get(name)

        def create_primary(self, *, name: str):
            self.create_calls += 1
            return 2

        def update_primary_status(self, *, primary_id: int, status: str):
            self.update_calls += 1
            self.primary_by_id[primary_id]["status"] = status
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return self.secondary_by_key.get((primary_department_id, name))

        def create_secondary(self, *, primary_department_id: int, name: str):
            self.create_calls += 1
            return 12

        def update_secondary_status(self, *, secondary_id: int, status: str):
            self.update_calls += 1
            self.secondary_by_id[secondary_id]["status"] = status
            return 1

        def get_tertiary_by_name(self, *, secondary_department_id: int, name: str):
            return self.tertiary_by_key.get((secondary_department_id, name))

        def create_tertiary(self, *, secondary_department_id: int, name: str):
            self.create_calls += 1
            return 112

        def update_tertiary_status(self, *, tertiary_id: int, status: str):
            self.update_calls += 1
            self.tertiary_by_id[tertiary_id]["status"] = status
            return 1

    repo = FakeRepository()
    service = DepartmentImportService(repository=repo)
    result = service.import_departments(
        file_bytes=(
            b"primary_department_name,primary_status,secondary_department_name,secondary_status,tertiary_department_name,tertiary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active,\xe6\x99\xba\xe8\x83\xbd\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4,active\n"
        ),
        filename="departments.csv",
    )

    assert result["success"] is True
    assert result["data"]["summary"] == {"total": 1, "success": 0, "failed": 0, "skipped": 1}
    assert result["data"]["details"][0]["status"] == "skipped"
    assert "已存在且未变化" in result["data"]["details"][0]["reason"]
    assert repo.create_calls == 0
    assert repo.update_calls == 0


def test_department_import_allows_secondary_without_tertiary_when_tertiary_columns_empty():
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
            b"primary_department_name,primary_status,secondary_department_name,secondary_status,tertiary_department_name,tertiary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active,,\n"
        ),
        filename="departments.csv",
    )

    assert result["success"] is True
    assert result["data"]["summary"]["success"] == 1


def test_department_import_rejects_half_filled_tertiary_columns():
    class FakeRepository:
        def get_primary_by_name(self, name: str):
            return None

        def create_primary(self, *, name: str):
            return 1

        def update_primary_status(self, *, primary_id: int, status: str):
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return None

        def create_secondary(self, *, primary_department_id: int, name: str):
            return 11

        def update_secondary_status(self, *, secondary_id: int, status: str):
            return 1

    service = DepartmentImportService(repository=FakeRepository())
    result = service.import_departments(
        file_bytes=(
            b"primary_department_name,primary_status,secondary_department_name,secondary_status,tertiary_department_name,tertiary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4,\n"
        ),
        filename="departments.csv",
    )

    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
    assert "三级部门" in result["data"]["details"][0]["reason"]


def test_department_import_marks_tertiary_creation_failure_failed_and_continues():
    class FakeRepository:
        def __init__(self) -> None:
            self.primary_by_name: dict[str, dict] = {}
            self.secondary_by_key: dict[tuple[int, str], dict] = {}
            self.tertiary_by_key: dict[tuple[int, str], dict] = {}
            self.next_primary_id = 1
            self.next_secondary_id = 11
            self.next_tertiary_id = 111

        def get_primary_by_name(self, name: str):
            return self.primary_by_name.get(name)

        def create_primary(self, *, name: str):
            primary_id = self.next_primary_id
            self.next_primary_id += 1
            self.primary_by_name[name] = {"id": primary_id, "name": name, "status": "active"}
            return primary_id

        def update_primary_status(self, *, primary_id: int, status: str):
            return 1

        def get_secondary_by_name(self, *, primary_department_id: int, name: str):
            return self.secondary_by_key.get((primary_department_id, name))

        def create_secondary(self, *, primary_department_id: int, name: str):
            secondary_id = self.next_secondary_id
            self.next_secondary_id += 1
            self.secondary_by_key[(primary_department_id, name)] = {
                "id": secondary_id,
                "primary_department_id": primary_department_id,
                "name": name,
                "status": "active",
            }
            return secondary_id

        def update_secondary_status(self, *, secondary_id: int, status: str):
            return 1

        def get_tertiary_by_name(self, *, secondary_department_id: int, name: str):
            return self.tertiary_by_key.get((secondary_department_id, name))

        def create_tertiary(self, *, secondary_department_id: int, name: str):
            if name == "人工智能实验室":
                return 0
            tertiary_id = self.next_tertiary_id
            self.next_tertiary_id += 1
            self.tertiary_by_key[(secondary_department_id, name)] = {
                "id": tertiary_id,
                "secondary_department_id": secondary_department_id,
                "name": name,
                "status": "active",
            }
            return tertiary_id

        def update_tertiary_status(self, *, tertiary_id: int, status: str):
            return 1

    service = DepartmentImportService(repository=FakeRepository())
    result = service.import_departments(
        file_bytes=(
            b"primary_department_name,primary_status,secondary_department_name,secondary_status,tertiary_department_name,tertiary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4,active\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active,\xe6\x99\xba\xe8\x83\xbd\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xae\x9e\xe9\xaa\x8c\xe5\xae\xa4,active\n"
        ),
        filename="departments.csv",
    )

    assert result["success"] is True
    assert result["data"]["summary"] == {"total": 2, "success": 1, "failed": 1, "skipped": 0}
    assert result["data"]["details"][0]["status"] == "failed"
    assert "三级部门创建失败" in result["data"]["details"][0]["reason"]
    assert result["data"]["details"][1]["status"] == "success"


def test_department_repository_lists_personnel_by_secondary_department():
    repo = DepartmentRepository(database=object())
    repo.has_personnel_column = lambda column_name: True
    captured: dict[str, object] = {}

    def fake_execute(query, params=()):
        captured["query"] = query
        captured["params"] = params
        return [
            {
                "id": 101,
                "employee_no": "P001",
                "full_name": "张三",
                "status": "active",
            }
        ]

    repo._execute_query = fake_execute

    rows = repo.list_personnel_by_secondary_department(secondary_id=11)

    assert rows[0]["employee_no"] == "P001"
    assert "personnel_records" in str(captured["query"])
    assert captured["params"] == (11,)


def test_department_repository_skips_personnel_lookup_when_secondary_department_column_missing():
    repo = DepartmentRepository(database=object())
    repo.has_personnel_column = lambda column_name: column_name != "secondary_department_id"
    repo._execute_query = lambda query, params=(): (_ for _ in ()).throw(AssertionError("should not query personnel"))

    rows = repo.list_personnel_by_secondary_department(secondary_id=11)

    assert rows == []


def test_department_repository_reuses_schema_metadata_within_ttl_and_refreshes_after_expiry():
    repo = DepartmentRepository(database=object())
    current_time = [200.0]
    repo._now = lambda: current_time[0]
    repo._schema_cache_ttl_seconds = 1.0
    tables = [
        {"primary_departments", "secondary_departments", "users"},
        {"primary_departments", "secondary_departments", "users", "tertiary_departments"},
    ]
    user_columns = [
        {"id", "secondary_department_id"},
        {"id", "secondary_department_id", "tertiary_department_id"},
    ]
    table_load_count = {"value": 0}
    user_column_load_count = {"value": 0}

    def fake_load_tables():
        table_load_count["value"] += 1
        return tables.pop(0)

    def fake_load_user_columns():
        user_column_load_count["value"] += 1
        return user_columns.pop(0)

    repo._load_tables = fake_load_tables
    repo._load_user_columns = fake_load_user_columns

    assert repo.has_table("tertiary_departments") is False
    assert repo.has_table("users") is True
    assert table_load_count["value"] == 1

    assert repo.has_user_column("tertiary_department_id") is False
    assert repo.has_user_column("secondary_department_id") is True
    assert user_column_load_count["value"] == 1

    current_time[0] = 201.5
    assert repo.has_table("tertiary_departments") is True
    assert repo.has_user_column("tertiary_department_id") is True
    assert table_load_count["value"] == 2
    assert user_column_load_count["value"] == 2


def test_department_repository_personnel_listing_drops_remarks_select_when_column_missing():
    repo = DepartmentRepository(database=object())
    repo.has_personnel_column = lambda column_name: column_name != "remarks"

    def fake_execute(query, params=()):
        assert "remarks" not in query
        return [
            {
                "id": 101,
                "employee_no": "P001",
                "full_name": "张三",
                "status": "active",
            }
        ]

    repo._execute_query = fake_execute

    rows = repo.list_personnel_by_secondary_department(secondary_id=11)

    assert rows[0]["full_name"] == "张三"


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
            self.tertiary_by_id = {
                111: {"id": 111, "secondary_department_id": 11, "name": "人工智能实验室", "status": "disabled"},
            }
            self.tertiary_by_key = {
                (item["secondary_department_id"], item["name"]): item
                for item in self.tertiary_by_id.values()
            }
            self.next_primary_id = 2
            self.next_secondary_id = 12
            self.next_tertiary_id = 112

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

        def get_tertiary_by_name(self, *, secondary_department_id: int, name: str):
            return self.tertiary_by_key.get((secondary_department_id, name))

        def create_tertiary(self, *, secondary_department_id: int, name: str):
            tertiary_id = self.next_tertiary_id
            self.next_tertiary_id += 1
            item = {
                "id": tertiary_id,
                "secondary_department_id": secondary_department_id,
                "name": name,
                "status": "active",
            }
            self.tertiary_by_id[tertiary_id] = item
            self.tertiary_by_key[(secondary_department_id, name)] = item
            return tertiary_id

        def update_tertiary_status(self, *, tertiary_id: int, status: str):
            self.tertiary_by_id[tertiary_id]["status"] = status
            return 1

    payload = build_xlsx(
        headers=[
            "primary_department_name",
            "primary_status",
            "secondary_department_name",
            "secondary_status",
            "tertiary_department_name",
            "tertiary_status",
        ],
        rows=[["计算机学院", "active", "软件工程系", "active", "人工智能实验室", "active"]],
        sheet_name="部门导入",
    )
    service = DepartmentImportService(repository=FakeRepository())

    result = service.import_departments(file_bytes=payload, filename="departments.xlsx")

    assert result["success"] is True
    assert service._repository.primary_by_name["计算机学院"]["status"] == "active"
    assert service._repository.secondary_by_key[(1, "软件工程系")]["status"] == "active"
    assert service._repository.tertiary_by_key[(11, "人工智能实验室")]["status"] == "active"


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


def test_department_service_create_and_mutate_tertiary():
    class FakeRepository:
        def __init__(self):
            self.primary = {
                1: {"id": 1, "name": "计算机学院", "status": "active"},
            }
            self.secondary = {
                11: {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "active"},
            }
            self.tertiary = {
                111: {"id": 111, "secondary_department_id": 11, "name": "软件工程教研室", "status": "active"},
            }

        def get_primary_by_id(self, primary_id: int):
            return self.primary.get(primary_id)

        def get_secondary_by_id(self, secondary_id: int):
            return self.secondary.get(secondary_id)

        def get_tertiary_by_id(self, tertiary_id: int):
            return self.tertiary.get(tertiary_id)

        def get_tertiary_by_name(self, *, secondary_department_id: int, name: str):
            return next(
                (
                    item
                    for item in self.tertiary.values()
                    if item["secondary_department_id"] == secondary_department_id and item["name"] == name
                ),
                None,
            )

        def create_tertiary(self, *, secondary_department_id: int, name: str):
            self.tertiary[112] = {
                "id": 112,
                "secondary_department_id": secondary_department_id,
                "name": name,
                "status": "active",
            }
            return 112

        def update_tertiary_name(self, *, tertiary_id: int, name: str):
            self.tertiary[tertiary_id]["name"] = name
            return 1

        def update_tertiary_status(self, *, tertiary_id: int, status: str):
            self.tertiary[tertiary_id]["status"] = status
            return 1

    service = DepartmentService(repository=FakeRepository())

    created_tertiary = service.create_tertiary(secondary_department_id=11, name="人工智能教研室")
    renamed_tertiary = service.rename_tertiary(tertiary_id=111, name="软件工程实验室")
    disabled_tertiary = service.update_tertiary_status(tertiary_id=111, status="disabled")

    assert created_tertiary["success"] is True
    assert created_tertiary["data"]["name"] == "人工智能教研室"
    assert renamed_tertiary["data"]["name"] == "软件工程实验室"
    assert disabled_tertiary["data"]["status"] == "disabled"


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


def test_department_service_rejects_duplicate_tertiary_name():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            if secondary_id == 11:
                return {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "active"}
            return None

        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def get_tertiary_by_name(self, *, secondary_department_id: int, name: str):
            if secondary_department_id == 11 and name == "软件工程教研室":
                return {"id": 111, "secondary_department_id": 11, "name": name, "status": "active"}
            return None

        def get_tertiary_by_id(self, tertiary_id: int):
            if tertiary_id == 111:
                return {"id": 111, "secondary_department_id": 11, "name": "软件工程教研室", "status": "active"}
            return None

    service = DepartmentService(repository=FakeRepository())

    duplicate_tertiary = service.create_tertiary(secondary_department_id=11, name="软件工程教研室")

    assert duplicate_tertiary["success"] is False
    assert duplicate_tertiary["code"] == "TERTIARY_DEPARTMENT_NAME_EXISTS"


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


def test_department_service_lists_all_members_for_secondary_department():
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

        def list_personnel_by_secondary_department(self, *, secondary_id: int):
            assert secondary_id == 11
            return [
                {
                    "id": 101,
                    "employee_no": "P001",
                    "full_name": "张三",
                    "status": "active",
                },
                {
                    "id": 102,
                    "employee_no": "P002",
                    "full_name": "李四",
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
    assert result["data"]["members"][0]["employee_no"] == "P001"
    assert result["data"]["members"][0]["full_name"] == "张三"
    assert result["data"]["users"][0]["employee_no"] == "P001"
    assert result["data"]["users"][1]["status"] == "disabled"


def test_department_service_lists_primary_direct_members():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def list_direct_personnel_by_primary_department(self, *, primary_id: int):
            assert primary_id == 1
            return [
                {"id": 7, "employee_no": "P001", "full_name": "张三", "status": "active"},
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_primary_direct_users(primary_id=1)

    assert result["success"] is True
    assert result["data"]["primary_department_id"] == 1
    assert result["data"]["primary_department_name"] == "计算机学院"
    assert result["data"]["user_count"] == 1
    assert result["data"]["member_count"] == 1
    assert result["data"]["members"][0]["employee_no"] == "P001"
    assert result["data"]["users"][0]["full_name"] == "张三"


def test_department_service_lists_secondary_direct_members():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            if secondary_id == 11:
                return {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "active"}
            return None

        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "计算机学院", "status": "active"}
            return None

        def list_direct_personnel_by_secondary_department(self, *, secondary_id: int):
            assert secondary_id == 11
            return [
                {"id": 8, "employee_no": "P002", "full_name": "李四", "status": "active"},
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_direct_users(secondary_id=11)

    assert result["success"] is True
    assert result["data"]["secondary_department_id"] == 11
    assert result["data"]["primary_department_name"] == "计算机学院"
    assert result["data"]["secondary_department_name"] == "软件工程系"
    assert result["data"]["members"][0]["full_name"] == "李四"
    assert result["data"]["users"][0]["employee_no"] == "P002"


def test_department_service_lists_all_members_for_tertiary_department():
    class FakeRepository:
        def get_tertiary_by_id(self, tertiary_id: int):
            if tertiary_id == 111:
                return {
                    "id": 111,
                    "secondary_department_id": 11,
                    "name": "软件工程教研室",
                    "status": "active",
                }
            return None

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

        def list_personnel_by_tertiary_department(self, *, tertiary_id: int):
            assert tertiary_id == 111
            return [
                {
                    "id": 101,
                    "employee_no": "P001",
                    "full_name": "张三",
                    "status": "active",
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_tertiary_users(tertiary_id=111)

    assert result["success"] is True
    assert result["data"]["tertiary_department_id"] == 111
    assert result["data"]["secondary_department_name"] == "软件工程系"
    assert result["data"]["tertiary_department_name"] == "软件工程教研室"
    assert result["data"]["members"][0]["employee_no"] == "P001"
    assert result["data"]["users"][0]["full_name"] == "张三"


def test_department_service_lists_legacy_members_for_secondary_department():
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

        def list_direct_personnel_by_secondary_department(self, *, secondary_id: int):
            assert secondary_id == 11
            return [
                {
                    "id": 102,
                    "employee_no": "P102",
                    "full_name": "历史成员",
                    "status": "active",
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_legacy_users(secondary_id=11)

    assert result["success"] is True
    assert result["data"]["secondary_department_id"] == 11
    assert result["data"]["user_count"] == 1
    assert result["data"]["members"][0]["employee_no"] == "P102"
    assert result["data"]["users"][0]["full_name"] == "历史成员"


def test_department_service_lists_secondary_members_without_account_user_type():
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

        def list_personnel_by_secondary_department(self, *, secondary_id: int):
            return [
                {
                    "id": 201,
                    "employee_no": "P201",
                    "full_name": "王五",
                    "status": "active",
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_users(secondary_id=11)

    assert result["success"] is True
    assert result["data"]["users"][0]["employee_no"] == "P201"
    assert result["data"]["members"][0]["full_name"] == "王五"
    assert "user_type" not in result["data"]["users"][0]


def test_department_service_secondary_member_count_matches_personnel_rows():
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

        def list_personnel_by_secondary_department(self, *, secondary_id: int):
            return [
                {
                    "id": 202,
                    "employee_no": "P202",
                    "full_name": "赵六",
                    "status": "active",
                }
            ]

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_users(secondary_id=11)

    assert result["success"] is True
    assert result["data"]["user_count"] == 1
    assert result["data"]["member_count"] == 1
    assert result["data"]["members"][0]["employee_no"] == "P202"


def test_department_service_returns_not_found_for_missing_secondary_department():
    class FakeRepository:
        def get_secondary_by_id(self, secondary_id: int):
            return None

    service = DepartmentService(repository=FakeRepository())

    result = service.list_secondary_users(secondary_id=999)

    assert result["success"] is False
    assert result["code"] == "SECONDARY_DEPARTMENT_NOT_FOUND"


def test_department_service_returns_not_found_for_missing_tertiary_department():
    class FakeRepository:
        def get_tertiary_by_id(self, tertiary_id: int):
            return None

    service = DepartmentService(repository=FakeRepository())

    result = service.list_tertiary_users(tertiary_id=999)

    assert result["success"] is False
    assert result["code"] == "TERTIARY_DEPARTMENT_NOT_FOUND"


def test_department_service_force_delete_requires_admin_password_before_cleanup():
    class FakeRepository:
        def __init__(self):
            self.force_deleted = []

        def get_primary_by_id(self, primary_id: int):
            return {"id": primary_id, "name": "研发中心", "status": "active"}

        def force_delete_department_subtree(self, *, level: str, department_id: int):
            self.force_deleted.append((level, department_id))
            return {}

    repo = FakeRepository()
    service = DepartmentService(repository=repo)
    service.verify_admin_password = lambda *, actor_user_id, admin_password: False

    result = service.force_delete_department(
        level="primary",
        department_id=1,
        actor_user_id=1,
        admin_password="wrong-password",
    )

    assert result["success"] is False
    assert result["code"] == "ADMIN_PASSWORD_INVALID"
    assert repo.force_deleted == []


def test_department_service_force_delete_primary_clears_references_and_deletes_subtree():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            return {"id": primary_id, "name": "研发中心", "status": "active"}

        def force_delete_department_subtree(self, *, level: str, department_id: int):
            assert level == "primary"
            assert department_id == 1
            return {
                "deleted_primary": 1,
                "deleted_secondary": 2,
                "deleted_tertiary": 3,
                "cleared_personnel": 4,
                "cleared_users": 5,
            }

    service = DepartmentService(repository=FakeRepository())
    service.verify_admin_password = lambda *, actor_user_id, admin_password: True

    result = service.force_delete_department(
        level="primary",
        department_id=1,
        actor_user_id=1,
        admin_password="admin-password",
    )

    assert result["success"] is True
    assert result["data"]["summary"] == {
        "deleted_primary": 1,
        "deleted_secondary": 2,
        "deleted_tertiary": 3,
        "cleared_personnel": 4,
        "cleared_users": 5,
    }
    assert "已清空 4 个人员" in result["message"]


def test_department_service_batch_force_delete_dedupes_parent_child_items():
    class FakeRepository:
        def get_primary_by_id(self, primary_id: int):
            if primary_id == 1:
                return {"id": 1, "name": "研发中心", "status": "active"}
            return None

        def get_secondary_by_id(self, secondary_id: int):
            if secondary_id == 11:
                return {"id": 11, "primary_department_id": 1, "name": "电芯部", "status": "active"}
            return None

        def get_tertiary_by_id(self, tertiary_id: int):
            if tertiary_id == 111:
                return {"id": 111, "secondary_department_id": 11, "name": "材料组", "status": "active"}
            return None

        def force_delete_departments(self, *, items: list[dict[str, int | str]]):
            assert items == [{"level": "primary", "id": 1}]
            return {
                "deleted_primary": 1,
                "deleted_secondary": 1,
                "deleted_tertiary": 1,
                "cleared_personnel": 2,
                "cleared_users": 3,
                "details": [{"level": "primary", "id": 1, "status": "success", "message": "删除成功"}],
            }

    service = DepartmentService(repository=FakeRepository())
    service.verify_admin_password = lambda *, actor_user_id, admin_password: True

    result = service.batch_force_delete_departments(
        items=[
            {"level": "primary", "id": 1},
            {"level": "secondary", "id": 11},
            {"level": "tertiary", "id": 111},
            {"level": "primary", "id": 1},
        ],
        actor_user_id=1,
        admin_password="admin-password",
    )

    assert result["success"] is True
    assert result["data"]["summary"]["deleted_primary"] == 1
    assert result["data"]["summary"]["cleared_users"] == 3
