from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.db import Database


class DepartmentRepository:
    def __init__(self, *, database: Database | None = None) -> None:
        self._db = database or Database(settings=get_settings())
        self._tables_cache: set[str] | None = None
        self._user_columns_cache: set[str] | None = None

    def _execute_query(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._db.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall() or []
        return [dict(row) for row in rows]

    def _execute_update(self, query: str, params: tuple[Any, ...] = ()) -> int:
        with self._db.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                lastrowid = int(cursor.lastrowid or 0)
                if lastrowid > 0:
                    return lastrowid
                return int(cursor.rowcount or 0)

    def _load_tables(self) -> set[str]:
        rows = self._execute_query("SHOW TABLES")
        names: set[str] = set()
        for row in rows:
            values = list(row.values())
            if values:
                names.add(str(values[0] or ""))
        return names

    def _tables(self) -> set[str]:
        if self._tables_cache is None:
            self._tables_cache = self._load_tables()
        return self._tables_cache

    def has_table(self, table_name: str) -> bool:
        return table_name in self._tables()

    def _load_user_columns(self) -> set[str]:
        if not self.has_table("users"):
            return set()
        rows = self._execute_query("SHOW COLUMNS FROM users")
        return {str(row.get("Field") or "") for row in rows}

    def _user_columns(self) -> set[str]:
        if self._user_columns_cache is None:
            self._user_columns_cache = self._load_user_columns()
        return self._user_columns_cache

    def has_user_column(self, column_name: str) -> bool:
        return column_name in self._user_columns()

    def list_department_tree(self, *, include_disabled: bool) -> list[dict[str, Any]]:
        where_clause = ""
        if not include_disabled:
            where_clause = """
            WHERE p.status = 'active'
              AND (s.id IS NULL OR s.status = 'active')
            """

        user_count_select = "0 AS secondary_user_count"
        user_count_join = ""
        if self.has_user_column("secondary_department_id"):
            user_count_select = "COALESCE(u.user_count, 0) AS secondary_user_count"
            user_count_join = """
            LEFT JOIN (
                SELECT secondary_department_id, COUNT(*) AS user_count
                FROM users
                WHERE secondary_department_id IS NOT NULL
                GROUP BY secondary_department_id
            ) u
                ON u.secondary_department_id = s.id
            """

        rows = self._execute_query(
            f"""
            SELECT
                p.id AS primary_id,
                p.name AS primary_name,
                p.status AS primary_status,
                s.id AS secondary_id,
                s.name AS secondary_name,
                s.status AS secondary_status,
                {user_count_select}
            FROM primary_departments p
            LEFT JOIN secondary_departments s
                ON s.primary_department_id = p.id
            {user_count_join}
            {where_clause}
            ORDER BY p.id ASC, s.id ASC
            """
        )

        items: list[dict[str, Any]] = []
        primary_index: dict[int, dict[str, Any]] = {}

        for row in rows:
            primary_id = int(row["primary_id"])
            primary_item = primary_index.get(primary_id)
            if primary_item is None:
                primary_item = {
                    "primary_id": primary_id,
                    "primary_name": row["primary_name"],
                    "primary_status": row["primary_status"],
                    "secondary_items": [],
                }
                primary_index[primary_id] = primary_item
                items.append(primary_item)

            secondary_id = row.get("secondary_id")
            if secondary_id is None:
                continue

            primary_item["secondary_items"].append(
                {
                    "id": int(secondary_id),
                    "name": row["secondary_name"],
                    "status": row["secondary_status"],
                    "user_count": int(row.get("secondary_user_count") or 0),
                }
            )

        return items

    def get_primary_by_id(self, primary_id: int) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT id, name, status
            FROM primary_departments
            WHERE id = %s
            LIMIT 1
            """,
            (int(primary_id),),
        )
        return rows[0] if rows else None

    def get_primary_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT id, name, status
            FROM primary_departments
            WHERE name = %s
            LIMIT 1
            """,
            (str(name or "").strip(),),
        )
        return rows[0] if rows else None

    def get_secondary_by_id(self, secondary_id: int) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT id, primary_department_id, name, status
            FROM secondary_departments
            WHERE id = %s
            LIMIT 1
            """,
            (int(secondary_id),),
        )
        return rows[0] if rows else None

    def get_secondary_by_name(self, *, primary_department_id: int, name: str) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT id, primary_department_id, name, status
            FROM secondary_departments
            WHERE primary_department_id = %s AND name = %s
            LIMIT 1
            """,
            (int(primary_department_id), str(name or "").strip()),
        )
        return rows[0] if rows else None

    def list_users_by_secondary_department(self, *, secondary_id: int) -> list[dict[str, Any]]:
        if not self.has_user_column("secondary_department_id"):
            return []

        fields = ["id", "username", "role", "status"]
        if self.has_user_column("user_type"):
            fields.insert(3, "user_type")

        return self._execute_query(
            f"""
            SELECT {", ".join(fields)}
            FROM users
            WHERE secondary_department_id = %s
            ORDER BY username ASC, id ASC
            """,
            (int(secondary_id),),
        )

    def create_primary(self, *, name: str) -> int:
        return self._execute_update(
            """
            INSERT INTO primary_departments (name, status)
            VALUES (%s, 'active')
            """,
            (str(name or "").strip(),),
        )

    def update_primary_name(self, *, primary_id: int, name: str) -> int:
        return self._execute_update(
            """
            UPDATE primary_departments
            SET name = %s
            WHERE id = %s
            """,
            (str(name or "").strip(), int(primary_id)),
        )

    def update_primary_status(self, *, primary_id: int, status: str) -> int:
        return self._execute_update(
            """
            UPDATE primary_departments
            SET status = %s
            WHERE id = %s
            """,
            (str(status or "").strip(), int(primary_id)),
        )

    def create_secondary(self, *, primary_department_id: int, name: str) -> int:
        return self._execute_update(
            """
            INSERT INTO secondary_departments (primary_department_id, name, status)
            VALUES (%s, %s, 'active')
            """,
            (int(primary_department_id), str(name or "").strip()),
        )

    def update_secondary_name(self, *, secondary_id: int, name: str) -> int:
        return self._execute_update(
            """
            UPDATE secondary_departments
            SET name = %s
            WHERE id = %s
            """,
            (str(name or "").strip(), int(secondary_id)),
        )

    def update_secondary_status(self, *, secondary_id: int, status: str) -> int:
        return self._execute_update(
            """
            UPDATE secondary_departments
            SET status = %s
            WHERE id = %s
            """,
            (str(status or "").strip(), int(secondary_id)),
        )
