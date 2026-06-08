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
        self._table_columns_cache: dict[str, set[str]] = {}
        self._tables_cache_loaded_at = 0.0
        self._user_columns_cache_loaded_at = 0.0
        self._table_columns_cache_loaded_at: dict[str, float] = {}
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

    def has_personnel_column(self, column_name: str) -> bool:
        return self.has_table_column("personnel_records", column_name)

    @staticmethod
    def _identifier(value: str) -> str:
        return f"`{str(value).replace('`', '``')}`"

    def _load_table_columns(self, table_name: str) -> set[str]:
        if not self.has_table(table_name):
            return set()
        rows = self._execute_query(f"SHOW COLUMNS FROM {self._identifier(table_name)}")
        return {str(row.get("Field") or "") for row in rows}

    def _table_columns(self, table_name: str) -> set[str]:
        loaded_at = self._table_columns_cache_loaded_at.get(table_name, 0.0)
        if self._cache_valid(loaded_at) and table_name in self._table_columns_cache:
            return self._table_columns_cache[table_name]
        self._table_columns_cache[table_name] = self._load_table_columns(table_name)
        self._table_columns_cache_loaded_at[table_name] = self._now()
        return self._table_columns_cache[table_name]

    def has_table_column(self, table_name: str, column_name: str) -> bool:
        return column_name in self._table_columns(table_name)

    def _count_rows(self, query: str, params: tuple[Any, ...] = ()) -> int:
        rows = self._execute_query(query, params)
        if not rows:
            return 0
        return int(rows[0].get("total") or 0)

    def _count_by_column(self, *, table_name: str, column_name: str, value: int) -> int:
        if not self.has_table_column(table_name, column_name):
            return 0
        return self._count_rows(
            f"""
            SELECT COUNT(*) AS total
            FROM {self._identifier(table_name)}
            WHERE {self._identifier(column_name)} = %s
            """,
            (int(value),),
        )

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
        primary_direct_user_count_select = "0 AS primary_direct_user_count"
        primary_direct_user_count_join = ""
        secondary_direct_user_count_select = "0 AS secondary_direct_user_count"
        secondary_direct_user_count_join = ""
        has_primary_personnel_column = self.has_personnel_column("primary_department_id")
        has_secondary_personnel_column = self.has_personnel_column("secondary_department_id")
        has_tertiary_personnel_column = self.has_personnel_column("tertiary_department_id")

        if has_primary_personnel_column:
            primary_direct_user_count_select = "COALESCE(pm.direct_user_count, 0) AS primary_direct_user_count"
            secondary_null_clause = "AND secondary_department_id IS NULL" if has_secondary_personnel_column else ""
            primary_direct_user_count_join = """
            LEFT JOIN (
                SELECT primary_department_id, COUNT(*) AS direct_user_count
                FROM personnel_records
                WHERE primary_department_id IS NOT NULL
                  {secondary_null_clause}
                GROUP BY primary_department_id
            ) pm
                ON pm.primary_department_id = p.id
            """.format(secondary_null_clause=secondary_null_clause)
        if has_secondary_personnel_column:
            user_count_select = "COALESCE(pm.user_count, 0) AS secondary_user_count"
            user_count_join = """
            LEFT JOIN (
                SELECT secondary_department_id, COUNT(*) AS user_count
                FROM personnel_records
                WHERE secondary_department_id IS NOT NULL
                GROUP BY secondary_department_id
            ) pm
                ON pm.secondary_department_id = s.id
            """
            secondary_direct_user_count_select = "COALESCE(sdm.direct_user_count, 0) AS secondary_direct_user_count"
            tertiary_null_clause = "AND tertiary_department_id IS NULL" if has_tertiary_personnel_column else ""
            secondary_direct_user_count_join = """
            LEFT JOIN (
                SELECT secondary_department_id, COUNT(*) AS direct_user_count
                FROM personnel_records
                WHERE secondary_department_id IS NOT NULL
                  {tertiary_null_clause}
                GROUP BY secondary_department_id
            ) sdm
                ON sdm.secondary_department_id = s.id
            """.format(tertiary_null_clause=tertiary_null_clause)

        rows = self._execute_query(
            f"""
            SELECT
                p.id AS primary_id,
                p.name AS primary_name,
                p.status AS primary_status,
                s.id AS secondary_id,
                s.name AS secondary_name,
                s.status AS secondary_status,
                {primary_direct_user_count_select},
                {user_count_select},
                {secondary_direct_user_count_select}
            FROM primary_departments p
            LEFT JOIN secondary_departments s
                ON s.primary_department_id = p.id{secondary_join_filter}
            {primary_direct_user_count_join}
            {user_count_join}
            {secondary_direct_user_count_join}
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
                    "direct_user_count": int(row.get("primary_direct_user_count") or 0),
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
                    "direct_user_count": int(row.get("secondary_direct_user_count", row.get("secondary_legacy_user_count")) or 0),
                    "legacy_user_count": int(row.get("secondary_legacy_user_count", row.get("secondary_direct_user_count")) or 0),
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
        primary_direct_user_count_select = "0 AS primary_direct_user_count"
        primary_direct_user_count_join = ""
        secondary_direct_user_count_select = "0 AS secondary_direct_user_count"
        secondary_direct_user_count_join = ""
        tertiary_user_count_select = "0 AS tertiary_user_count"
        tertiary_user_count_join = ""

        has_primary_personnel_column = self.has_personnel_column("primary_department_id")
        has_secondary_personnel_column = self.has_personnel_column("secondary_department_id")
        has_tertiary_personnel_column = self.has_personnel_column("tertiary_department_id")

        if has_primary_personnel_column:
            primary_direct_user_count_select = "COALESCE(pm.direct_user_count, 0) AS primary_direct_user_count"
            secondary_null_clause = "AND secondary_department_id IS NULL" if has_secondary_personnel_column else ""
            primary_direct_user_count_join = """
            LEFT JOIN (
                SELECT primary_department_id, COUNT(*) AS direct_user_count
                FROM personnel_records
                WHERE primary_department_id IS NOT NULL
                  {secondary_null_clause}
                GROUP BY primary_department_id
            ) pm
                ON pm.primary_department_id = p.id
            """.format(secondary_null_clause=secondary_null_clause)
        if has_secondary_personnel_column:
            secondary_user_count_select = "COALESCE(sm.user_count, 0) AS secondary_user_count"
            secondary_user_count_join = """
            LEFT JOIN (
                SELECT secondary_department_id, COUNT(*) AS user_count
                FROM personnel_records
                WHERE secondary_department_id IS NOT NULL
                GROUP BY secondary_department_id
            ) sm
                ON sm.secondary_department_id = s.id
            """
            secondary_direct_user_count_select = "COALESCE(sdm.direct_user_count, 0) AS secondary_direct_user_count"
            if has_tertiary_personnel_column:
                secondary_direct_user_count_join = """
                LEFT JOIN (
                    SELECT secondary_department_id, COUNT(*) AS direct_user_count
                    FROM personnel_records
                    WHERE secondary_department_id IS NOT NULL
                      AND tertiary_department_id IS NULL
                    GROUP BY secondary_department_id
                ) sdm
                    ON sdm.secondary_department_id = s.id
                """
            else:
                secondary_direct_user_count_join = """
                LEFT JOIN (
                    SELECT secondary_department_id, COUNT(*) AS direct_user_count
                    FROM personnel_records
                    WHERE secondary_department_id IS NOT NULL
                    GROUP BY secondary_department_id
                ) sdm
                    ON sdm.secondary_department_id = s.id
                """
        if has_tertiary_personnel_column:
            tertiary_user_count_select = "COALESCE(tm.user_count, 0) AS tertiary_user_count"
            tertiary_user_count_join = """
            LEFT JOIN (
                SELECT tertiary_department_id, COUNT(*) AS user_count
                FROM personnel_records
                WHERE tertiary_department_id IS NOT NULL
                GROUP BY tertiary_department_id
            ) tm
                ON tm.tertiary_department_id = t.id
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
                {primary_direct_user_count_select},
                {secondary_user_count_select},
                {secondary_direct_user_count_select},
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
            {primary_direct_user_count_join}
            {secondary_user_count_join}
            {secondary_direct_user_count_join}
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
                    "direct_user_count": int(row.get("primary_direct_user_count") or 0),
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
                    "direct_user_count": int(row.get("secondary_direct_user_count", row.get("secondary_legacy_user_count")) or 0),
                    "legacy_user_count": int(row.get("secondary_legacy_user_count", row.get("secondary_direct_user_count")) or 0),
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

    def list_secondary_ids_by_primary(self, *, primary_id: int) -> list[int]:
        rows = self._execute_query(
            """
            SELECT id
            FROM secondary_departments
            WHERE primary_department_id = %s
            ORDER BY id ASC
            """,
            (int(primary_id),),
        )
        return [int(row["id"]) for row in rows]

    def list_tertiary_ids_by_secondary(self, *, secondary_id: int) -> list[int]:
        if not self.has_table("tertiary_departments"):
            return []
        rows = self._execute_query(
            """
            SELECT id
            FROM tertiary_departments
            WHERE secondary_department_id = %s
            ORDER BY id ASC
            """,
            (int(secondary_id),),
        )
        return [int(row["id"]) for row in rows]

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

    def _personnel_list_fields(self) -> list[str]:
        fields = ["id", "employee_no", "full_name", "status"]
        if self.has_personnel_column("remarks"):
            fields.append("remarks")
        return fields

    def list_personnel_by_secondary_department(self, *, secondary_id: int) -> list[dict[str, Any]]:
        if not self.has_personnel_column("secondary_department_id"):
            return []

        return self._execute_query(
            f"""
            SELECT {", ".join(self._personnel_list_fields())}
            FROM personnel_records
            WHERE secondary_department_id = %s
            ORDER BY employee_no ASC, id ASC
            """,
            (int(secondary_id),),
        )

    def list_personnel_by_tertiary_department(self, *, tertiary_id: int) -> list[dict[str, Any]]:
        if not self.has_personnel_column("tertiary_department_id"):
            return []

        return self._execute_query(
            f"""
            SELECT {", ".join(self._personnel_list_fields())}
            FROM personnel_records
            WHERE tertiary_department_id = %s
            ORDER BY employee_no ASC, id ASC
            """,
            (int(tertiary_id),),
        )

    def list_direct_personnel_by_primary_department(self, *, primary_id: int) -> list[dict[str, Any]]:
        if not self.has_personnel_column("primary_department_id"):
            return []

        where_clause = "primary_department_id = %s"
        if self.has_personnel_column("secondary_department_id"):
            where_clause += " AND secondary_department_id IS NULL"

        return self._execute_query(
            f"""
            SELECT {", ".join(self._personnel_list_fields())}
            FROM personnel_records
            WHERE {where_clause}
            ORDER BY employee_no ASC, id ASC
            """,
            (int(primary_id),),
        )

    def list_direct_personnel_by_secondary_department(self, *, secondary_id: int) -> list[dict[str, Any]]:
        if not self.has_personnel_column("secondary_department_id"):
            return []

        where_clause = "secondary_department_id = %s"
        if self.has_personnel_column("tertiary_department_id"):
            where_clause += " AND tertiary_department_id IS NULL"

        return self._execute_query(
            f"""
            SELECT {", ".join(self._personnel_list_fields())}
            FROM personnel_records
            WHERE {where_clause}
            ORDER BY employee_no ASC, id ASC
            """,
            (int(secondary_id),),
        )

    def list_direct_users_by_primary_department(self, *, primary_id: int) -> list[dict[str, Any]]:
        if not self.has_user_column("primary_department_id"):
            return []

        fields = ["id", "username", "role", "status"]
        if self.has_user_column("user_type"):
            fields.insert(3, "user_type")

        where_clause = "primary_department_id = %s"
        if self.has_user_column("secondary_department_id"):
            where_clause += " AND secondary_department_id IS NULL"

        return self._execute_query(
            f"""
            SELECT {", ".join(fields)}
            FROM users
            WHERE {where_clause}
            ORDER BY username ASC, id ASC
            """,
            (int(primary_id),),
        )

    def list_direct_users_by_secondary_department(self, *, secondary_id: int) -> list[dict[str, Any]]:
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

    def list_legacy_users_by_secondary_department(self, *, secondary_id: int) -> list[dict[str, Any]]:
        return self.list_direct_users_by_secondary_department(secondary_id=secondary_id)

    def count_secondary_departments_by_primary(self, *, primary_id: int) -> int:
        return self._count_rows(
            """
            SELECT COUNT(*) AS total
            FROM secondary_departments
            WHERE primary_department_id = %s
            """,
            (int(primary_id),),
        )

    def count_tertiary_departments_by_secondary(self, *, secondary_id: int) -> int:
        if not self.has_table("tertiary_departments"):
            return 0
        return self._count_rows(
            """
            SELECT COUNT(*) AS total
            FROM tertiary_departments
            WHERE secondary_department_id = %s
            """,
            (int(secondary_id),),
        )

    def count_users_by_primary_department(self, *, primary_id: int) -> int:
        return self._count_by_column(table_name="users", column_name="primary_department_id", value=int(primary_id))

    def count_users_by_secondary_department(self, *, secondary_id: int) -> int:
        return self._count_by_column(table_name="users", column_name="secondary_department_id", value=int(secondary_id))

    def count_users_by_tertiary_department(self, *, tertiary_id: int) -> int:
        return self._count_by_column(table_name="users", column_name="tertiary_department_id", value=int(tertiary_id))

    def count_personnel_by_primary_department(self, *, primary_id: int) -> int:
        return self._count_by_column(table_name="personnel_records", column_name="primary_department_id", value=int(primary_id))

    def count_personnel_by_secondary_department(self, *, secondary_id: int) -> int:
        return self._count_by_column(table_name="personnel_records", column_name="secondary_department_id", value=int(secondary_id))

    def count_personnel_by_tertiary_department(self, *, tertiary_id: int) -> int:
        return self._count_by_column(table_name="personnel_records", column_name="tertiary_department_id", value=int(tertiary_id))

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

    def delete_primary(self, *, primary_id: int) -> int:
        return self._execute_update(
            """
            DELETE FROM primary_departments
            WHERE id = %s
            """,
            (int(primary_id),),
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

    def delete_secondary(self, *, secondary_id: int) -> int:
        return self._execute_update(
            """
            DELETE FROM secondary_departments
            WHERE id = %s
            """,
            (int(secondary_id),),
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

    def delete_tertiary(self, *, tertiary_id: int) -> int:
        if not self.has_table("tertiary_departments"):
            return 0
        return self._execute_update(
            """
            DELETE FROM tertiary_departments
            WHERE id = %s
            """,
            (int(tertiary_id),),
        )

    @staticmethod
    def _ordered_unique(values: list[int]) -> list[int]:
        seen: set[int] = set()
        result: list[int] = []
        for value in values:
            normalized = int(value)
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _department_subtree_ids(self, *, level: str, department_id: int) -> dict[str, list[int]]:
        normalized_level = str(level or "").strip().lower()
        normalized_id = int(department_id)
        primary_ids: list[int] = []
        secondary_ids: list[int] = []
        tertiary_ids: list[int] = []

        if normalized_level == "primary":
            primary_ids.append(normalized_id)
            secondary_ids.extend(self.list_secondary_ids_by_primary(primary_id=normalized_id))
            for secondary_id in list(secondary_ids):
                tertiary_ids.extend(self.list_tertiary_ids_by_secondary(secondary_id=secondary_id))
        elif normalized_level == "secondary":
            secondary_ids.append(normalized_id)
            tertiary_ids.extend(self.list_tertiary_ids_by_secondary(secondary_id=normalized_id))
        elif normalized_level == "tertiary":
            tertiary_ids.append(normalized_id)
        else:
            raise ValueError(f"invalid_department_level:{normalized_level}")

        return {
            "primary": self._ordered_unique(primary_ids),
            "secondary": self._ordered_unique(secondary_ids),
            "tertiary": self._ordered_unique(tertiary_ids),
        }

    def _merge_department_scopes(self, scopes: list[dict[str, list[int]]]) -> dict[str, list[int]]:
        return {
            "primary": self._ordered_unique([item for scope in scopes for item in scope.get("primary", [])]),
            "secondary": self._ordered_unique([item for scope in scopes for item in scope.get("secondary", [])]),
            "tertiary": self._ordered_unique([item for scope in scopes for item in scope.get("tertiary", [])]),
        }

    def _execute_in_clause_update(
        self,
        cursor: Any,
        *,
        query_template: str,
        ids: list[int],
    ) -> int:
        if not ids:
            return 0
        placeholders = ", ".join(["%s"] * len(ids))
        cursor.execute(query_template.format(placeholders=placeholders), tuple(int(item) for item in ids))
        return int(cursor.rowcount or 0)

    def _clear_personnel_department_references_cursor(
        self,
        cursor: Any,
        *,
        primary_ids: list[int],
        secondary_ids: list[int],
        tertiary_ids: list[int],
    ) -> int:
        if not self.has_table("personnel_records"):
            return 0
        cleared = 0
        if primary_ids and self.has_personnel_column("primary_department_id"):
            cleared += self._execute_in_clause_update(
                cursor,
                query_template="""
                    UPDATE personnel_records
                    SET primary_department_id = NULL,
                        secondary_department_id = NULL,
                        tertiary_department_id = NULL
                    WHERE primary_department_id IN ({placeholders})
                """,
                ids=primary_ids,
            )
        if secondary_ids and self.has_personnel_column("secondary_department_id"):
            cleared += self._execute_in_clause_update(
                cursor,
                query_template="""
                    UPDATE personnel_records
                    SET secondary_department_id = NULL,
                        tertiary_department_id = NULL
                    WHERE secondary_department_id IN ({placeholders})
                """,
                ids=secondary_ids,
            )
        if tertiary_ids and self.has_personnel_column("tertiary_department_id"):
            cleared += self._execute_in_clause_update(
                cursor,
                query_template="""
                    UPDATE personnel_records
                    SET tertiary_department_id = NULL
                    WHERE tertiary_department_id IN ({placeholders})
                """,
                ids=tertiary_ids,
            )
        return cleared

    def _clear_user_department_references_cursor(
        self,
        cursor: Any,
        *,
        primary_ids: list[int],
        secondary_ids: list[int],
        tertiary_ids: list[int],
    ) -> int:
        if not self.has_table("users"):
            return 0
        cleared = 0
        if primary_ids and self.has_user_column("primary_department_id"):
            cleared += self._execute_in_clause_update(
                cursor,
                query_template="""
                    UPDATE users
                    SET primary_department_id = NULL,
                        secondary_department_id = NULL,
                        tertiary_department_id = NULL
                    WHERE primary_department_id IN ({placeholders})
                """,
                ids=primary_ids,
            )
        if secondary_ids and self.has_user_column("secondary_department_id"):
            cleared += self._execute_in_clause_update(
                cursor,
                query_template="""
                    UPDATE users
                    SET secondary_department_id = NULL,
                        tertiary_department_id = NULL
                    WHERE secondary_department_id IN ({placeholders})
                """,
                ids=secondary_ids,
            )
        if tertiary_ids and self.has_user_column("tertiary_department_id"):
            cleared += self._execute_in_clause_update(
                cursor,
                query_template="""
                    UPDATE users
                    SET tertiary_department_id = NULL
                    WHERE tertiary_department_id IN ({placeholders})
                """,
                ids=tertiary_ids,
            )
        return cleared

    def force_delete_department_subtree(self, *, level: str, department_id: int) -> dict[str, int]:
        return self.force_delete_departments(items=[{"level": level, "id": int(department_id)}])

    def force_delete_departments(self, *, items: list[dict[str, Any]]) -> dict[str, Any]:
        scopes = [
            self._department_subtree_ids(level=str(item.get("level") or ""), department_id=int(item.get("id") or 0))
            for item in list(items or [])
        ]
        scope = self._merge_department_scopes(scopes)
        primary_ids = scope["primary"]
        secondary_ids = scope["secondary"]
        tertiary_ids = scope["tertiary"]

        with self._db.connection() as conn:
            try:
                conn.begin()
                with conn.cursor() as cursor:
                    cleared_personnel = self._clear_personnel_department_references_cursor(
                        cursor,
                        primary_ids=primary_ids,
                        secondary_ids=secondary_ids,
                        tertiary_ids=tertiary_ids,
                    )
                    cleared_users = self._clear_user_department_references_cursor(
                        cursor,
                        primary_ids=primary_ids,
                        secondary_ids=secondary_ids,
                        tertiary_ids=tertiary_ids,
                    )
                    deleted_tertiary = self._execute_in_clause_update(
                        cursor,
                        query_template="DELETE FROM tertiary_departments WHERE id IN ({placeholders})",
                        ids=tertiary_ids,
                    )
                    deleted_secondary = self._execute_in_clause_update(
                        cursor,
                        query_template="DELETE FROM secondary_departments WHERE id IN ({placeholders})",
                        ids=secondary_ids,
                    )
                    deleted_primary = self._execute_in_clause_update(
                        cursor,
                        query_template="DELETE FROM primary_departments WHERE id IN ({placeholders})",
                        ids=primary_ids,
                    )
                conn.commit()
                return {
                    "deleted_primary": deleted_primary,
                    "deleted_secondary": deleted_secondary,
                    "deleted_tertiary": deleted_tertiary,
                    "cleared_personnel": cleared_personnel,
                    "cleared_users": cleared_users,
                    "details": [
                        {"level": "primary", "id": item, "status": "success", "message": "删除成功"}
                        for item in primary_ids
                    ]
                    + [
                        {"level": "secondary", "id": item, "status": "success", "message": "删除成功"}
                        for item in secondary_ids
                    ]
                    + [
                        {"level": "tertiary", "id": item, "status": "success", "message": "删除成功"}
                        for item in tertiary_ids
                    ],
                }
            except Exception:
                conn.rollback()
                raise

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
