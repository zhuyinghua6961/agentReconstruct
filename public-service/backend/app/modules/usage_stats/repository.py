from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from app.core.config import get_settings
from app.core.db import Database
from app.core.timezone import ensure_beijing_datetime, now_beijing
from app.modules.usage_stats.helpers import (
    EVENT_DAILY_COUNT_COLUMNS,
    EXPORT_USAGE_STATS_ROW_LIMIT,
    normalize_event_type,
    normalize_usage_stats_sort_by,
    normalize_usage_stats_sort_order,
)


class UsageStatsRepository:
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

    def insert_activity_event(
        self,
        *,
        user_id: int,
        event_type: str,
        occurred_at: datetime,
        trace_id: str | None = None,
        conversation_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False) if metadata is not None else None
        return self._execute_update(
            """
            INSERT INTO user_activity_events (
                user_id, event_type, occurred_at, trace_id, conversation_id, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                int(user_id),
                str(event_type),
                ensure_beijing_datetime(occurred_at).replace(tzinfo=None),
                str(trace_id or "").strip() or None,
                int(conversation_id) if conversation_id else None,
                metadata_json,
            ),
        )

    def insert_online_session(
        self,
        *,
        user_id: int,
        session_id: str,
        started_at: datetime,
        ended_at: datetime,
        active_seconds: int,
    ) -> int:
        return self._execute_update(
            """
            INSERT INTO user_online_sessions (user_id, session_id, started_at, ended_at, active_seconds)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                int(user_id),
                str(session_id or "").strip()[:64],
                ensure_beijing_datetime(started_at).replace(tzinfo=None),
                ensure_beijing_datetime(ended_at).replace(tzinfo=None),
                max(0, int(active_seconds)),
            ),
        )

    def increment_daily_event_count(
        self,
        *,
        user_id: int,
        event_type: str,
        occurred_at: datetime,
        increment: int = 1,
    ) -> None:
        normalized = normalize_event_type(event_type)
        if normalized is None:
            return
        column = EVENT_DAILY_COUNT_COLUMNS[normalized]
        stat_date = ensure_beijing_datetime(occurred_at).date()
        occurred_naive = ensure_beijing_datetime(occurred_at).replace(tzinfo=None)
        self._execute_update(
            f"""
            INSERT INTO user_daily_stats (
                user_id, stat_date, {column}, last_active_at
            ) VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                {column} = {column} + VALUES({column}),
                last_active_at = GREATEST(COALESCE(last_active_at, VALUES(last_active_at)), VALUES(last_active_at))
            """,
            (int(user_id), stat_date, max(0, int(increment)), occurred_naive),
        )

    def add_daily_active_seconds(
        self,
        *,
        user_id: int,
        occurred_at: datetime,
        active_seconds: int,
    ) -> None:
        if int(active_seconds) <= 0:
            return
        stat_date = ensure_beijing_datetime(occurred_at).date()
        occurred_naive = ensure_beijing_datetime(occurred_at).replace(tzinfo=None)
        self._execute_update(
            """
            INSERT INTO user_daily_stats (user_id, stat_date, active_seconds, last_active_at)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                active_seconds = active_seconds + VALUES(active_seconds),
                last_active_at = GREATEST(COALESCE(last_active_at, VALUES(last_active_at)), VALUES(last_active_at))
            """,
            (int(user_id), stat_date, max(0, int(active_seconds)), occurred_naive),
        )

    @staticmethod
    def _sort_sql(sort_by: str | None, sort_order: str | None) -> str:
        normalized_sort_by = normalize_usage_stats_sort_by(sort_by)
        normalized_sort_order = normalize_usage_stats_sort_order(sort_order)
        direction = "ASC" if normalized_sort_order == "asc" else "DESC"
        sort_exprs = {
            "ask_query_count": "COALESCE(s.ask_query_count, 0)",
            "file_qa_count": "COALESCE(s.file_qa_count, 0)",
            "ask_total": "(COALESCE(s.ask_query_count, 0) + COALESCE(s.file_qa_count, 0))",
            "literature_search_count": "COALESCE(s.literature_search_count, 0)",
            "patent_search_count": "COALESCE(s.patent_search_count, 0)",
            "active_seconds": "COALESCE(s.active_seconds, 0)",
            "last_active_at": "s.last_active_at",
            "username": "u.username",
        }
        primary = sort_exprs[normalized_sort_by]
        if normalized_sort_by == "last_active_at":
            # IS NULL is 1 for missing values; ASC keeps real timestamps before NULLs.
            return f"ORDER BY ({primary} IS NULL) ASC, {primary} {direction}, u.id ASC"
        return f"ORDER BY {primary} {direction}, u.id ASC"

    def _build_user_filters(
        self,
        *,
        keyword: str | None = None,
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
    ) -> tuple[str, list[Any]]:
        conditions = ["1=1"]
        where_params: list[Any] = []
        if keyword:
            like = f"%{keyword.strip()}%"
            conditions.append(
                "(u.username LIKE %s OR p.full_name LIKE %s OR p.employee_no LIKE %s)"
            )
            where_params.extend([like, like, like])
        if primary_department_id is not None:
            conditions.append("u.primary_department_id = %s")
            where_params.append(int(primary_department_id))
        if secondary_department_id is not None:
            conditions.append("u.secondary_department_id = %s")
            where_params.append(int(secondary_department_id))
        if tertiary_department_id is not None:
            conditions.append("u.tertiary_department_id = %s")
            where_params.append(int(tertiary_department_id))
        return " AND ".join(conditions), where_params

    def list_users_with_stats(
        self,
        *,
        stat_from: date,
        stat_to: date,
        offset: int,
        limit: int,
        keyword: str | None = None,
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where_sql, where_params = self._build_user_filters(
            keyword=keyword,
            primary_department_id=primary_department_id,
            secondary_department_id=secondary_department_id,
            tertiary_department_id=tertiary_department_id,
        )

        count_rows = self._execute_query(
            f"""
            SELECT COUNT(*) AS total
            FROM users AS u
            LEFT JOIN personnel_records AS p ON p.id = u.personnel_id
            WHERE {where_sql}
            """,
            tuple(where_params),
        )
        total = int(count_rows[0].get("total") or 0) if count_rows else 0

        rows = self._execute_query(
            f"""
            SELECT
                u.id,
                u.username,
                u.role,
                u.status,
                u.user_type,
                u.personnel_id,
                u.primary_department_id,
                u.secondary_department_id,
                u.tertiary_department_id,
                u.created_at,
                u.updated_at,
                COALESCE(s.ask_query_count, 0) AS ask_query_count,
                COALESCE(s.file_qa_count, 0) AS file_qa_count,
                COALESCE(s.literature_search_count, 0) AS literature_search_count,
                COALESCE(s.patent_search_count, 0) AS patent_search_count,
                COALESCE(s.active_seconds, 0) AS active_seconds,
                s.last_active_at
            FROM users AS u
            LEFT JOIN personnel_records AS p ON p.id = u.personnel_id
            LEFT JOIN (
                SELECT
                    user_id,
                    SUM(ask_query_count) AS ask_query_count,
                    SUM(file_qa_count) AS file_qa_count,
                    SUM(literature_search_count) AS literature_search_count,
                    SUM(patent_search_count) AS patent_search_count,
                    SUM(active_seconds) AS active_seconds,
                    MAX(last_active_at) AS last_active_at
                FROM user_daily_stats
                WHERE stat_date BETWEEN %s AND %s
                GROUP BY user_id
            ) AS s ON s.user_id = u.id
            WHERE {where_sql}
            {self._sort_sql(sort_by, sort_order)}
            LIMIT %s OFFSET %s
            """,
            tuple([stat_from, stat_to, *where_params, int(limit), int(offset)]),
        )
        return rows, total

    def list_users_with_stats_for_export(
        self,
        *,
        stat_from: date,
        stat_to: date,
        keyword: str | None = None,
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int = EXPORT_USAGE_STATS_ROW_LIMIT,
    ) -> list[dict[str, Any]]:
        where_sql, where_params = self._build_user_filters(
            keyword=keyword,
            primary_department_id=primary_department_id,
            secondary_department_id=secondary_department_id,
            tertiary_department_id=tertiary_department_id,
        )
        return self._execute_query(
            f"""
            SELECT
                u.id,
                u.username,
                u.role,
                u.status,
                u.user_type,
                u.personnel_id,
                u.primary_department_id,
                u.secondary_department_id,
                u.tertiary_department_id,
                u.created_at,
                u.updated_at,
                COALESCE(s.ask_query_count, 0) AS ask_query_count,
                COALESCE(s.file_qa_count, 0) AS file_qa_count,
                COALESCE(s.literature_search_count, 0) AS literature_search_count,
                COALESCE(s.patent_search_count, 0) AS patent_search_count,
                COALESCE(s.active_seconds, 0) AS active_seconds,
                s.last_active_at
            FROM users AS u
            LEFT JOIN personnel_records AS p ON p.id = u.personnel_id
            LEFT JOIN (
                SELECT
                    user_id,
                    SUM(ask_query_count) AS ask_query_count,
                    SUM(file_qa_count) AS file_qa_count,
                    SUM(literature_search_count) AS literature_search_count,
                    SUM(patent_search_count) AS patent_search_count,
                    SUM(active_seconds) AS active_seconds,
                    MAX(last_active_at) AS last_active_at
                FROM user_daily_stats
                WHERE stat_date BETWEEN %s AND %s
                GROUP BY user_id
            ) AS s ON s.user_id = u.id
            WHERE {where_sql}
            {self._sort_sql(sort_by, sort_order)}
            LIMIT %s
            """,
            tuple([stat_from, stat_to, *where_params, int(limit)]),
        )
