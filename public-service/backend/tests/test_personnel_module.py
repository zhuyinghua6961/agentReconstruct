from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.main import app
from app.modules.auth.repository import AuthRepository


def load_migration_sql(filename: str) -> str:
    repo_root = Path(__file__).resolve().parents[3]
    migration_path = repo_root / "highThinkingQA" / "server" / "database" / "migrations" / filename
    return migration_path.read_text(encoding="utf-8")


def find_module_spec(name: str):
    try:
        return importlib.util.find_spec(name)
    except ModuleNotFoundError:
        return None


def _decode(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _department_payload(
    *,
    primary_department_id: int = 1,
    secondary_department_id: int = 11,
    tertiary_department_id: int = 111,
) -> dict[str, object]:
    return {
        "primary_department_id": primary_department_id,
        "primary_department_name": "计算机学院",
        "secondary_department_id": secondary_department_id,
        "secondary_department_name": "软件工程系",
        "tertiary_department_id": tertiary_department_id,
        "tertiary_department_name": "智能软件实验室",
        "department_display": "计算机学院 / 软件工程系 / 智能软件实验室",
        "department_completion_level": "complete",
        "require_department_setup": False,
    }


class _FakeThreeLevelDepartments:
    def validate_department_selection(self, **kwargs):
        assert kwargs == {
            "primary_department_id": 1,
            "secondary_department_id": 11,
            "tertiary_department_id": 111,
            "require_active": True,
            "allow_empty": False,
            "allow_legacy_two_level": True,
        }
        return {"success": True, "data": _department_payload()}

    def describe_user_department(self, **kwargs):
        return _department_payload(
            primary_department_id=int(kwargs["primary_department_id"]),
            secondary_department_id=int(kwargs["secondary_department_id"]),
            tertiary_department_id=int(kwargs["tertiary_department_id"]),
        )

    def resolve_by_names(self, **kwargs):
        assert kwargs == {
            "primary_name": "计算机学院",
            "secondary_name": "软件工程系",
            "tertiary_name": "智能软件实验室",
            "active_only": True,
            "allow_legacy_two_level": True,
        }
        return {"success": True, "data": _department_payload()}


def test_personnel_binding_migration_adds_table_and_user_column():
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "highThinkingQA"
        / "server"
        / "database"
        / "migrations"
        / "20260420_01_personnel_binding.sql"
    )

    assert migration_path.exists()
    ddl = load_migration_sql("20260420_01_personnel_binding.sql")
    assert "CREATE TABLE IF NOT EXISTS personnel_records" in ddl
    assert "employee_no" in ddl
    assert "verification_code_hash" in ddl
    assert "personnel_id" in ddl
    assert "fk_users_personnel" in ddl
    assert "FOREIGN KEY" in ddl


def test_personnel_binding_migration_handles_missing_legacy_anchor_column():
    ddl = load_migration_sql("20260420_01_personnel_binding.sql")

    assert "ELSE NULL" in ddl
    assert "@users_personnel_after_column IS NOT NULL" in ddl
    assert 'ALTER TABLE users ADD COLUMN personnel_id BIGINT NULL"' in ddl


def test_personnel_department_migration_adds_three_department_columns_and_fks():
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "highThinkingQA"
        / "server"
        / "database"
        / "migrations"
        / "20260420_02_personnel_department_source.sql"
    )

    assert migration_path.exists()
    ddl = load_migration_sql("20260420_02_personnel_department_source.sql")
    assert "primary_department_id" in ddl
    assert "secondary_department_id" in ddl
    assert "tertiary_department_id" in ddl
    assert "fk_personnel_primary_department" in ddl
    assert "fk_personnel_secondary_department" in ddl
    assert "fk_personnel_tertiary_department" in ddl


def test_settings_expose_personnel_department_strict_source_flag(monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("PERSONNEL_DEPARTMENT_STRICT_SOURCE_ENABLED", "1")
    config_module.get_settings.cache_clear()
    try:
        settings = config_module.get_settings()
        assert settings.personnel_department_strict_source_enabled is True
    finally:
        config_module.get_settings.cache_clear()


def test_auth_repository_select_user_fields_include_personnel_id_when_present():
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
        "personnel_id",
        "created_at",
        "updated_at",
    }

    fields = repo._select_user_fields(include_password=True)

    assert "personnel_id" in fields


def test_auth_repository_list_users_includes_personnel_id_when_present():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
        "id",
        "username",
        "role",
        "status",
        "user_type",
        "personnel_id",
        "created_at",
        "updated_at",
    }

    def fake_execute(query: str, params: tuple[object, ...] = ()):
        assert "personnel_id" in query
        return []

    repo._execute_query = fake_execute

    assert repo.list_users(offset=0, limit=10) == []


def test_auth_repository_can_sync_all_bound_user_departments_for_personnel():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
        "id",
        "personnel_id",
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
    }

    def fake_update(query: str, params: tuple[object, ...] = ()) -> int:
        normalized = " ".join(query.split())
        assert "UPDATE users" in normalized
        assert "SET primary_department_id = %s" in normalized
        assert "secondary_department_id = %s" in normalized
        assert "tertiary_department_id = %s" in normalized
        assert "WHERE personnel_id = %s" in normalized
        assert params == (1, 11, 111, 9)
        return 3

    repo._execute_update = fake_update

    assert repo.sync_departments_for_personnel(
        personnel_id=9,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    ) == 3


def test_auth_repository_can_clear_single_user_department_cache():
    repo = AuthRepository(database=object())
    repo._load_columns = lambda: {
        "id",
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
    }

    def fake_update(query: str, params: tuple[object, ...] = ()) -> int:
        normalized = " ".join(query.split())
        assert "UPDATE users" in normalized
        assert "primary_department_id = %s" in normalized
        assert "secondary_department_id = %s" in normalized
        assert "tertiary_department_id = %s" in normalized
        assert "WHERE id = %s" in normalized
        assert params == (None, None, None, 12)
        return 1

    repo._execute_update = fake_update

    assert repo.clear_user_department_cache(user_id=12) == 1


def test_personnel_repository_maps_personnel_rows_and_binding_count():
    module_spec = find_module_spec("app.modules.personnel.repository")
    assert module_spec is not None

    from app.modules.personnel.repository import PersonnelRepository

    repo = PersonnelRepository(database=object())
    repo._execute_query = lambda query, params=(): [
        {
            "id": 9,
            "employee_no": "T2024001",
            "full_name": "张三",
            "status": "active",
            "remarks": "化学学院",
            "binding_count": 2,
            "created_at": None,
            "updated_at": None,
        }
    ]

    rows = repo.list_personnel(
        employee_no="",
        full_name="",
        status="",
        keyword="",
        offset=0,
        limit=10,
    )

    assert rows == [
        {
            "id": 9,
            "employee_no": "T2024001",
            "full_name": "张三",
            "status": "active",
            "remarks": "化学学院",
            "binding_count": 2,
            "created_at": None,
            "updated_at": None,
        }
    ]


def test_personnel_repository_maps_binding_user_rows():
    module_spec = find_module_spec("app.modules.personnel.repository")
    assert module_spec is not None

    from app.modules.personnel.repository import PersonnelRepository

    repo = PersonnelRepository(database=object())
    repo._execute_query = lambda query, params=(): [
        {
            "id": 12,
            "username": "alice",
            "role": "user",
            "user_type": 3,
            "status": "active",
            "personnel_id": 9,
        },
        {
            "id": 37,
            "username": "alice_lab",
            "role": "user",
            "user_type": 2,
            "status": "active",
            "personnel_id": 9,
        },
    ]


