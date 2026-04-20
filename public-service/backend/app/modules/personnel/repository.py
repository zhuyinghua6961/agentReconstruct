from __future__ import annotations

from time import monotonic
from typing import Any

from app.core.config import get_settings
from app.core.db import Database


REMARKS_UNSET = object()


class PersonnelRepository:
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

    @staticmethod
    def _clean_text(value: object) -> str:
        return str(value or "").strip()

    def _personnel_filters(
        self,
        *,
        employee_no: str,
        full_name: str,
        status: str,
        keyword: str,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        employee_no_text = self._clean_text(employee_no)
        if employee_no_text:
            clauses.append("p.employee_no LIKE %s")
            params.append(f"%{employee_no_text}%")

        full_name_text = self._clean_text(full_name)
        if full_name_text:
            clauses.append("p.full_name LIKE %s")
            params.append(f"%{full_name_text}%")

        status_text = self._clean_text(status).lower()
        if status_text in {"active", "disabled"}:
            clauses.append("p.status = %s")
            params.append(status_text)

        keyword_text = self._clean_text(keyword)
        if keyword_text:
            clauses.append("(p.employee_no LIKE %s OR p.full_name LIKE %s)")
            params.extend((f"%{keyword_text}%", f"%{keyword_text}%"))

        if not clauses:
            return "", params
        return f"WHERE {' AND '.join(clauses)}", params

    def get_by_id(self, personnel_id: int) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT
                p.id,
                p.employee_no,
                p.full_name,
                p.verification_code_hash,
                p.status,
                p.remarks,
                p.created_at,
                p.updated_at,
                COALESCE(u.binding_count, 0) AS binding_count
            FROM personnel_records p
            LEFT JOIN (
                SELECT personnel_id, COUNT(*) AS binding_count
                FROM users
                WHERE personnel_id IS NOT NULL
                GROUP BY personnel_id
            ) u
                ON u.personnel_id = p.id
            WHERE p.id = %s
            LIMIT 1
            """,
            (int(personnel_id),),
        )
        return rows[0] if rows else None

    def get_by_employee_no(self, employee_no: str) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT
                p.id,
                p.employee_no,
                p.full_name,
                p.verification_code_hash,
                p.status,
                p.remarks,
                p.created_at,
                p.updated_at,
                COALESCE(u.binding_count, 0) AS binding_count
            FROM personnel_records p
            LEFT JOIN (
                SELECT personnel_id, COUNT(*) AS binding_count
                FROM users
                WHERE personnel_id IS NOT NULL
                GROUP BY personnel_id
            ) u
                ON u.personnel_id = p.id
            WHERE p.employee_no = %s
            LIMIT 1
            """,
            (self._clean_text(employee_no),),
        )
        return rows[0] if rows else None

    def count_personnel(
        self,
        *,
        employee_no: str = "",
        full_name: str = "",
        status: str = "",
        keyword: str = "",
    ) -> int:
        where_clause, params = self._personnel_filters(
            employee_no=employee_no,
            full_name=full_name,
            status=status,
            keyword=keyword,
        )
        rows = self._execute_query(
            f"""
            SELECT COUNT(*) AS total
            FROM personnel_records p
            {where_clause}
            """,
            tuple(params),
        )
        if not rows:
            return 0
        return int(rows[0].get("total") or 0)

    def list_personnel(
        self,
        *,
        employee_no: str = "",
        full_name: str = "",
        status: str = "",
        keyword: str = "",
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where_clause, params = self._personnel_filters(
            employee_no=employee_no,
            full_name=full_name,
            status=status,
            keyword=keyword,
        )
        return self._execute_query(
            f"""
            SELECT
                p.id,
                p.employee_no,
                p.full_name,
                p.status,
                p.remarks,
                p.created_at,
                p.updated_at,
                COALESCE(u.binding_count, 0) AS binding_count
            FROM personnel_records p
            LEFT JOIN (
                SELECT personnel_id, COUNT(*) AS binding_count
                FROM users
                WHERE personnel_id IS NOT NULL
                GROUP BY personnel_id
            ) u
                ON u.personnel_id = p.id
            {where_clause}
            ORDER BY p.id ASC
            LIMIT %s OFFSET %s
            """,
            tuple(params) + (int(limit), int(offset)),
        )

    def create_personnel(
        self,
        *,
        employee_no: str,
        full_name: str,
        verification_code_hash: str,
        status: str = "active",
        remarks: str | None = None,
    ) -> int:
        return self._execute_update(
            """
            INSERT INTO personnel_records (
                employee_no,
                full_name,
                verification_code_hash,
                status,
                remarks
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                self._clean_text(employee_no),
                self._clean_text(full_name),
                str(verification_code_hash or ""),
                self._clean_text(status).lower() or "active",
                self._clean_text(remarks) or None,
            ),
        )

    def update_personnel(
        self,
        *,
        personnel_id: int,
        full_name: str | None = None,
        verification_code_hash: str | None = None,
        remarks: object = REMARKS_UNSET,
        status: str | None = None,
    ) -> int:
        sets: list[str] = []
        params: list[Any] = []
        if full_name is not None:
            sets.append("full_name = %s")
            params.append(self._clean_text(full_name))
        if verification_code_hash is not None:
            sets.append("verification_code_hash = %s")
            params.append(str(verification_code_hash or ""))
        if remarks is not REMARKS_UNSET:
            sets.append("remarks = %s")
            params.append(self._clean_text(remarks) or None)
        if status is not None:
            sets.append("status = %s")
            params.append(self._clean_text(status).lower())
        if not sets:
            return 0
        params.append(int(personnel_id))
        return self._execute_update(
            f"""
            UPDATE personnel_records
            SET {", ".join(sets)}
            WHERE id = %s
            """,
            tuple(params),
        )

    def update_personnel_status(self, *, personnel_id: int, status: str) -> int:
        return self._execute_update(
            """
            UPDATE personnel_records
            SET status = %s
            WHERE id = %s
            """,
            (self._clean_text(status).lower(), int(personnel_id)),
        )

    def list_bindings(self, *, personnel_id: int) -> list[dict[str, Any]]:
        fields = ["id", "username", "role", "status", "personnel_id"]
        if self.has_user_column("user_type"):
            fields.append("user_type")
        return self._execute_query(
            f"""
            SELECT {", ".join(fields)}
            FROM users
            WHERE personnel_id = %s
            ORDER BY id ASC
            """,
            (int(personnel_id),),
        )

    def import_personnel_rows(self, *, rows: list[dict[str, Any]]) -> dict[str, Any]:
        created = 0
        updated = 0
        details: list[dict[str, Any]] = []

        with self._db.connection() as conn:
            try:
                conn.begin()
                with conn.cursor() as cursor:
                    for row in rows:
                        line_no = int(row["line_no"])
                        employee_no = self._clean_text(row.get("employee_no"))
                        full_name = self._clean_text(row.get("full_name"))
                        verification_code_hash = str(row.get("verification_code_hash") or "")
                        status = self._clean_text(row.get("status")).lower()
                        raw_remarks = row.get("remarks", REMARKS_UNSET)
                        remarks = REMARKS_UNSET if raw_remarks is REMARKS_UNSET else (self._clean_text(raw_remarks) or None)

                        cursor.execute(
                            """
                            SELECT id
                            FROM personnel_records
                            WHERE employee_no = %s
                            LIMIT 1
                            FOR UPDATE
                            """,
                            (employee_no,),
                        )
                        existing = cursor.fetchone() or None
                        if existing:
                            update_sets = [
                                "full_name = %s",
                                "verification_code_hash = %s",
                                "status = %s",
                            ]
                            update_params: list[Any] = [
                                full_name,
                                verification_code_hash,
                                status,
                            ]
                            if remarks is not REMARKS_UNSET:
                                update_sets.insert(2, "remarks = %s")
                                update_params.insert(2, remarks)
                            update_params.append(int(existing["id"]))
                            cursor.execute(
                                f"""
                                UPDATE personnel_records
                                SET {", ".join(update_sets)}
                                WHERE id = %s
                                """,
                                tuple(update_params),
                            )
                            updated += 1
                            details.append(
                                {
                                    "row": line_no,
                                    "employee_no": employee_no,
                                    "full_name": full_name,
                                    "personnel_record_status": status,
                                    "status": "updated",
                                }
                            )
                            continue

                        cursor.execute(
                            """
                            INSERT INTO personnel_records (
                                employee_no,
                                full_name,
                                verification_code_hash,
                                status,
                                remarks
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                employee_no,
                                full_name,
                                verification_code_hash,
                                status,
                                None if remarks is REMARKS_UNSET else remarks,
                            ),
                        )
                        created += 1
                        details.append(
                            {
                                "row": line_no,
                                "employee_no": employee_no,
                                "full_name": full_name,
                                "personnel_record_status": status,
                                "status": "created",
                            }
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return {
            "created": created,
            "updated": updated,
            "details": details,
        }
