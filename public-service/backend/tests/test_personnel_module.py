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
            "allow_legacy_two_level": False,
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
            "allow_legacy_two_level": False,
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


def test_personnel_repository_import_rows_rolls_back_on_write_error():
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
            if "SELECT id FROM personnel_records" in query_text:
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

    try:
        repo.import_personnel_rows(
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
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")

    assert connection.begin_called == 1
    assert connection.commit_called == 0
    assert connection.rollback_called == 1
    assert connection.cursor_instance.inserted_rows == ["T2024001", "T2024002"]


def test_personnel_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/api/admin/personnel" in paths
    assert "/api/admin/personnel/{personnel_id}" in paths
    assert "/api/admin/personnel/{personnel_id}/status" in paths
    assert "/api/admin/personnel/{personnel_id}/bindings" in paths
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


def test_personnel_import_rejects_duplicate_employee_no_inside_file():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    service = PersonnelImportService(repository=object())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks\n"
        "T2024001,张三,AAA111,active,计算机学院,软件工程系,智能软件实验室,化学学院\n"
        "T2024001,李四,BBB222,disabled,计算机学院,软件工程系,智能软件实验室,材料系\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert "T2024001" in result["error"]


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


def test_personnel_import_is_atomic_when_late_row_validation_fails():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    class FakeRepository:
        def __init__(self) -> None:
            self.create_calls = []
            self.update_calls = []

        def get_by_employee_no(self, employee_no: str):
            return None

        def update_personnel(self, **kwargs):
            self.update_calls.append(kwargs)
            return 1

        def create_personnel(self, **kwargs):
            self.create_calls.append(kwargs)
            return 10

    repo = FakeRepository()
    service = PersonnelImportService(repository=repo, department_service=_FakeThreeLevelDepartments())
    csv_bytes = (
        "employee_no,full_name,verification_code,status,primary_department_name,secondary_department_name,tertiary_department_name,remarks\n"
        "T2024001,张三,AAA111,active,计算机学院,软件工程系,智能软件实验室,化学学院\n"
        "T2024002,李四,,active,计算机学院,软件工程系,智能软件实验室,材料系\n"
    ).encode("utf-8")

    result = service.import_personnel(file_bytes=csv_bytes, filename="personnel.csv")

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert "第 3 行校验码为空" == result["error"]
    assert repo.create_calls == []
    assert repo.update_calls == []


def test_personnel_template_supports_csv_and_xlsx():
    module_spec = find_module_spec("app.modules.personnel.import_service")
    assert module_spec is not None

    from app.modules.personnel.import_service import PersonnelImportService

    service = PersonnelImportService(repository=object())
    csv_response = service.template_response(fmt="csv")
    xlsx_response = service.template_response(fmt="xlsx")

    assert csv_response.headers["Content-Disposition"].endswith('personnel_import_template.csv"')
    assert "employee_no" in csv_response.body.decode("utf-8-sig")
    assert xlsx_response.headers["Content-Disposition"].endswith('personnel_import_template.xlsx"')
    assert xlsx_response.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_personnel_admin_routes_registered(monkeypatch):
    module_spec = find_module_spec("app.modules.personnel.api")
    assert module_spec is not None

    from app.core.deps import AuthContext
    from app.modules.personnel.api import PersonnelUpdateRequest
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
                "allow_legacy_two_level": False,
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
                "allow_legacy_two_level": False,
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
        def resolve_by_names(self, **kwargs):
            assert kwargs == {
                "primary_name": "计算机学院",
                "secondary_name": "软件工程系",
                "tertiary_name": "智能软件实验室",
                "active_only": True,
                "allow_legacy_two_level": False,
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