def test_personnel_repository_deletes_personnel_record():
    module_spec = find_module_spec("app.modules.personnel.repository")
    assert module_spec is not None

    from app.modules.personnel.repository import PersonnelRepository

    captured: dict[str, object] = {}
    repo = PersonnelRepository(database=object())

    def fake_update(query: str, params: tuple[object, ...] = ()):
        captured["query"] = " ".join(query.split())
        captured["params"] = params
        return 1

    repo._execute_update = fake_update

    assert repo.delete_personnel(personnel_id=9) == 1
    assert "DELETE FROM personnel_records" in captured["query"]
    assert "WHERE id = %s" in captured["query"]
    assert "NOT EXISTS" in captured["query"]
    assert "FROM users" in captured["query"]
    assert "u.personnel_id = personnel_records.id" in captured["query"]
    assert captured["params"] == (9,)


def test_personnel_repository_lists_bound_department_triplets_for_backfill():
    module_spec = find_module_spec("app.modules.personnel.repository")
    assert module_spec is not None

    from app.modules.personnel.repository import PersonnelRepository

    repo = PersonnelRepository(database=object())

    def fake_execute(query: str, params: tuple[object, ...] = ()):
        normalized = " ".join(query.split())
        assert "FROM users" in normalized
        assert "WHERE personnel_id = %s" in normalized
        assert "GROUP BY primary_department_id, secondary_department_id, tertiary_department_id" in normalized
        assert params == (9,)
        return [
            {
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "binding_count": 2,
            }
        ]

    repo._execute_query = fake_execute

    assert repo.list_bound_department_candidates(personnel_id=9) == [
        {
            "primary_department_id": 1,
            "secondary_department_id": 11,
            "tertiary_department_id": 111,
            "binding_count": 2,
        }
    ]


def test_personnel_repository_import_rows_records_write_error_and_continues():
    module_spec = find_module_spec("app.modules.personnel.repository")
    assert module_spec is not None

    from app.modules.personnel.repository import PersonnelRepository

    class FakeCursor:
        def __init__(self) -> None:
            self.inserted_rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            query_text = " ".join(str(query).split())
            if query_text.startswith("SAVEPOINT") or query_text.startswith("ROLLBACK TO SAVEPOINT") or query_text.startswith("RELEASE SAVEPOINT"):
                return
            if "SELECT" in query_text and "FROM personnel_records" in query_text:
                return
            if "UPDATE personnel_records" in query_text:
                return
            if "INSERT INTO personnel_records" in query_text:
                self.inserted_rows.append(str(params[0]))
                if str(params[0]) == "T2024002":
                    raise RuntimeError("boom")
                return
            raise AssertionError(f"unexpected query: {query_text}")

        def fetchone(self):
            return None

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_instance = FakeCursor()
            self.begin_called = 0
            self.commit_called = 0
            self.rollback_called = 0

        def begin(self):
            self.begin_called += 1

        def commit(self):
            self.commit_called += 1

        def rollback(self):
            self.rollback_called += 1

        def cursor(self):
            return self.cursor_instance

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
    repo = PersonnelRepository(database=FakeDatabase(connection))

    result = repo.import_personnel_rows(
        rows=[
            {
                "line_no": 2,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code_hash": "hash-1",
                "status": "active",
                "remarks": "化学学院",
            },
            {
                "line_no": 3,
                "employee_no": "T2024002",
                "full_name": "李四",
                "verification_code_hash": "hash-2",
                "status": "active",
                "remarks": "材料系",
            },
        ]
    )

    assert connection.begin_called == 1
    assert connection.commit_called == 1
    assert connection.rollback_called == 0
    assert connection.cursor_instance.inserted_rows == ["T2024001", "T2024002"]
    assert result["created"] == 1
    assert result["details"][1]["status"] == "failed"
    assert result["details"][1]["reason"] == "boom"


def test_personnel_repository_import_rows_skips_existing_unchanged_record():
    module_spec = find_module_spec("app.modules.personnel.repository")
    assert module_spec is not None

    from app.modules.personnel.repository import PersonnelRepository
    from app.modules.personnel.service import PersonnelService

    verification_code_hash = PersonnelService.hash_verification_code("ABC123")

    class FakeCursor:
        def __init__(self) -> None:
            self.update_calls = 0
            self.insert_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            query_text = " ".join(str(query).split())
            if query_text.startswith("SAVEPOINT") or query_text.startswith("ROLLBACK TO SAVEPOINT") or query_text.startswith("RELEASE SAVEPOINT"):
                return
            if "SELECT id" in query_text and "FROM personnel_records" in query_text:
                return
            if "UPDATE personnel_records" in query_text:
                self.update_calls += 1
                return
            if "INSERT INTO personnel_records" in query_text:
                self.insert_calls += 1
                return
            raise AssertionError(f"unexpected query: {query_text}")

        def fetchone(self):
            return {
                "id": 9,
                "full_name": "张三",
                "verification_code_hash": verification_code_hash,
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "status": "active",
                "remarks": "示例备注",
            }

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_instance = FakeCursor()
            self.begin_called = 0
            self.commit_called = 0
            self.rollback_called = 0

        def begin(self):
            self.begin_called += 1

        def commit(self):
            self.commit_called += 1

        def rollback(self):
            self.rollback_called += 1

        def cursor(self):
            return self.cursor_instance

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
    repo = PersonnelRepository(database=FakeDatabase(connection))

    result = repo.import_personnel_rows(
        rows=[
            {
                "line_no": 2,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code": "ABC123",
                "verification_code_hash": PersonnelService.hash_verification_code("ABC123"),
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "status": "active",
                "remarks": "示例备注",
            },
        ]
    )

    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["skipped"] == 1
    assert result["details"][0]["status"] == "skipped"
    assert "未变化" in result["details"][0]["message"]
    assert connection.cursor_instance.update_calls == 0
    assert connection.cursor_instance.insert_calls == 0
    assert connection.commit_called == 1


def test_personnel_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/api/admin/personnel" in paths
    assert "/api/admin/personnel/{personnel_id}" in paths
    assert "/api/admin/personnel/{personnel_id}/status" in paths
    assert "/api/admin/personnel/{personnel_id}/bindings" in paths
    assert "/api/admin/personnel/batch-delete" in paths
    assert "/api/admin/personnel/batch-import" in paths
    assert "/api/admin/personnel/import-template" in paths


def test_personnel_service_list_supports_employee_name_and_status_filters():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def count_personnel(self, **kwargs):
            assert kwargs == {
                "employee_no": "T2024",
                "full_name": "张",
                "status": "active",
                "keyword": "",
            }
            return 3

        def list_personnel(self, **kwargs):
            assert kwargs == {
                "employee_no": "T2024",
                "full_name": "张",
                "status": "active",
                "keyword": "",
                "offset": 5,
                "limit": 5,
            }
            return [
                {
                    "id": 9,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "status": "active",
                    "remarks": "",
                    "binding_count": 2,
                    "created_at": None,
                    "updated_at": None,
                }
            ]

    service = PersonnelService(repository=FakeRepository())
    result = service.list_personnel(page=2, page_size=5, employee_no="T2024", full_name="张", status="active", keyword="")

    assert result["success"] is True
    assert result["pagination"] == {"page": 2, "page_size": 5, "total": 3}
    assert result["data"]["items"][0]["binding_count"] == 2


def test_personnel_service_create_hashes_verification_code():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.created_payload = None

        def get_by_employee_no(self, employee_no: str):
            assert employee_no == "T2024001"
            return None

        def create_personnel(self, **kwargs):
            self.created_payload = kwargs
            return 9

        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code_hash": self.created_payload["verification_code_hash"],
                "status": "active",
                "remarks": "化学学院",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "binding_count": 0,
                "created_at": None,
                "updated_at": None,
            }

    repo = FakeRepository()
    service = PersonnelService(repository=repo, department_service=_FakeThreeLevelDepartments(), users_repo=object())
    result = service.create_personnel(
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
        status="active",
        remarks="化学学院",
    )

    assert result["success"] is True
    assert repo.created_payload["verification_code_hash"].startswith("pbkdf2_sha256$")
    assert repo.created_payload["verification_code_hash"] != "ABC123"
    assert result["data"]["employee_no"] == "T2024001"


