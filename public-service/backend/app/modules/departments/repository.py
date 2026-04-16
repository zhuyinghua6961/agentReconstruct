from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.db import Database


class DepartmentRepository:
    def __init__(self, *, database: Database | None = None) -> None:
        self._db = database or Database(settings=get_settings())

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

    def list_department_tree(self, *, include_disabled: bool) -> list[dict[str, Any]]:
        where_clause = ""
        if not include_disabled:
            where_clause = """
            WHERE p.status = 'active'
              AND (s.id IS NULL OR s.status = 'active')
            """

        rows = self._execute_query(
            f"""
            SELECT
                p.id AS primary_id,
                p.name AS primary_name,
                p.status AS primary_status,
                s.id AS secondary_id,
                s.name AS secondary_name,
                s.status AS secondary_status
            FROM primary_departments p
            LEFT JOIN secondary_departments s
                ON s.primary_department_id = p.id
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
