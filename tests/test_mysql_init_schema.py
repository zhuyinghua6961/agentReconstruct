from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "deploy" / "mysql-init" / "001_schema.sql"


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