def test_personnel_service_update_can_reset_verification_code():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.state = {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code_hash": "pbkdf2_sha256$1$old$hash",
                "status": "active",
                "remarks": "原备注",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "binding_count": 1,
                "created_at": None,
                "updated_at": None,
            }
            self.updated_payload = None

        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return dict(self.state)

        def update_personnel(self, **kwargs):
            self.updated_payload = kwargs
            self.state["full_name"] = kwargs["full_name"]
            self.state["remarks"] = kwargs["remarks"]
            self.state["verification_code_hash"] = kwargs["verification_code_hash"]
            return 1

    repo = FakeRepository()
    service = PersonnelService(repository=repo, department_service=_FakeThreeLevelDepartments(), users_repo=object())
    result = service.update_personnel(
        personnel_id=9,
        full_name="李四",
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
        remarks="新备注",
        verification_code="NEW456",
    )

    assert result["success"] is True
    assert repo.updated_payload["verification_code_hash"].startswith("pbkdf2_sha256$")
    assert repo.updated_payload["verification_code_hash"] != "NEW456"
    assert result["data"]["full_name"] == "李四"


def test_personnel_service_update_can_clear_remarks():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.state = {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code_hash": "pbkdf2_sha256$1$old$hash",
                "status": "active",
                "remarks": "原备注",
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "binding_count": 1,
                "created_at": None,
                "updated_at": None,
            }
            self.updated_payload = None

        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return dict(self.state)

        def update_personnel(self, **kwargs):
            self.updated_payload = kwargs
            self.state["remarks"] = None
            return 1

    repo = FakeRepository()
    service = PersonnelService(repository=repo, department_service=_FakeThreeLevelDepartments(), users_repo=object())
    result = service.update_personnel(
        personnel_id=9,
        full_name="张三",
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
        remarks=None,
    )

    assert result["success"] is True
    assert "remarks" in repo.updated_payload
    assert repo.updated_payload["remarks"] is None


def test_personnel_service_disable_updates_record_status():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.state = {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code_hash": "pbkdf2_sha256$1$old$hash",
                "status": "active",
                "remarks": "",
                "binding_count": 0,
                "created_at": None,
                "updated_at": None,
            }

        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return dict(self.state)

        def update_personnel_status(self, *, personnel_id: int, status: str):
            assert personnel_id == 9
            assert status == "disabled"
            self.state["status"] = "disabled"
            return 1

    service = PersonnelService(repository=FakeRepository())
    result = service.update_personnel_status(personnel_id=9, status="disabled")

    assert result["success"] is True
    assert result["data"]["personnel_record_status"] == "disabled"


def test_personnel_service_list_bindings_returns_all_bound_accounts():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code_hash": "pbkdf2_sha256$1$old$hash",
                "status": "active",
                "remarks": "",
                "binding_count": 2,
                "created_at": None,
                "updated_at": None,
            }

        def list_bindings(self, *, personnel_id: int):
            assert personnel_id == 9
            return [
                {"id": 12, "username": "alice", "role": "user", "user_type": 3, "status": "active", "personnel_id": 9},
                {"id": 37, "username": "alice_lab", "role": "user", "user_type": 2, "status": "active", "personnel_id": 9},
            ]

    service = PersonnelService(repository=FakeRepository())
    result = service.list_bindings(personnel_id=9)

    assert result["success"] is True
    assert len(result["data"]["items"]) == 2
    assert result["data"]["items"][1]["username"] == "alice_lab"


def test_personnel_service_delete_rejects_bound_personnel():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.delete_called = False

        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "binding_count": 2,
            }

        def delete_personnel(self, *, personnel_id: int):
            self.delete_called = True
            return 1

    repository = FakeRepository()
    service = PersonnelService(repository=repository)
    result = service.delete_personnel(personnel_id=9)

    assert result["success"] is False
    assert result["code"] == "PERSONNEL_HAS_BINDINGS"
    assert service.status_code_for(result, ok_status=200) == 409
    assert repository.delete_called is False


def test_personnel_service_delete_removes_unbound_personnel():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.deleted_id = None

        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "disabled",
                "binding_count": 0,
            }

        def delete_personnel(self, *, personnel_id: int):
            self.deleted_id = personnel_id
            return 1

    repository = FakeRepository()
    service = PersonnelService(repository=repository)
    result = service.delete_personnel(personnel_id=9)

    assert result["success"] is True
    assert result["data"]["id"] == 9
    assert repository.deleted_id == 9


def test_personnel_service_batch_delete_requires_selection():
    from app.modules.personnel.service import PersonnelService

    service = PersonnelService(repository=object())

    result = service.batch_delete_personnel(personnel_ids=[])

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"


def test_personnel_service_batch_delete_partially_succeeds_and_deduplicates():
    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.deleted_ids = []

        def get_by_id(self, personnel_id: int):
            records = {
                9: {
                    "id": 9,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "status": "active",
                    "binding_count": 0,
                    "created_at": None,
                    "updated_at": None,
                },
                10: {
                    "id": 10,
                    "employee_no": "T2024002",
                    "full_name": "李四",
                    "status": "active",
                    "binding_count": 2,
                    "created_at": None,
                    "updated_at": None,
                },
            }
            return records.get(int(personnel_id))

        def delete_personnel(self, *, personnel_id: int):
            self.deleted_ids.append(personnel_id)
            return 1

    repository = FakeRepository()
    service = PersonnelService(repository=repository)

    result = service.batch_delete_personnel(personnel_ids=[9, 10, 404, 9])

    assert result["success"] is True
    assert result["data"]["summary"] == {"total": 3, "success": 1, "failed": 2, "skipped": 0}
    assert repository.deleted_ids == [9]
    assert [detail["status"] for detail in result["data"]["details"]] == ["success", "failed", "failed"]
    assert result["data"]["details"][1]["code"] == "PERSONNEL_HAS_BINDINGS"
    assert result["data"]["details"][2]["code"] == "PERSONNEL_NOT_FOUND"


def test_personnel_import_marks_duplicate_employee_no_rows_failed_and_continues():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows, sync_bound_users=True):
            self.rows = rows
            return {
                "created": 1,
                "updated": 0,
                "skipped": 0,
                "details": [
                    {
                        "row": rows[0]["line_no"],
                        "employee_no": rows[0]["employee_no"],
                        "full_name": rows[0]["full_name"],
                        "personnel_record_status": rows[0]["status"],
                        "status": "created",
                    }
                ],
            }

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks\n"
        "T2024001,张三,AAA111,active,计算机学院,软件工程系,智能软件实验室,化学学院\n"
        "T2024001,李四,BBB222,disabled,计算机学院,软件工程系,智能软件实验室,材料系\n"
        "T2024002,王五,CCC333,active,计算机学院,软件工程系,智能软件实验室,材料系\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is True
    assert result["data"]["summary"]["created"] == 1
    assert result["data"]["summary"]["failed"] == 2
    assert [row["employee_no"] for row in repo.rows] == ["T2024002"]
    failed_details = [detail for detail in result["data"]["details"] if detail["status"] == "failed"]
    assert [detail["row"] for detail in failed_details] == [2, 3]
    assert all("重复工号" in detail["reason"] for detail in failed_details)


