from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.db import Database


class QuotaRepository:
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

    def get_quota_config(self, quota_type: str) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT
                quota_type,
                quota_name,
                period,
                period_days,
                default_limit,
                daily_limit,
                weekly_limit,
                monthly_limit,
                is_active
            FROM quota_configs
            WHERE quota_type = %s
            LIMIT 1
            """,
            (quota_type,),
        )
        return rows[0] if rows else None

    def get_user_override_limit(self, *, user_id: int, quota_type: str) -> int | None:
        rows = self._execute_query(
            """
            SELECT custom_limit
            FROM user_quota_overrides
            WHERE user_id = %s AND quota_type = %s
            LIMIT 1
            """,
            (user_id, quota_type),
        )
        if not rows:
            return None
        return int(rows[0].get("custom_limit"))

    def get_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        rows = self._execute_query(
            """
            SELECT used_count
            FROM user_quota_usage
            WHERE user_id = %s AND quota_type = %s AND period_key = %s
            LIMIT 1
            """,
            (user_id, quota_type, period_key),
        )
        if not rows:
            return 0
        return int(rows[0].get("used_count") or 0)

    def increment_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        self._execute_update(
            """
            INSERT INTO user_quota_usage (user_id, quota_type, period_key, used_count)
            VALUES (%s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE used_count = used_count + 1
            """,
            (user_id, quota_type, period_key),
        )
        return self.get_usage(user_id=user_id, quota_type=quota_type, period_key=period_key)

    def list_active_configs(self) -> list[dict[str, Any]]:
        return self._execute_query(
            """
            SELECT
                quota_type,
                quota_name,
                period,
                period_days,
                default_limit,
                daily_limit,
                weekly_limit,
                monthly_limit,
                is_active
            FROM quota_configs
            WHERE is_active = 1
            ORDER BY quota_type ASC
            """
        )

    def list_all_configs(self) -> list[dict[str, Any]]:
        return self._execute_query(
            """
            SELECT
                id,
                quota_type,
                quota_name,
                period,
                period_days,
                default_limit,
                daily_limit,
                weekly_limit,
                monthly_limit,
                is_active,
                created_at,
                updated_at
            FROM quota_configs
            ORDER BY id ASC
            """
        )

    def create_quota_config(
        self,
        *,
        quota_type: str,
        quota_name: str,
        period: str,
        period_days: int | None,
        default_limit: int,
        daily_limit: int | None,
        weekly_limit: int | None,
        monthly_limit: int | None,
        is_active: bool,
    ) -> int:
        return self._execute_update(
            """
            INSERT INTO quota_configs (
                quota_type,
                quota_name,
                period,
                period_days,
                default_limit,
                daily_limit,
                weekly_limit,
                monthly_limit,
                is_active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                quota_type,
                quota_name,
                period,
                period_days,
                int(default_limit),
                int(daily_limit) if daily_limit is not None else None,
                int(weekly_limit) if weekly_limit is not None else None,
                int(monthly_limit) if monthly_limit is not None else None,
                1 if bool(is_active) else 0,
            ),
        )

    def update_quota_config(
        self,
        *,
        quota_type: str,
        default_limit: int,
        daily_limit: int | None,
        weekly_limit: int | None,
        monthly_limit: int | None,
        is_active: bool,
        period: str,
        period_days: int | None,
    ) -> int:
        return self._execute_update(
            """
            UPDATE quota_configs
            SET
                default_limit = %s,
                daily_limit = %s,
                weekly_limit = %s,
                monthly_limit = %s,
                is_active = %s,
                period = %s,
                period_days = %s
            WHERE quota_type = %s
            """,
            (
                int(default_limit),
                int(daily_limit) if daily_limit is not None else None,
                int(weekly_limit) if weekly_limit is not None else None,
                int(monthly_limit) if monthly_limit is not None else None,
                1 if is_active else 0,
                period,
                period_days,
                quota_type,
            ),
        )

    def reset_user_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        return self._execute_update(
            """
            UPDATE user_quota_usage
            SET used_count = 0
            WHERE user_id = %s AND quota_type = %s AND period_key = %s
            """,
            (user_id, quota_type, period_key),
        )
