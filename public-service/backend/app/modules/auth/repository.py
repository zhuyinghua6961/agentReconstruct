from __future__ import annotations

from time import monotonic
from typing import Any

from app.core.config import get_settings
from app.core.db import Database


class AuthRepository:
    def __init__(self, *, database: Database | None = None) -> None:
        self._db = database or Database(settings=get_settings())
        self._columns_cache: set[str] | None = None
        self._tables_cache: set[str] | None = None
        self._columns_cache_loaded_at = 0.0
        self._tables_cache_loaded_at = 0.0
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

    def _load_columns(self) -> set[str]:
        rows = self._execute_query("SHOW COLUMNS FROM users")
        return {str(row.get("Field") or "") for row in rows}

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

    def _columns(self) -> set[str]:
        if self._cache_valid(self._columns_cache_loaded_at) and self._columns_cache is not None:
            return self._columns_cache
        self._columns_cache = self._load_columns()
        self._columns_cache_loaded_at = self._now()
        return self._columns_cache

    def _tables(self) -> set[str]:
        if self._cache_valid(self._tables_cache_loaded_at) and self._tables_cache is not None:
            return self._tables_cache
        self._tables_cache = self._load_tables()
        self._tables_cache_loaded_at = self._now()
        return self._tables_cache

    def has_column(self, column_name: str) -> bool:
        return column_name in self._columns()

    def has_table(self, table_name: str) -> bool:
        return table_name in self._tables()

    def has_user_type_column(self) -> bool:
        return self.has_column("user_type")

    def _select_user_fields(self, *, include_password: bool) -> str:
        fields = ["id", "username"]
        if include_password:
            fields.append("password_hash")
        fields.extend(["role", "status"])
        if self.has_column("user_type"):
            fields.append("user_type")
        if self.has_column("is_first_login"):
            fields.append("is_first_login")
        if self.has_column("must_set_security_questions"):
            fields.append("must_set_security_questions")
        if self.has_column("personnel_id"):
            fields.append("personnel_id")
        if self.has_column("primary_department_id"):
            fields.append("primary_department_id")
        if self.has_column("secondary_department_id"):
            fields.append("secondary_department_id")
        if self.has_column("tertiary_department_id"):
            fields.append("tertiary_department_id")
        if self.has_column("password_updated_at"):
            fields.append("password_updated_at")
        if self.has_column("failed_login_attempts"):
            fields.append("failed_login_attempts")
        if self.has_column("locked_until"):
            fields.append("locked_until")
        fields.extend(["created_at", "updated_at"])
        return ", ".join(fields)

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        fields = self._select_user_fields(include_password=True)
        rows = self._execute_query(
            f"""
            SELECT {fields}
            FROM users
            WHERE username = %s
            LIMIT 1
            """,
            (username,),
        )
        return rows[0] if rows else None

    def get_by_id(self, user_id: int) -> dict[str, Any] | None:
        fields = self._select_user_fields(include_password=True)
        rows = self._execute_query(
            f"""
            SELECT {fields}
            FROM users
            WHERE id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        return rows[0] if rows else None

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        user_type: int | None = None,
        is_first_login: bool | None = None,
        must_set_security_questions: bool | None = None,
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
    ) -> int:
        columns = ["username", "password_hash", "role", "status"]
        values: list[Any] = [username, password_hash, role, "active"]
        if user_type is not None and self.has_column("user_type"):
            columns.append("user_type")
            values.append(int(user_type))
        if is_first_login is not None and self.has_column("is_first_login"):
            columns.append("is_first_login")
            values.append(1 if is_first_login else 0)
        if must_set_security_questions is not None and self.has_column("must_set_security_questions"):
            columns.append("must_set_security_questions")
            values.append(1 if must_set_security_questions else 0)
        if self.has_column("primary_department_id"):
            columns.append("primary_department_id")
            values.append(primary_department_id)
        if self.has_column("secondary_department_id"):
            columns.append("secondary_department_id")
            values.append(secondary_department_id)
        if self.has_column("tertiary_department_id"):
            columns.append("tertiary_department_id")
            values.append(tertiary_department_id)
        if self.has_column("password_updated_at"):
            columns.append("password_updated_at")
            values.append("NOW_FUNC_PLACEHOLDER")

        placeholders: list[str] = []
        params: list[Any] = []
        for value in values:
            if value == "NOW_FUNC_PLACEHOLDER":
                placeholders.append("NOW()")
            else:
                placeholders.append("%s")
                params.append(value)

        sql = f"""
            INSERT INTO users ({", ".join(columns)})
            VALUES ({", ".join(placeholders)})
        """
        return self._execute_update(sql, tuple(params))

    def create_registered_user(
        self,
        *,
        username: str,
        password_hash: str,
        primary_department_id: int,
        secondary_department_id: int | None,
        tertiary_department_id: int | None,
        personnel_id: int,
        security_question_items: list[dict[str, Any]],
        user_type: int = 2,
    ) -> int:
        required_columns = [
            "user_type",
            "is_first_login",
            "must_set_security_questions",
            "personnel_id",
            "primary_department_id",
            "secondary_department_id",
            "tertiary_department_id",
        ]
        missing_columns = [name for name in required_columns if not self.has_column(name)]
        required_tables = ["password_history", "user_security_questions"]
        missing_tables = [name for name in required_tables if not self.has_table(name)]
        if missing_columns or missing_tables:
            missing_parts = []
            if missing_columns:
                missing_parts.append(f"columns={','.join(missing_columns)}")
            if missing_tables:
                missing_parts.append(f"tables={','.join(missing_tables)}")
            raise RuntimeError(f"registration_schema_incomplete:{';'.join(missing_parts)}")

        columns = ["username", "password_hash", "role", "status"]
        values: list[Any] = [username, password_hash, "user", "active"]

        columns.append("user_type")
        values.append(int(user_type))
        columns.append("is_first_login")
        values.append(0)
        columns.append("must_set_security_questions")
        values.append(0)
        columns.append("personnel_id")
        values.append(int(personnel_id))
        columns.append("primary_department_id")
        values.append(int(primary_department_id))
        columns.append("secondary_department_id")
        values.append(int(secondary_department_id) if secondary_department_id is not None else None)
        columns.append("tertiary_department_id")
        values.append(int(tertiary_department_id) if tertiary_department_id is not None else None)
        if self.has_column("password_updated_at"):
            columns.append("password_updated_at")
            values.append("NOW_FUNC_PLACEHOLDER")

        placeholders: list[str] = []
        params: list[Any] = []
        for value in values:
            if value == "NOW_FUNC_PLACEHOLDER":
                placeholders.append("NOW()")
            else:
                placeholders.append("%s")
                params.append(value)

        insert_user_sql = f"""
            INSERT INTO users ({", ".join(columns)})
            VALUES ({", ".join(placeholders)})
        """

        with self._db.connection() as conn:
            conn.begin()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(insert_user_sql, tuple(params))
                    user_id = int(cursor.lastrowid or 0)
                    if user_id <= 0:
                        raise RuntimeError("create_registered_user_failed")

                    cursor.execute(
                        """
                        INSERT INTO password_history (user_id, password_hash)
                        VALUES (%s, %s)
                        """,
                        (user_id, password_hash),
                    )

                    for item in security_question_items:
                        cursor.execute(
                            """
                            INSERT INTO user_security_questions (user_id, question, answer_hash, sort_order)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (
                                user_id,
                                str(item.get("question") or ""),
                                str(item.get("answer_hash") or ""),
                                int(item.get("sort_order") or 0),
                            ),
                        )
                conn.commit()
                return user_id
            except Exception:
                conn.rollback()
                raise

    def update_password_hash(self, *, user_id: int, password_hash: str) -> int:
        if self.has_column("password_updated_at"):
            return self._execute_update(
                """
                UPDATE users
                SET password_hash = %s, password_updated_at = NOW()
                WHERE id = %s
                """,
                (password_hash, user_id),
            )
        return self._execute_update(
            """
            UPDATE users
            SET password_hash = %s
            WHERE id = %s
            """,
            (password_hash, user_id),
        )

    def update_user_department(
        self,
        *,
        user_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> int:
        if not self.has_column("primary_department_id") or not self.has_column("secondary_department_id"):
            return 0
        if self.has_column("tertiary_department_id"):
            return self._execute_update(
                """
                UPDATE users
                SET primary_department_id = %s,
                    secondary_department_id = %s,
                    tertiary_department_id = %s
                WHERE id = %s
                """,
                (primary_department_id, secondary_department_id, tertiary_department_id, user_id),
            )
        return self._execute_update(
            """
            UPDATE users
            SET primary_department_id = %s,
                secondary_department_id = %s
            WHERE id = %s
            """,
            (primary_department_id, secondary_department_id, user_id),
        )

    def sync_departments_for_personnel(
        self,
        *,
        personnel_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> int:
        if (
            not self.has_column("personnel_id")
            or not self.has_column("primary_department_id")
            or not self.has_column("secondary_department_id")
        ):
            return 0
        if self.has_column("tertiary_department_id"):
            return self._execute_update(
                """
                UPDATE users
                SET primary_department_id = %s,
                    secondary_department_id = %s,
                    tertiary_department_id = %s
                WHERE personnel_id = %s
                """,
                (
                    primary_department_id,
                    secondary_department_id,
                    tertiary_department_id,
                    int(personnel_id),
                ),
            )
        return self._execute_update(
            """
            UPDATE users
            SET primary_department_id = %s,
                secondary_department_id = %s
            WHERE personnel_id = %s
            """,
            (
                primary_department_id,
                secondary_department_id,
                int(personnel_id),
            ),
        )

    def clear_user_department_cache(self, *, user_id: int) -> int:
        if not self.has_column("primary_department_id") or not self.has_column("secondary_department_id"):
            return 0
        if self.has_column("tertiary_department_id"):
            return self._execute_update(
                """
                UPDATE users
                SET primary_department_id = %s,
                    secondary_department_id = %s,
                    tertiary_department_id = %s
                WHERE id = %s
                """,
                (None, None, None, int(user_id)),
            )
        return self._execute_update(
            """
            UPDATE users
            SET primary_department_id = %s,
                secondary_department_id = %s
            WHERE id = %s
            """,
            (None, None, int(user_id)),
        )

    def bind_user_personnel_with_departments(
        self,
        *,
        user_id: int,
        personnel_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> int:
        if not self.has_column("personnel_id"):
            return 0
        has_department_cache = self.has_column("primary_department_id") and self.has_column("secondary_department_id")
        has_tertiary_department = self.has_column("tertiary_department_id")

        with self._db.connection() as conn:
            try:
                conn.begin()
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE users
                        SET personnel_id = %s
                        WHERE id = %s
                        """,
                        (int(personnel_id), int(user_id)),
                    )
                    updated_count = int(cursor.rowcount or 0)
                    if has_department_cache:
                        if has_tertiary_department:
                            cursor.execute(
                                """
                                UPDATE users
                                SET primary_department_id = %s,
                                    secondary_department_id = %s,
                                    tertiary_department_id = %s
                                WHERE personnel_id = %s
                                """,
                                (
                                    primary_department_id,
                                    secondary_department_id,
                                    tertiary_department_id,
                                    int(personnel_id),
                                ),
                            )
                        else:
                            cursor.execute(
                                """
                                UPDATE users
                                SET primary_department_id = %s,
                                    secondary_department_id = %s
                                WHERE personnel_id = %s
                                """,
                                (
                                    primary_department_id,
                                    secondary_department_id,
                                    int(personnel_id),
                                ),
                            )
                conn.commit()
                return updated_count
            except Exception:
                conn.rollback()
                raise

    def clear_user_personnel_with_department_cache(self, *, user_id: int) -> int:
        if not self.has_column("personnel_id"):
            return 0
        has_department_cache = self.has_column("primary_department_id") and self.has_column("secondary_department_id")
        has_tertiary_department = self.has_column("tertiary_department_id")

        with self._db.connection() as conn:
            try:
                conn.begin()
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE users
                        SET personnel_id = %s
                        WHERE id = %s
                        """,
                        (None, int(user_id)),
                    )
                    updated_count = int(cursor.rowcount or 0)
                    if has_department_cache:
                        if has_tertiary_department:
                            cursor.execute(
                                """
                                UPDATE users
                                SET primary_department_id = %s,
                                    secondary_department_id = %s,
                                    tertiary_department_id = %s
                                WHERE id = %s
                                """,
                                (None, None, None, int(user_id)),
                            )
                        else:
                            cursor.execute(
                                """
                                UPDATE users
                                SET primary_department_id = %s,
                                    secondary_department_id = %s
                                WHERE id = %s
                                """,
                                (None, None, int(user_id)),
                            )
                conn.commit()
                return updated_count
            except Exception:
                conn.rollback()
                raise

    def update_username(self, *, user_id: int, username: str) -> int:
        return self._execute_update(
            """
            UPDATE users
            SET username = %s
            WHERE id = %s
            """,
            (username, user_id),
        )

    def update_user_personnel(self, *, user_id: int, personnel_id: int | None) -> int:
        if not self.has_column("personnel_id"):
            return 0
        return self._execute_update(
            """
            UPDATE users
            SET personnel_id = %s
            WHERE id = %s
            """,
            (personnel_id, user_id),
        )

    def count_users(self) -> int:
        rows = self._execute_query("SELECT COUNT(*) AS total FROM users")
        if not rows:
            return 0
        return int(rows[0].get("total", 0) or 0)

    def list_users(self, *, offset: int, limit: int) -> list[dict[str, Any]]:
        fields = ["id", "username", "role", "status"]
        if self.has_column("user_type"):
            fields.append("user_type")
        if self.has_column("personnel_id"):
            fields.append("personnel_id")
        if self.has_column("primary_department_id"):
            fields.append("primary_department_id")
        if self.has_column("secondary_department_id"):
            fields.append("secondary_department_id")
        if self.has_column("tertiary_department_id"):
            fields.append("tertiary_department_id")
        fields.extend(["created_at", "updated_at"])
        return self._execute_query(
            f"""
            SELECT {", ".join(fields)}
            FROM users
            ORDER BY id ASC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )

    def update_status(self, *, user_id: int, status: str) -> int:
        return self._execute_update(
            """
            UPDATE users
            SET status = %s
            WHERE id = %s
            """,
            (status, user_id),
        )

    def update_user_type(self, *, user_id: int, user_type: int) -> int:
        if not self.has_column("user_type"):
            return 0
        return self._execute_update(
            """
            UPDATE users
            SET user_type = %s
            WHERE id = %s
            """,
            (int(user_type), user_id),
        )

    def delete_user(self, *, user_id: int) -> int:
        return self._execute_update(
            """
            DELETE FROM users
            WHERE id = %s
            """,
            (user_id,),
        )

    def reset_login_attempts(self, *, user_id: int) -> int:
        if not self.has_column("failed_login_attempts"):
            return 0
        if self.has_column("locked_until"):
            return self._execute_update(
                """
                UPDATE users
                SET failed_login_attempts = 0, locked_until = NULL
                WHERE id = %s
                """,
                (user_id,),
            )
        return self._execute_update(
            """
            UPDATE users
            SET failed_login_attempts = 0
            WHERE id = %s
            """,
            (user_id,),
        )

    def increment_login_attempts(self, *, user_id: int, lock_threshold: int, lock_minutes: int) -> dict[str, Any]:
        user = self.get_by_id(user_id)
        current = int((user or {}).get("failed_login_attempts") or 0)
        next_count = current + 1
        if self.has_column("locked_until") and next_count >= lock_threshold:
            self._execute_update(
                """
                UPDATE users
                SET failed_login_attempts = %s,
                    locked_until = DATE_ADD(NOW(), INTERVAL %s MINUTE)
                WHERE id = %s
                """,
                (next_count, int(lock_minutes), user_id),
            )
        else:
            self._execute_update(
                """
                UPDATE users
                SET failed_login_attempts = %s
                WHERE id = %s
                """,
                (next_count, user_id),
            )
        latest = self.get_by_id(user_id) or {}
        return {
            "failed_login_attempts": int(latest.get("failed_login_attempts") or next_count),
            "locked_until": latest.get("locked_until"),
        }

    def mark_first_login_completed(self, *, user_id: int) -> int:
        if not self.has_column("is_first_login"):
            return 0
        return self._execute_update(
            """
            UPDATE users
            SET is_first_login = 0
            WHERE id = %s
            """,
            (user_id,),
        )

    def set_security_setup_required(self, *, user_id: int, required: bool) -> int:
        if not self.has_column("must_set_security_questions"):
            return 0
        return self._execute_update(
            """
            UPDATE users
            SET must_set_security_questions = %s
            WHERE id = %s
            """,
            (1 if required else 0, user_id),
        )

    def mark_first_login_required(self, *, user_id: int) -> int:
        if not self.has_column("is_first_login"):
            return 0
        return self._execute_update(
            """
            UPDATE users
            SET is_first_login = 1
            WHERE id = %s
            """,
            (user_id,),
        )

    def list_recent_password_hashes(self, *, user_id: int, limit: int) -> list[str]:
        if not self.has_table("password_history"):
            return []
        rows = self._execute_query(
            """
            SELECT password_hash
            FROM password_history
            WHERE user_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (user_id, int(limit)),
        )
        return [str(row.get("password_hash") or "") for row in rows]

    def add_password_history(self, *, user_id: int, password_hash: str) -> int:
        if not self.has_table("password_history"):
            return 0
        return self._execute_update(
            """
            INSERT INTO password_history (user_id, password_hash)
            VALUES (%s, %s)
            """,
            (user_id, password_hash),
        )

    def trim_password_history(self, *, user_id: int, keep_limit: int) -> int:
        if not self.has_table("password_history"):
            return 0
        return self._execute_update(
            """
            DELETE FROM password_history
            WHERE user_id = %s
              AND id NOT IN (
                  SELECT id
                  FROM (
                      SELECT id
                      FROM password_history
                      WHERE user_id = %s
                      ORDER BY created_at DESC, id DESC
                      LIMIT %s
                  ) AS keep_rows
              )
            """,
            (user_id, user_id, int(keep_limit)),
        )

    def list_security_questions(self, *, user_id: int) -> list[dict[str, Any]]:
        if not self.has_table("user_security_questions"):
            return []
        return self._execute_query(
            """
            SELECT question, answer_hash, sort_order
            FROM user_security_questions
            WHERE user_id = %s
            ORDER BY sort_order ASC, id ASC
            """,
            (user_id,),
        )

    def has_security_questions(self, *, user_id: int) -> bool:
        if not self.has_table("user_security_questions"):
            return False
        rows = self._execute_query(
            """
            SELECT COUNT(*) AS total
            FROM user_security_questions
            WHERE user_id = %s
            """,
            (user_id,),
        )
        if not rows:
            return False
        return int(rows[0].get("total") or 0) > 0

    def replace_security_questions(self, *, user_id: int, items: list[dict[str, Any]]) -> int:
        if not self.has_table("user_security_questions"):
            return 0
        self._execute_update("DELETE FROM user_security_questions WHERE user_id = %s", (user_id,))
        affected = 0
        for item in items:
            affected += int(
                self._execute_update(
                    """
                    INSERT INTO user_security_questions (user_id, question, answer_hash, sort_order)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        str(item.get("question") or ""),
                        str(item.get("answer_hash") or ""),
                        int(item.get("sort_order") or 0),
                    ),
                )
                or 0
            )
        return affected