def test_personnel_import_updates_existing_employee_no():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.updated_payload = None
            self.created_payload = None

        def import_personnel_rows(self, *, rows):
            row = rows[0]
            self.updated_payload = {
                "personnel_id": 9,
                "full_name": row["full_name"],
                "verification_code_hash": row["verification_code_hash"],
                "remarks": row["remarks"],
                "status": row["status"],
            }
            return {
                "created": 0,
                "updated": 1,
                "details": [
                    {
                        "row": row["line_no"],
                        "employee_no": row["employee_no"],
                        "full_name": row["full_name"],
                        "personnel_record_status": row["status"],
                        "status": "updated",
                    }
                ],
            }

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks\n"
        "T2024001,张三,AAA111,disabled,计算机学院,软件工程系,智能软件实验室,化学学院\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is True
    assert result["data"]["summary"]["updated"] == 1
    assert repo.updated_payload["personnel_id"] == 9
    assert repo.updated_payload["status"] == "disabled"
    assert repo.updated_payload["verification_code_hash"].startswith("pbkdf2_sha256$")
    assert repo.created_payload is None


def test_personnel_import_updates_existing_employee_no_can_clear_remarks():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.updated_payload = None

        def import_personnel_rows(self, *, rows):
            row = rows[0]
            self.updated_payload = {
                "personnel_id": 9,
                "full_name": row["full_name"],
                "verification_code_hash": row["verification_code_hash"],
                "remarks": row["remarks"],
                "status": row["status"],
            }
            return {
                "created": 0,
                "updated": 1,
                "details": [
                    {
                        "row": row["line_no"],
                        "employee_no": row["employee_no"],
                        "full_name": row["full_name"],
                        "personnel_record_status": row["status"],
                        "status": "updated",
                    }
                ],
            }

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks\n"
        "T2024001,张三,AAA111,disabled,计算机学院,软件工程系,智能软件实验室,\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is True
    assert repo.updated_payload["remarks"] == ""


def test_personnel_import_without_remarks_column_keeps_existing_remarks():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService
    from app.modules.personnel.repository import REMARKS_UNSET

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows):
            self.rows = rows
            return {
                "created": 0,
                "updated": 1,
                "details": [
                    {
                        "row": rows[0]["line_no"],
                        "employee_no": rows[0]["employee_no"],
                        "full_name": rows[0]["full_name"],
                        "personnel_record_status": rows[0]["status"],
                        "status": "updated",
                    }
                ],
            }

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name\n"
        "T2024001,张三,AAA111,disabled,计算机学院,软件工程系,智能软件实验室\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is True
    assert repo.rows[0]["remarks"] is REMARKS_UNSET


def test_personnel_import_accepts_chinese_template_headers_and_defaults_status_active():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows, sync_bound_users=True):
            self.rows = rows
            return {
                "created": 1,
                "updated": 0,
                "details": [
                    {
                        "row": rows[0]["line_no"],
                        "employee_no": rows[0]["employee_no"],
                        "full_name": rows[0]["full_name"],
                        "personnel_record_status": rows[0]["status"],
                        "status": "created",
                    }
                ],
            }

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "工号,姓名,一级部门,二级部门,三级部门,校验码,备注\n"
        "T2024001,张三,计算机学院,软件工程系,智能软件实验室,AAA111,示例备注\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is True
    assert repo.rows[0]["status"] == "active"
    assert repo.rows[0]["remarks"] == "示例备注"


def test_personnel_import_keeps_valid_rows_when_late_row_validation_fails():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows, sync_bound_users=True):
            self.rows = rows
            return {
                "created": len(rows),
                "updated": 0,
                "skipped": 0,
                "details": [
                    {
                        "row": row["line_no"],
                        "employee_no": row["employee_no"],
                        "full_name": row["full_name"],
                        "personnel_record_status": row["status"],
                        "status": "created",
                    }
                    for row in rows
                ],
            }

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks\n"
        "T2024001,张三,AAA111,active,计算机学院,软件工程系,智能软件实验室,化学学院\n"
        "T2024002,李四,,active,计算机学院,软件工程系,智能软件实验室,材料系\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is True
    assert result["data"]["summary"] == {
        "total": 2,
        "created": 1,
        "updated": 0,
        "skipped": 0,
        "failed": 1,
        "created_departments_total": 0,
        "created_primary_departments": 0,
        "created_secondary_departments": 0,
        "created_tertiary_departments": 0,
    }
    assert [row["employee_no"] for row in repo.rows] == ["T2024001"]
    assert result["data"]["details"][1]["row"] == 3
    assert result["data"]["details"][1]["employee_no"] == "T2024002"
    assert result["data"]["details"][1]["full_name"] == "李四"
    assert result["data"]["details"][1]["status"] == "failed"
    assert result["data"]["details"][1]["reason"] == "校验码为空"


def test_personnel_repository_import_records_row_write_failure_and_continues():
    module_spec = find_module_spec("app.modules.personnel.repository")
    assert module_spec is not None

    from app.modules.personnel.repository import PersonnelRepository

    class FakeCursor:
        def __init__(self) -> None:
            self.lastrowid = 0
            self.current_employee_no = ""
            self.records: dict[str, dict] = {}
            self.fail_on_insert = {"T2024002"}
            self.next_id = 10

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            normalized = " ".join(str(query).split())
            if normalized.startswith("SAVEPOINT") or normalized.startswith("RELEASE SAVEPOINT") or normalized.startswith("ROLLBACK TO SAVEPOINT"):
                return
            if "FROM personnel_records" in normalized and "WHERE employee_no = %s" in normalized:
                self.current_employee_no = str(params[0])
                return
            if normalized.startswith("INSERT INTO personnel_records"):
                employee_no = str(params[0])
                if employee_no in self.fail_on_insert:
                    raise RuntimeError("duplicate key")
                self.next_id += 1
                self.lastrowid = self.next_id
                self.records[employee_no] = {"id": self.lastrowid}
                return
            raise AssertionError(f"unexpected query: {normalized}")

        def fetchone(self):
            return self.records.get(self.current_employee_no)

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_obj = FakeCursor()
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def begin(self):
            return None

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    class FakeDatabase:
        def __init__(self) -> None:
            self.connection_obj = FakeConnection()

        def connection(self):
            return self.connection_obj

    db = FakeDatabase()
    repo = PersonnelRepository(database=db)
    rows = [
        {
            "line_no": 2,
            "employee_no": "T2024001",
            "full_name": "张三",
            "verification_code": "AAA111",
            "verification_code_hash": "hashed-1",
            "status": "active",
            "primary_department_id": 1,
            "secondary_department_id": None,
            "tertiary_department_id": None,
        },
        {
            "line_no": 3,
            "employee_no": "T2024002",
            "full_name": "李四",
            "verification_code": "BBB222",
            "verification_code_hash": "hashed-2",
            "status": "active",
            "primary_department_id": 1,
            "secondary_department_id": None,
            "tertiary_department_id": None,
        },
        {
            "line_no": 4,
            "employee_no": "T2024003",
            "full_name": "王五",
            "verification_code": "CCC333",
            "verification_code_hash": "hashed-3",
            "status": "active",
            "primary_department_id": 1,
            "secondary_department_id": None,
            "tertiary_department_id": None,
        },
    ]

    result = repo.import_personnel_rows(rows=rows)

    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["skipped"] == 0
    assert [detail["status"] for detail in result["details"]] == ["created", "failed", "created"]
    assert result["details"][1]["row"] == 3
    assert "duplicate key" in result["details"][1]["reason"]
    assert db.connection_obj.commits == 1
    assert db.connection_obj.rollbacks == 0


def test_personnel_template_supports_csv_and_xlsx():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    service = PersonnelImportService(repository=object())
    csv_response = service.template_response(fmt="csv")
    xlsx_response = service.template_response(fmt="xlsx")

    assert csv_response.headers["Content-Disposition"].endswith('personnel_import_template.csv"')
    first_line = csv_response.body.decode("utf-8-sig").splitlines()[0]
    assert first_line == "工号,姓名,一级部门,二级部门,三级部门,校验码,备注"
    assert "status" not in first_line
    assert xlsx_response.headers["Content-Disposition"].endswith('personnel_import_template.xlsx"')
    assert xlsx_response.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_personnel_import_accepts_legacy_english_headers():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows, sync_bound_users=True):
            self.rows = rows
            return {
                "created": 1,
                "updated": 0,
                "details": [
                    {
                        "row": rows[0]["line_no"],
                        "employee_no": rows[0]["employee_no"],
                        "full_name": rows[0]["full_name"],
                        "personnel_record_status": rows[0]["status"],
                        "status": "created",
                    }
                ],
            }

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks\n"
        "T2024001,张三,AAA111,active,计算机学院,软件工程系,智能软件实验室,示例备注\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is True
    assert repo.rows[0]["status"] == "active"


