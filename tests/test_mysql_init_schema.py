from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "deploy" / "mysql-init" / "001_schema.sql"
ADMIN_SEED = ROOT / "deploy" / "mysql-init" / "003_seed_admin.sql"
DEPARTMENT_SEED = ROOT / "deploy" / "mysql-init" / "002_seed_departments.sql"
EXPECTED_ADMIN_HASH = (
    "pbkdf2_sha256$120000$daa41997f72e67a45a78c9fa3f45c55b$"
    "fb7154bc11eaeb476133a82415e2515f8bb99e7c5190cd5c711ab24124e1361a"
)


def test_mysql_init_schema_contains_personnel_and_department_tables() -> None:
    content = SCHEMA.read_text(encoding="utf-8")

    for table_name in [
        "primary_departments",
        "secondary_departments",
        "tertiary_departments",
        "personnel_records",
    ]:
        assert f"CREATE TABLE `{table_name}`" in content


def test_mysql_init_schema_contains_user_personnel_department_bindings() -> None:
    content = SCHEMA.read_text(encoding="utf-8")

    for column_name in [
        "primary_department_id",
        "secondary_department_id",
        "tertiary_department_id",
        "personnel_id",
    ]:
        assert f"`{column_name}` bigint DEFAULT NULL" in content

    for constraint_name in [
        "fk_users_primary_department",
        "fk_users_secondary_department",
        "fk_users_tertiary_department",
        "fk_users_personnel",
    ]:
        assert f"CONSTRAINT `{constraint_name}`" in content


def test_mysql_init_includes_idempotent_default_admin_seed() -> None:
    content = ADMIN_SEED.read_text(encoding="utf-8")

    assert "INSERT INTO `users`" in content
    assert "`username`" in content
    assert "'admin'" in content
    assert "`role`" in content
    assert "`user_type`" in content
    assert "`is_first_login`" in content
    assert "`must_set_security_questions`" in content
    assert "'active'" in content
    assert "ON DUPLICATE KEY UPDATE" in content
    assert "`username` = `username`" in content
    assert "INSERT INTO `password_history`" in content
    assert "NOT EXISTS" in content

    match = re.search(r"'(pbkdf2_sha256\$\d+\$[0-9a-f]+\$[0-9a-f]+)'", content)
    assert match, "default admin seed must store a PBKDF2 password hash"

    assert match.group(1) == EXPECTED_ADMIN_HASH


def test_department_seed_declares_utf8mb4_client_charset() -> None:
    content = DEPARTMENT_SEED.read_text(encoding="utf-8")

    assert "SET NAMES utf8mb4" in content
