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

    rows = repo.list_bindings(personnel_id=9)

    assert rows == [
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
                "binding_count": 0,
                "created_at": None,
                "updated_at": None,
            }

    repo = FakeRepository()
    service = PersonnelService(repository=repo)
    result = service.create_personnel(
        employee_no="T2024001",
        full_name="张三",
        verification_code="ABC123",
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
    service = PersonnelService(repository=repo)
    result = service.update_personnel(
        personnel_id=9,
        full_name="李四",
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
    service = PersonnelService(repository=repo)
    result = service.update_personnel(personnel_id=9, full_name="张三", remarks=None)

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
        "employee_no,full_name,verification_code,status,remarks\n"
        "T2024001,张三,AAA111,active,化学学院\n"
        "T2024001,李四,BBB222,disabled,材料系\n"
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
    service = PersonnelImportService(repository=repo)
    csv_bytes = (
        "employee_no,full_name,verification_code,status,remarks\n"
        "T2024001,张三,AAA111,disabled,化学学院\n"
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
    service = PersonnelImportService(repository=repo)
    csv_bytes = (
        "employee_no,full_name,verification_code,status,remarks\n"
        "T2024001,张三,AAA111,disabled,\n"
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
    service = PersonnelImportService(repository=repo)
    csv_bytes = (
        "employee_no,full_name,verification_code,status\n"
        "T2024001,张三,AAA111,disabled\n"
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
    service = PersonnelImportService(repository=repo)
    csv_bytes = (
        "employee_no,full_name,verification_code,status,remarks\n"
        "T2024001,张三,AAA111,active,化学学院\n"
        "T2024002,李四,,active,材料系\n"
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

    assert response_without_remarks.status_code == 200
    assert response_with_null_remarks.status_code == 200
    assert captured_calls[0]["remarks"] is REMARKS_UNSET
    assert captured_calls[1]["remarks"] is None