def test_personnel_admin_routes_registered(monkeypatch):
    module_spec = find_module_spec("app.modules.personnel.api")
    assert module_spec is not None

    from app.core.deps import AuthContext
    from app.modules.personnel.api import PersonnelBatchDeleteRequest, PersonnelUpdateRequest
    from app.modules.personnel import api as personnel_api_module
    from app.modules.personnel.repository import REMARKS_UNSET
    from app.modules.personnel import service as personnel_service_module

    monkeypatch.setattr(
        personnel_service_module.personnel_service,
        "list_personnel",
        lambda **kwargs: {"success": True, "data": {"items": []}, "pagination": {"page": kwargs["page"], "page_size": kwargs["page_size"], "total": 0}},
    )

    response = personnel_api_module.list_personnel(
        page=1,
        page_size=20,
        employee_no="",
        full_name="",
        status="",
        keyword="",
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["items"] == []

    captured_calls = []

    def fake_update_personnel(**kwargs):
        captured_calls.append(kwargs)
        return {"success": True, "data": {"id": kwargs["personnel_id"]}}

    monkeypatch.setattr(personnel_service_module.personnel_service, "update_personnel", fake_update_personnel)

    response_without_remarks = personnel_api_module.update_personnel(
        personnel_id=9,
        payload=PersonnelUpdateRequest(full_name="张三"),
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )
    response_with_null_remarks = personnel_api_module.update_personnel(
        personnel_id=9,
        payload=PersonnelUpdateRequest(full_name="张三", remarks=None),
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )
    response_with_status = personnel_api_module.update_personnel(
        personnel_id=9,
        payload=PersonnelUpdateRequest(full_name="张三", remarks=None, status="disabled"),
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert response_without_remarks.status_code == 200
    assert response_with_null_remarks.status_code == 200
    assert response_with_status.status_code == 200
    assert captured_calls[0]["remarks"] is REMARKS_UNSET
    assert captured_calls[1]["remarks"] is None
    assert captured_calls[2]["status"] == "disabled"

    def fake_delete_personnel(**kwargs):
        return {"success": True, "data": {"id": kwargs["personnel_id"]}}

    monkeypatch.setattr(personnel_service_module.personnel_service, "delete_personnel", fake_delete_personnel)

    delete_response = personnel_api_module.delete_personnel(
        personnel_id=9,
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert delete_response.status_code == 200
    assert _decode(delete_response)["data"]["id"] == 9

    batch_calls = []

    def fake_batch_delete_personnel(**kwargs):
        batch_calls.append(kwargs)
        return {
            "success": True,
            "data": {
                "summary": {"total": 2, "success": 2, "failed": 0, "skipped": 0},
                "details": [],
            },
        }

    monkeypatch.setattr(personnel_service_module.personnel_service, "batch_delete_personnel", fake_batch_delete_personnel)

    batch_delete_response = personnel_api_module.batch_delete_personnel(
        payload=PersonnelBatchDeleteRequest(personnel_ids=[9, 10]),
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert batch_delete_response.status_code == 200
    assert batch_calls == [{"personnel_ids": [9, 10]}]

    force_calls = []

    def fake_force_delete_personnel(**kwargs):
        force_calls.append(kwargs)
        return {"success": True, "data": {"summary": {"deleted": 1, "unbound_users": 2}}}

    def fake_batch_force_delete_personnel(**kwargs):
        force_calls.append(kwargs)
        return {
            "success": True,
            "data": {
                "summary": {"total": 2, "success": 2, "failed": 0, "unbound_users": 3},
                "details": [],
            },
        }

    monkeypatch.setattr(personnel_service_module.personnel_service, "force_delete_personnel", fake_force_delete_personnel)
    monkeypatch.setattr(
        personnel_service_module.personnel_service,
        "batch_force_delete_personnel",
        fake_batch_force_delete_personnel,
    )

    force_response = personnel_api_module.force_delete_personnel(
        personnel_id=9,
        payload=personnel_api_module.PersonnelForceDeleteRequest(admin_password="secret"),
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )
    batch_force_response = personnel_api_module.batch_force_delete_personnel(
        payload=personnel_api_module.PersonnelBatchForceDeleteRequest(personnel_ids=[9, 10], admin_password="secret"),
        _context=AuthContext(user_id=1, role="admin", username="admin"),
    )

    assert force_response.status_code == 200
    assert batch_force_response.status_code == 200
    assert force_calls == [
        {"personnel_id": 9, "actor_user_id": 1, "admin_password": "secret"},
        {"personnel_ids": [9, 10], "actor_user_id": 1, "admin_password": "secret"},
    ]


def test_create_personnel_requires_complete_three_level_department():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def get_by_employee_no(self, employee_no: str):
            assert employee_no == "T2024001"
            return None

    class FakeDepartments:
        def validate_department_selection(self, **kwargs):
            assert kwargs == {
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
                "require_active": True,
                "allow_empty": False,
                "allow_legacy_two_level": True,
            }
            return {"success": False, "error": "请选择一级、二级和三级部门", "code": "DEPARTMENT_REQUIRED"}

    service = PersonnelService(repository=FakeRepository(), department_service=FakeDepartments(), users_repo=object())
    result = service.create_personnel(
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        primary_department_id=None,
        secondary_department_id=None,
        tertiary_department_id=None,
        status="active",
        remarks="化学学院",
    )

    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_REQUIRED"


def test_create_personnel_accepts_primary_direct_department():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.created = None

        def get_by_employee_no(self, employee_no: str):
            return None

        def create_personnel(self, **kwargs):
            self.created = kwargs
            return 9

        def get_by_id(self, personnel_id: int):
            return {
                "id": personnel_id,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "remarks": None,
                "primary_department_id": 1,
                "secondary_department_id": None,
                "tertiary_department_id": None,
                "binding_count": 0,
                "created_at": None,
                "updated_at": None,
            }

    class FakeDepartments:
        def validate_department_selection(self, **kwargs):
            assert kwargs == {
                "primary_department_id": 1,
                "secondary_department_id": None,
                "tertiary_department_id": None,
                "require_active": True,
                "allow_empty": False,
                "allow_legacy_two_level": True,
            }
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "primary_department_name": "计算机学院",
                    "secondary_department_id": None,
                    "secondary_department_name": None,
                    "tertiary_department_id": None,
                    "tertiary_department_name": None,
                    "department_display": "计算机学院",
                    "department_completion_level": "primary_complete",
                    "require_department_setup": False,
                },
            }

        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": kwargs["primary_department_id"],
                "primary_department_name": "计算机学院",
                "secondary_department_id": kwargs["secondary_department_id"],
                "secondary_department_name": None,
                "tertiary_department_id": kwargs["tertiary_department_id"],
                "tertiary_department_name": None,
                "department_display": "计算机学院",
                "department_completion_level": "primary_complete",
                "require_department_setup": False,
            }

    repo = FakeRepository()
    service = PersonnelService(repository=repo, department_service=FakeDepartments(), users_repo=object())
    result = service.create_personnel(
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
        primary_department_id=1,
        secondary_department_id=None,
        tertiary_department_id=None,
        status="active",
    )

    assert result["success"] is True
    assert repo.created["primary_department_id"] == 1
    assert repo.created["secondary_department_id"] is None
    assert repo.created["tertiary_department_id"] is None
    assert result["data"]["department_display"] == "计算机学院"


def test_update_personnel_syncs_all_bound_users_when_department_changes():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.repository import REMARKS_UNSET
    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self) -> None:
            self.state = {
                "id": 9,
                "employee_no": "T2024001",
                "full_name": "张三",
                "verification_code_hash": "pbkdf2_sha256$1$old$hash",
                "status": "active",
                "remarks": "原备注",
                "primary_department_id": 2,
                "secondary_department_id": 21,
                "tertiary_department_id": 211,
                "binding_count": 2,
                "created_at": None,
                "updated_at": None,
            }
            self.updated_payload = None

        def get_by_id(self, personnel_id: int):
            assert personnel_id == 9
            return dict(self.state)

        def update_personnel_and_sync_bound_users(self, **kwargs):
            self.updated_payload = kwargs
            self.state.update(
                {
                    "full_name": kwargs["full_name"],
                    "remarks": kwargs.get("remarks"),
                    "primary_department_id": kwargs["primary_department_id"],
                    "secondary_department_id": kwargs["secondary_department_id"],
                    "tertiary_department_id": kwargs["tertiary_department_id"],
                }
            )
            return 1

    class FakeDepartments:
        def validate_department_selection(self, **kwargs):
            assert kwargs == {
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
                "require_active": True,
                "allow_empty": False,
                "allow_legacy_two_level": True,
            }
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "primary_department_name": "计算机学院",
                    "secondary_department_id": 11,
                    "secondary_department_name": "软件工程系",
                    "tertiary_department_id": 111,
                    "tertiary_department_name": "智能软件实验室",
                    "department_display": "计算机学院 / 软件工程系 / 智能软件实验室",
                    "department_completion_level": "complete",
                    "require_department_setup": False,
                },
            }

        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": kwargs["primary_department_id"],
                "primary_department_name": "计算机学院",
                "secondary_department_id": kwargs["secondary_department_id"],
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": kwargs["tertiary_department_id"],
                "tertiary_department_name": "智能软件实验室",
                "department_display": "计算机学院 / 软件工程系 / 智能软件实验室",
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    class FakeUsersRepo:
        def __init__(self) -> None:
            self.synced = []

        def sync_departments_for_personnel(self, **kwargs):
            self.synced.append(kwargs)
            return 2

    repo = FakeRepository()
    users_repo = FakeUsersRepo()
    service = PersonnelService(repository=repo, department_service=FakeDepartments(), users_repo=users_repo)
    result = service.update_personnel(
        personnel_id=9,
        full_name="李四",
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
        status="disabled",
        remarks=REMARKS_UNSET,
    )

    assert result["success"] is True
    assert repo.updated_payload["primary_department_id"] == 1
    assert repo.updated_payload["secondary_department_id"] == 11
    assert repo.updated_payload["tertiary_department_id"] == 111
    assert repo.updated_payload["status"] == "disabled"
    assert repo.updated_payload["sync_bound_users"] is True
    assert users_repo.synced == []


