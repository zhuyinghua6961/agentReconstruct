from __future__ import annotations

from time import monotonic
from typing import Any

from app.core.config import get_settings
from app.core.db import Database


class DepartmentRepository:
    def __init__(self, *, database: Database | None = None) -> None:
        self._db = database or Database(settings=get_settings())
        self._tables_cache: set[str] | None = None
        self._user_columns_cache: set[str] | None = None
        self._tables_cache_loaded_at = 0.0
        self._user_columns_cache_loaded_at = 0.0
        self._schema_cache_ttl_seconds = 1.0
        self._now = monotonic

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

    def _cache_valid(self, loaded_at: float) -> bool:
        return loaded_at > 0 and (self._now() - loaded_at) < self._schema_cache_ttl_seconds

    def _tables(self) -> set[str]:
        if self._cache_valid(self._tables_cache_loaded_at) and self._tables_cache is not None:
            return self._tables_cache
        self._tables_cache = self._load_tables()
        self._tables_cache_loaded_at = self._now()
        return self._tables_cache

    def has_table(self, table_name: str) -> bool:
        return table_name in self._tables()

    def _load_user_columns(self) -> set[str]:
        if not self.has_table("users"):
            return set()
        rows = self._execute_query("SHOW COLUMNS FROM users")
        return {str(row.get("Field") or "") for row in rows}

    def _user_columns(self) -> set[str]:
        if self._cache_valid(self._user_columns_cache_loaded_at) and self._user_columns_cache is not None:
            return self._user_columns_cache
        self._user_columns_cache = self._load_user_columns()
        self._user_columns_cache_loaded_at = self._now()
        return self._user_columns_cache

    def has_user_column(self, column_name: str) -> bool:
        return column_name in self._user_columns()

    def list_department_tree(self, *, include_disabled: bool) -> list[dict[str, Any]]:
        has_tertiary_table = self.has_table("tertiary_departments")
        if has_tertiary_table:
            return self._list_department_tree_with_tertiary(include_disabled=include_disabled)
        return self._list_department_tree_two_levels(include_disabled=include_disabled)

    def _list_department_tree_two_levels(self, *, include_disabled: bool) -> list[dict[str, Any]]:
        primary_where_clause = ""
        secondary_join_filter = ""
        if not include_disabled:
            primary_where_clause = "WHERE p.status = 'active'"
            secondary_join_filter = " AND s.status = 'active'"

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
                ON s.primary_department_id = p.id{secondary_join_filter}
            {user_count_join}
            {primary_where_clause}
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
                    "legacy_user_count": 0,
                    "tertiary_items": [],
                }
            )

        return items

    def _list_department_tree_with_tertiary(self, *, include_disabled: bool) -> list[dict[str, Any]]:
        primary_where_clause = ""
        secondary_join_filter = ""
        tertiary_join_filter = ""
        if not include_disabled:
            primary_where_clause = "WHERE p.status = 'active'"
            secondary_join_filter = " AND s.status = 'active'"
            tertiary_join_filter = " AND t.status = 'active'"

        secondary_user_count_select = "0 AS secondary_user_count"
        secondary_user_count_join = ""
        secondary_legacy_user_count_select = "0 AS secondary_legacy_user_count"
        secondary_legacy_user_count_join = ""
        tertiary_user_count_select = "0 AS tertiary_user_count"
        tertiary_user_count_join = ""

        if self.has_user_column("secondary_department_id"):
            secondary_user_count_select = "COALESCE(su.user_count, 0) AS secondary_user_count"
            secondary_user_count_join = """
            LEFT JOIN (
                SELECT secondary_department_id, COUNT(*) AS user_count
                FROM users
                WHERE secondary_department_id IS NOT NULL
                GROUP BY secondary_department_id
            ) su
                ON su.secondary_department_id = s.id
            """
            if self.has_user_column("tertiary_department_id"):
                secondary_legacy_user_count_select = "COALESCE(sl.legacy_user_count, 0) AS secondary_legacy_user_count"
                secondary_legacy_user_count_join = """
                LEFT JOIN (
                    SELECT secondary_department_id, COUNT(*) AS legacy_user_count
                    FROM users
                    WHERE secondary_department_id IS NOT NULL
                      AND tertiary_department_id IS NULL
                    GROUP BY secondary_department_id
                ) sl
                    ON sl.secondary_department_id = s.id
                """
            else:
                secondary_legacy_user_count_select = "COALESCE(su.user_count, 0) AS secondary_legacy_user_count"
        if self.has_user_column("tertiary_department_id"):
            tertiary_user_count_select = "COALESCE(tu.user_count, 0) AS tertiary_user_count"
            tertiary_user_count_join = """
            LEFT JOIN (
                SELECT tertiary_department_id, COUNT(*) AS user_count
                FROM users
                WHERE tertiary_department_id IS NOT NULL
                GROUP BY tertiary_department_id
            ) tu
                ON tu.tertiary_department_id = t.id
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
                {secondary_user_count_select},
                {secondary_legacy_user_count_select},
                t.id AS tertiary_id,
                t.secondary_department_id AS tertiary_secondary_department_id,
                t.name AS tertiary_name,
                t.status AS tertiary_status,
                {tertiary_user_count_select}
            FROM primary_departments p
            LEFT JOIN secondary_departments s
                ON s.primary_department_id = p.id{secondary_join_filter}
            LEFT JOIN tertiary_departments t
                ON t.secondary_department_id = s.id{tertiary_join_filter}
            {secondary_user_count_join}
            {secondary_legacy_user_count_join}
            {tertiary_user_count_join}
            {primary_where_clause}
            ORDER BY p.id ASC, s.id ASC, t.id ASC
            """
        )

        items: list[dict[str, Any]] = []
        primary_index: dict[int, dict[str, Any]] = {}
        secondary_index: dict[tuple[int, int], dict[str, Any]] = {}

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

            secondary_key = (primary_id, int(secondary_id))
            secondary_item = secondary_index.get(secondary_key)
            if secondary_item is None:
                secondary_item = {
                    "id": int(secondary_id),
                    "name": row["secondary_name"],
                    "status": row["secondary_status"],
                    "user_count": int(row.get("secondary_user_count") or 0),
                    "legacy_user_count": int(row.get("secondary_legacy_user_count") or 0),
                    "tertiary_items": [],
                }
                secondary_index[secondary_key] = secondary_item
                primary_item["secondary_items"].append(secondary_item)

            tertiary_id = row.get("tertiary_id")
            if tertiary_id is None:
                continue

            secondary_item["tertiary_items"].append(
                {
                    "id": int(tertiary_id),
                    "secondary_department_id": int(row.get("tertiary_secondary_department_id") or secondary_item["id"]),
                    "name": row["tertiary_name"],
                    "status": row["tertiary_status"],
                    "user_count": int(row.get("tertiary_user_count") or 0),
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

    def get_tertiary_by_id(self, tertiary_id: int) -> dict[str, Any] | None:
        if not self.has_table("tertiary_departments"):
            return None
        rows = self._execute_query(
            """
            SELECT id, secondary_department_id, name, status
            FROM tertiary_departments
            WHERE id = %s
            LIMIT 1
            """,
            (int(tertiary_id),),
        )
        return rows[0] if rows else None

    def get_tertiary_by_name(self, *, secondary_department_id: int, name: str) -> dict[str, Any] | None:
        if not self.has_table("tertiary_departments"):
            return None
        rows = self._execute_query(
            """
            SELECT id, secondary_department_id, name, status
            FROM tertiary_departments
            WHERE secondary_department_id = %s AND name = %s
            LIMIT 1
            """,
            (int(secondary_department_id), str(name or "").strip()),
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

    def list_users_by_tertiary_department(self, *, tertiary_id: int) -> list[dict[str, Any]]:
        if not self.has_user_column("tertiary_department_id"):
            return []

        fields = ["id", "username", "role", "status"]
        if self.has_user_column("user_type"):
            fields.insert(3, "user_type")

        return self._execute_query(
            f"""
            SELECT {", ".join(fields)}
            FROM users
            WHERE tertiary_department_id = %s
            ORDER BY username ASC, id ASC
            """,
            (int(tertiary_id),),
        )

    def list_legacy_users_by_secondary_department(self, *, secondary_id: int) -> list[dict[str, Any]]:
        if not self.has_user_column("secondary_department_id"):
            return []

        fields = ["id", "username", "role", "status"]
        if self.has_user_column("user_type"):
            fields.insert(3, "user_type")

        where_clause = "secondary_department_id = %s"
        if self.has_user_column("tertiary_department_id"):
            where_clause += " AND tertiary_department_id IS NULL"

        return self._execute_query(
            f"""
            SELECT {", ".join(fields)}
            FROM users
            WHERE {where_clause}
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

    def create_tertiary(self, *, secondary_department_id: int, name: str) -> int:
        if not self.has_table("tertiary_departments"):
            return 0
        return self._execute_update(
            """
            INSERT INTO tertiary_departments (secondary_department_id, name, status)
            VALUES (%s, %s, 'active')
            """,
            (int(secondary_department_id), str(name or "").strip()),
        )

    def update_tertiary_name(self, *, tertiary_id: int, name: str) -> int:
        if not self.has_table("tertiary_departments"):
            return 0
        return self._execute_update(
            """
            UPDATE tertiary_departments
            SET name = %s
            WHERE id = %s
            """,
            (str(name or "").strip(), int(tertiary_id)),
        )

    def update_tertiary_status(self, *, tertiary_id: int, status: str) -> int:
        if not self.has_table("tertiary_departments"):
            return 0
        return self._execute_update(
            """
            UPDATE tertiary_departments
            SET status = %s
            WHERE id = %s
            """,
            (str(status or "").strip(), int(tertiary_id)),
        )