def test_personnel_repository_update_and_sync_can_clear_lower_department_levels():
    from app.modules.personnel.repository import PersonnelRepository

    class FakeCursor:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple]] = []
            self.rowcount = 1

        def execute(self, query: str, params: tuple = ()):
            self.executed.append((query, params))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        def __init__(self, cursor: FakeCursor) -> None:
            self.cursor_obj = cursor

        def begin(self):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

        def cursor(self):
            return self.cursor_obj

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeDatabase:
        def __init__(self) -> None:
            self.cursor = FakeCursor()

        def connection(self):
            return FakeConnection(self.cursor)

    db = FakeDatabase()
    repo = PersonnelRepository(database=db)
    repo.has_table = lambda table_name: table_name == "users"
    repo.has_user_column = lambda column_name: column_name in {
        "personnel_id",
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
    }

    result = repo.update_personnel_and_sync_bound_users(
        personnel_id=9,
        full_name="张三",
        primary_department_id=1,
        secondary_department_id=None,
        tertiary_department_id=None,
        sync_bound_users=True,
    )

    assert result == 1
    update_sql, update_params = db.cursor.executed[0]
    sync_sql, sync_params = db.cursor.executed[1]
    assert "secondary_department_id = %s" in update_sql
    assert "tertiary_department_id = %s" in update_sql
    assert update_params[2] is None
    assert update_params[3] is None
    assert sync_params == (1, None, None, 9)


def test_import_personnel_requires_department_name_columns_and_syncs_existing_personnel():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows: list[dict], sync_bound_users: bool):
            self.rows = rows
            self.sync_bound_users = sync_bound_users
            return {
                "created": 0,
                "updated": 1,
                "details": [
                    {
                        "row": 2,
                        "employee_no": "T2024001",
                        "full_name": "张三",
                        "status": "updated",
                    }
                ],
            }

    class FakeService:
        def hash_verification_code(self, verification_code: str) -> str:
            assert verification_code == "ABC123"
            return "hashed-code"

    class FakeDepartments:
        def resolve_or_create_by_names(self, **kwargs):
            assert kwargs == {
                "primary_name": "计算机学院",
                "secondary_name": "软件工程系",
                "tertiary_name": "智能软件实验室",
                "active_only": True,
                "allow_legacy_two_level": True,
            }
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": 111,
                    "primary_department_name": "计算机学院",
                    "secondary_department_name": "软件工程系",
                    "tertiary_department_name": "智能软件实验室",
                    "department_display": "计算机学院 / 软件工程系 / 智能软件实验室",
                    "created_departments": {"primary": 0, "secondary": 0, "tertiary": 0, "total": 0},
                },
            }

    csv_content = "\n".join(
        [
            "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks",
            "T2024001,张三,ABC123,active,计算机学院,软件工程系,智能软件实验室,化学学院",
        ]
    ).encode("utf-8")

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, service=FakeService(), department_service=FakeDepartments())
    result = service.import_personnel(file_bytes=csv_content, filename="personnel.csv")

    assert result["success"] is True
    assert result["data"]["summary"]["updated"] == 1
    assert repo.sync_bound_users is True
    assert repo.rows == [
        {
            "line_no": 2,
            "employee_no": "T2024001",
            "full_name": "张三",
            "verification_code": "ABC123",
            "verification_code_hash": "hashed-code",
            "status": "active",
            "remarks": "化学学院",
            "primary_department_id": 1,
            "secondary_department_id": 11,
            "tertiary_department_id": 111,
        }
    ]


def test_import_personnel_allows_primary_only_department_names():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def import_personnel_rows(self, *, rows: list[dict], sync_bound_users: bool):
            self.rows = rows
            self.sync_bound_users = sync_bound_users
            return {
                "created": 1,
                "updated": 0,
                "skipped": 0,
                "details": [],
            }

    class FakeService:
        def hash_verification_code(self, verification_code: str) -> str:
            assert verification_code == "ABC123"
            return "hashed-code"

    class FakeDepartments:
        def resolve_or_create_by_names(self, **kwargs):
            assert kwargs == {
                "primary_name": "计算机学院",
                "secondary_name": "",
                "tertiary_name": "",
                "active_only": True,
                "allow_legacy_two_level": True,
            }
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "secondary_department_id": None,
                    "tertiary_department_id": None,
                    "department_display": "计算机学院",
                    "department_completion_level": "primary_complete",
                    "require_department_setup": False,
                    "created_departments": {"primary": 0, "secondary": 0, "tertiary": 0, "total": 0},
                },
            }

    csv_content = "\n".join(
        [
            "工号,姓名,校验码,状态,一级部门,二级部门,三级部门,备注",
            "T2024001,张三,ABC123,active,计算机学院,,,",
        ]
    ).encode("utf-8")

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, service=FakeService(), department_service=FakeDepartments())
    result = service.import_personnel(file_bytes=csv_content, filename="personnel.csv")

    assert result["success"] is True
    assert repo.sync_bound_users is True
    assert repo.rows[0]["primary_department_id"] == 1
    assert repo.rows[0]["secondary_department_id"] is None
    assert repo.rows[0]["tertiary_department_id"] is None
    assert result["data"]["summary"]["created_departments_total"] == 0


def test_import_personnel_auto_creates_missing_departments_and_reports_summary():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def import_personnel_rows(self, *, rows: list[dict], sync_bound_users: bool):
            self.rows = rows
            self.sync_bound_users = sync_bound_users
            return {"created": len(rows), "updated": 0, "skipped": 0, "details": []}

    class FakeService:
        def hash_verification_code(self, verification_code: str) -> str:
            return f"hashed-{verification_code}"

    class FakeDepartments:
        def __init__(self):
            self.calls: list[dict] = []
            self.created_paths: set[tuple[str, str, str]] = set()

        def resolve_or_create_by_names(self, **kwargs):
            self.calls.append(kwargs)
            path = (kwargs["primary_name"], kwargs["secondary_name"], kwargs["tertiary_name"])
            created = {"primary": 0, "secondary": 0, "tertiary": 0, "total": 0}
            if path not in self.created_paths:
                self.created_paths.add(path)
                created = {"primary": 1, "secondary": 1, "tertiary": 1, "total": 3}
            return {
                "success": True,
                "data": {
                    "primary_department_id": 101,
                    "secondary_department_id": 102,
                    "tertiary_department_id": 103,
                    "department_display": "新能源事业部 / 电芯研发部 / 材料实验室",
                    "created_departments": created,
                },
            }

    csv_content = "\n".join(
        [
            "工号,姓名,校验码,状态,一级部门,二级部门,三级部门,备注",
            "T2024001,张三,ABC123,active,新能源事业部,电芯研发部,材料实验室,",
            "T2024002,李四,ABC124,active,新能源事业部,电芯研发部,材料实验室,",
        ]
    ).encode("utf-8")

    repo = FakeRepository()
    departments = FakeDepartments()
    service = PersonnelImportService(repository=repo, service=FakeService(), department_service=departments)
    result = service.import_personnel(file_bytes=csv_content, filename="personnel.csv")

    assert result["success"] is True
    assert result["data"]["summary"]["created"] == 2
    assert result["data"]["summary"]["created_departments_total"] == 3
    assert result["data"]["summary"]["created_primary_departments"] == 1
    assert result["data"]["summary"]["created_secondary_departments"] == 1
    assert result["data"]["summary"]["created_tertiary_departments"] == 1
    assert len(departments.calls) == 2
    assert repo.rows[0]["primary_department_id"] == 101
    assert repo.rows[1]["tertiary_department_id"] == 103


def test_import_personnel_marks_disabled_department_row_failed_and_continues():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows: list[dict], sync_bound_users: bool):
            self.rows = rows
            return {
                "created": len(rows),
                "updated": 0,
                "skipped": 0,
                "details": [
                    {
                        "row": row["line_no"],
                        "employee_no": row["employee_no"],
                        "full_name": row["full_name"],
                        "status": "created",
                    }
                    for row in rows
                ],
            }

    class FakeDepartments:
        def resolve_or_create_by_names(self, **kwargs):
            if kwargs["primary_name"] == "计算机学院":
                return {
                    "success": True,
                    "data": {
                        "primary_department_id": 1,
                        "secondary_department_id": None,
                        "tertiary_department_id": None,
                        "created_departments": {"primary": 0, "secondary": 0, "tertiary": 0, "total": 0},
                    },
                }
            return {"success": False, "error": "部门已停用，无法选择", "code": "DEPARTMENT_DISABLED"}

    csv_content = "\n".join(
        [
            "工号,姓名,校验码,状态,一级部门,二级部门,三级部门,备注",
            "T2024001,张三,ABC123,active,停用学院,,,",
            "T2024002,李四,ABC124,active,计算机学院,,,",
        ]
    ).encode("utf-8")

    class FakeService:
        def hash_verification_code(self, verification_code: str) -> str:
            return f"hashed-{verification_code}"

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, service=FakeService(), department_service=FakeDepartments())
    result = service.import_personnel(file_bytes=csv_content, filename="personnel.csv")

    assert result["success"] is True
    assert result["data"]["summary"]["created"] == 1
    assert result["data"]["summary"]["failed"] == 1
    assert repo.rows[0]["employee_no"] == "T2024002"
    assert result["data"]["details"][0]["status"] == "failed"
    assert "部门已停用" in result["data"]["details"][0]["reason"]


def test_import_personnel_marks_tertiary_name_without_secondary_name_failed_and_continues():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.rows = None

        def import_personnel_rows(self, *, rows: list[dict], sync_bound_users: bool):
            self.rows = rows
            return {
                "created": len(rows),
                "updated": 0,
                "skipped": 0,
                "details": [
                    {
                        "row": row["line_no"],
                        "employee_no": row["employee_no"],
                        "full_name": row["full_name"],
                        "status": "created",
                    }
                    for row in rows
                ],
            }

    class FakeDepartments:
        def resolve_or_create_by_names(self, **kwargs):
            return {
                "success": True,
                "data": {
                    "primary_department_id": 1,
                    "secondary_department_id": None,
                    "tertiary_department_id": None,
                    "created_departments": {"primary": 0, "secondary": 0, "tertiary": 0, "total": 0},
                },
            }

    csv_content = "\n".join(
        [
            "工号,姓名,校验码,状态,一级部门,二级部门,三级部门,备注",
            "T2024001,张三,ABC123,active,计算机学院,,智能软件实验室,",
            "T2024002,李四,ABC124,active,计算机学院,,,",
        ]
    ).encode("utf-8")

    class FakeService:
        def hash_verification_code(self, verification_code: str) -> str:
            return f"hashed-{verification_code}"

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, service=FakeService(), department_service=FakeDepartments())
    result = service.import_personnel(file_bytes=csv_content, filename="personnel.csv")

    assert result["success"] is True
    assert result["data"]["summary"]["created"] == 1
    assert result["data"]["summary"]["failed"] == 1
    assert repo.rows[0]["employee_no"] == "T2024002"
    assert result["data"]["details"][0]["status"] == "failed"
    assert "三级部门名称不能在二级部门为空时填写" in result["data"]["details"][0]["reason"]


def test_personnel_payload_includes_department_display_fields():
    module_spec = find_module_spec("app.modules.personnel.service")
    assert module_spec is not None

    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def count_personnel(self, **kwargs):
            return 1

        def list_personnel(self, **kwargs):
            return [
                {
                    "id": 9,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "status": "active",
                    "remarks": "备注",
                    "primary_department_id": 1,
                    "secondary_department_id": 11,
                    "tertiary_department_id": 111,
                    "binding_count": 2,
                    "created_at": None,
                    "updated_at": None,
                }
            ]

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            assert kwargs == {
                "primary_department_id": 1,
                "secondary_department_id": 11,
                "tertiary_department_id": 111,
            }
            return {
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "tertiary_department_id": 111,
                "tertiary_department_name": "智能软件实验室",
                "department_display": "计算机学院 / 软件工程系 / 智能软件实验室",
                "department_completion_level": "complete",
                "require_department_setup": False,
            }

    service = PersonnelService(repository=FakeRepository(), department_service=FakeDepartments(), users_repo=object())
    result = service.list_personnel(page=1, page_size=10)

    assert result["success"] is True
    assert result["data"]["items"][0]["primary_department_name"] == "计算机学院"
    assert result["data"]["items"][0]["secondary_department_name"] == "软件工程系"
    assert result["data"]["items"][0]["tertiary_department_name"] == "智能软件实验室"
    assert result["data"]["items"][0]["department_display"] == "计算机学院 / 软件工程系 / 智能软件实验室"


def test_backfill_service_reports_synced_missing_and_conflicting_personnel():
    module_spec = find_module_spec("app.modules.personnel.backfill_service")
    assert module_spec is not None

    from app.modules.personnel.backfill_service import PersonnelDepartmentBackfillService

    class FakeRepository:
        def list_personnel_for_backfill(self):
            return [
                {"id": 1, "employee_no": "T1", "full_name": "张三"},
                {"id": 2, "employee_no": "T2", "full_name": "李四"},
                {"id": 3, "employee_no": "T3", "full_name": "王五"},
            ]

        def list_bound_department_candidates(self, *, personnel_id: int):
            if personnel_id == 1:
                return [{"primary_department_id": 1, "secondary_department_id": 11, "tertiary_department_id": 111, "binding_count": 2}]
            if personnel_id == 2:
                return []
            return [
                {"primary_department_id": 1, "secondary_department_id": 11, "tertiary_department_id": 111, "binding_count": 1},
                {"primary_department_id": 2, "secondary_department_id": 21, "tertiary_department_id": 211, "binding_count": 1},
            ]

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": kwargs.get("primary_department_id"),
                "secondary_department_id": kwargs.get("secondary_department_id"),
                "tertiary_department_id": kwargs.get("tertiary_department_id"),
                "department_display": f"{kwargs.get('primary_department_id')}/{kwargs.get('secondary_department_id')}/{kwargs.get('tertiary_department_id')}",
            }

    service = PersonnelDepartmentBackfillService(repository=FakeRepository(), department_service=FakeDepartments(), users_repo=object())
    result = service.preview()

    assert result["success"] is True
    assert result["data"]["summary"] == {
        "total": 3,
        "synced": 1,
        "missing_department": 1,
        "conflicting_departments": 1,
    }


def test_backfill_service_apply_updates_only_synced_items():
    module_spec = find_module_spec("app.modules.personnel.backfill_service")
    assert module_spec is not None

    from app.modules.personnel.backfill_service import PersonnelDepartmentBackfillService

    class FakeRepository:
        def __init__(self) -> None:
            self.updated = []

        def list_personnel_for_backfill(self):
            return [
                {"id": 1, "employee_no": "T1", "full_name": "张三"},
                {"id": 2, "employee_no": "T2", "full_name": "李四"},
            ]

        def list_bound_department_candidates(self, *, personnel_id: int):
            if personnel_id == 1:
                return [{"primary_department_id": 1, "secondary_department_id": 11, "tertiary_department_id": 111, "binding_count": 2}]
            return [
                {"primary_department_id": 1, "secondary_department_id": 11, "tertiary_department_id": 111, "binding_count": 1},
                {"primary_department_id": 2, "secondary_department_id": 21, "tertiary_department_id": 211, "binding_count": 1},
            ]

        def backfill_personnel_department_and_sync_users(self, **kwargs):
            self.updated.append(kwargs)
            return 1

    class FakeDepartments:
        def describe_user_department(self, **kwargs):
            return {
                "primary_department_id": kwargs.get("primary_department_id"),
                "secondary_department_id": kwargs.get("secondary_department_id"),
                "tertiary_department_id": kwargs.get("tertiary_department_id"),
                "department_display": f"{kwargs.get('primary_department_id')}/{kwargs.get('secondary_department_id')}/{kwargs.get('tertiary_department_id')}",
            }

    repo = FakeRepository()
    service = PersonnelDepartmentBackfillService(repository=repo, department_service=FakeDepartments(), users_repo=object())
    result = service.apply()

    assert result["success"] is True
    assert repo.updated == [
        {
            "personnel_id": 1,
            "primary_department_id": 1,
            "secondary_department_id": 11,
            "tertiary_department_id": 111,
        }
    ]


def test_personnel_department_backfill_cli_reports_summary_and_exit_code(monkeypatch, capsys):
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "personnel_department_backfill.py"
    assert script_path.exists()

    spec = importlib.util.spec_from_file_location("personnel_department_backfill_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakeService:
        def preview(self):
            return {
                "success": True,
                "data": {
                    "summary": {
                        "total": 3,
                        "synced": 1,
                        "missing_department": 1,
                        "conflicting_departments": 1,
                    }
                },
            }

        def apply(self):
            return {
                "success": True,
                "data": {
                    "summary": {
                        "total": 3,
                        "synced": 1,
                        "missing_department": 0,
                        "conflicting_departments": 0,
                    }
                },
            }

    monkeypatch.setattr(module, "PersonnelDepartmentBackfillService", FakeService)

    assert module.main(["--dry-run"]) == 1
    dry_run_output = capsys.readouterr().out
    assert "conflicting_departments" in dry_run_output

    assert module.main(["--apply"]) == 0
    apply_output = capsys.readouterr().out
    assert "synced" in apply_output


def test_personnel_service_force_delete_requires_admin_password_before_unbinding():
    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def __init__(self):
            self.force_deleted = []

        def get_by_id(self, personnel_id: int):
            return {
                "id": personnel_id,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "binding_count": 2,
            }

        def force_delete_personnel_and_unbind_users(self, *, personnel_id: int):
            self.force_deleted.append(personnel_id)
            return {"deleted": 1, "unbound_users": 2}

    class FakeUsers:
        def get_by_id(self, user_id: int):
            return {"id": user_id, "role": "admin", "user_type": 1, "password_hash": "hash"}

    repo = FakeRepository()
    service = PersonnelService(repository=repo, users_repo=FakeUsers())
    service.verify_admin_password = lambda *, actor_user_id, admin_password: False

    result = service.force_delete_personnel(
        personnel_id=9,
        actor_user_id=1,
        admin_password="wrong-password",
    )

    assert result["success"] is False
    assert result["code"] == "ADMIN_PASSWORD_INVALID"
    assert repo.force_deleted == []


def test_personnel_service_force_delete_unbinds_accounts_and_deletes_personnel():
    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def get_by_id(self, personnel_id: int):
            return {
                "id": personnel_id,
                "employee_no": "T2024001",
                "full_name": "张三",
                "status": "active",
                "binding_count": 2,
            }

        def force_delete_personnel_and_unbind_users(self, *, personnel_id: int):
            assert personnel_id == 9
            return {"deleted": 1, "unbound_users": 2}

    service = PersonnelService(repository=FakeRepository(), users_repo=object())
    service.verify_admin_password = lambda *, actor_user_id, admin_password: True

    result = service.force_delete_personnel(
        personnel_id=9,
        actor_user_id=1,
        admin_password="admin-password",
    )

    assert result["success"] is True
    assert result["data"]["summary"]["deleted"] == 1
    assert result["data"]["summary"]["unbound_users"] == 2
    assert "已解绑 2 个账号" in result["message"]


def test_personnel_service_batch_force_delete_only_processes_requested_failed_items():
    from app.modules.personnel.service import PersonnelService

    class FakeRepository:
        def get_by_id(self, personnel_id: int):
            if personnel_id == 404:
                return None
            return {
                "id": personnel_id,
                "employee_no": f"T{personnel_id}",
                "full_name": f"人员{personnel_id}",
                "status": "active",
                "binding_count": 1,
            }

        def force_delete_personnel_and_unbind_users(self, *, personnel_id: int):
            return {"deleted": 1, "unbound_users": personnel_id}

    service = PersonnelService(repository=FakeRepository(), users_repo=object())
    service.verify_admin_password = lambda *, actor_user_id, admin_password: True

    result = service.batch_force_delete_personnel(
        personnel_ids=[9, 404, 9, 10],
        actor_user_id=1,
        admin_password="admin-password",
    )

    assert result["success"] is True
    assert result["data"]["summary"] == {
        "total": 3,
        "success": 2,
        "failed": 1,
        "unbound_users": 19,
    }
    assert result["data"]["details"][1]["code"] == "PERSONNEL_NOT_FOUND"
