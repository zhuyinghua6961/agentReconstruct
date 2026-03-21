from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from app.core.config import get_settings
from app.core.db import Database


logger = logging.getLogger(__name__)


class ConversationOutboxRepository:
    def __init__(self, *, database: Database | None = None) -> None:
        self._db = database or Database(settings=get_settings())
        self._table_exists_cache: bool | None = None
        self._missing_table_warned = False
        self._table_name = "conversation_json_outbox"

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

    def _table_exists(self) -> bool:
        if self._table_exists_cache is not None:
            return self._table_exists_cache
        rows = self._execute_query(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
            """,
            (self._table_name,),
        )
        total = int((rows[0] or {}).get("total", 0)) if rows else 0
        self._table_exists_cache = total > 0
        if not self._table_exists_cache and not self._missing_table_warned:
            logger.warning("conversation outbox disabled: table %s missing", self._table_name)
            self._missing_table_warned = True
        return self._table_exists_cache

    def support_status(self) -> dict[str, Any]:
        exists = self._table_exists()
        return {
            "table_name": self._table_name,
            "table_exists": exists,
            "enabled": exists,
            "reason": "" if exists else "missing_table",
        }

    def enqueue_task(
        self,
        *,
        conversation_id: int,
        user_id: int,
        json_version: int,
        local_path: str,
        object_name: str,
        content_hash: str | None,
        last_error: str | None,
    ) -> int:
        if not self._table_exists():
            return 0
        return self._execute_update(
            """
            INSERT INTO conversation_json_outbox (
                conversation_id,
                user_id,
                json_version,
                local_path,
                object_name,
                content_hash,
                status,
                attempt_count,
                next_retry_at,
                processing_started_at,
                last_error
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, NULL, NULL, %s)
            ON DUPLICATE KEY UPDATE
                user_id = VALUES(user_id),
                local_path = VALUES(local_path),
                object_name = VALUES(object_name),
                content_hash = VALUES(content_hash),
                status = 'pending',
                next_retry_at = NULL,
                processing_started_at = NULL,
                last_error = VALUES(last_error),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(conversation_id),
                int(user_id),
                int(json_version),
                local_path,
                object_name,
                content_hash,
                last_error,
            ),
        )

    def reclaim_stuck_processing(self, *, timeout_seconds: int) -> int:
        if not self._table_exists():
            return 0
        cutoff = datetime.now() - timedelta(seconds=max(1, int(timeout_seconds)))
        return self._execute_update(
            """
            UPDATE conversation_json_outbox
            SET
                status = 'failed',
                processing_started_at = NULL,
                next_retry_at = CURRENT_TIMESTAMP,
                last_error = 'processing_timeout',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'processing'
              AND processing_started_at IS NOT NULL
              AND processing_started_at < %s
            """,
            (cutoff,),
        )

    def claim_due_tasks(self, *, limit: int) -> list[dict[str, Any]]:
        if not self._table_exists():
            return []
        rows = self._execute_query(
            """
            SELECT
                id,
                conversation_id,
                user_id,
                json_version,
                local_path,
                object_name,
                content_hash,
                status,
                attempt_count,
                next_retry_at,
                processing_started_at,
                last_error,
                created_at,
                updated_at
            FROM conversation_json_outbox
            WHERE status IN ('pending', 'failed')
              AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
            ORDER BY created_at ASC, id ASC
            LIMIT %s
            """,
            (max(1, int(limit)),),
        )
        claimed: list[dict[str, Any]] = []
        for row in rows:
            task_id = int(row.get("id") or 0)
            if task_id <= 0:
                continue
            affected = self._execute_update(
                """
                UPDATE conversation_json_outbox
                SET
                    status = 'processing',
                    processing_started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status IN ('pending', 'failed')
                  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                """,
                (task_id,),
            )
            if affected:
                refreshed = self.get_task(task_id=task_id)
                if refreshed:
                    claimed.append(refreshed)
        return claimed

    def get_task(self, *, task_id: int) -> dict[str, Any] | None:
        if not self._table_exists():
            return None
        rows = self._execute_query(
            """
            SELECT
                id,
                conversation_id,
                user_id,
                json_version,
                local_path,
                object_name,
                content_hash,
                status,
                attempt_count,
                next_retry_at,
                processing_started_at,
                last_error,
                created_at,
                updated_at
            FROM conversation_json_outbox
            WHERE id = %s
            LIMIT 1
            """,
            (int(task_id),),
        )
        return rows[0] if rows else None

    def touch_processing(self, *, task_id: int) -> int:
        if not self._table_exists():
            return 0
        return self._execute_update(
            """
            UPDATE conversation_json_outbox
            SET
                processing_started_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status = 'processing'
            """,
            (int(task_id),),
        )

    def mark_done(self, *, task_id: int, note: str | None = None) -> int:
        if not self._table_exists():
            return 0
        return self._execute_update(
            """
            UPDATE conversation_json_outbox
            SET
                status = 'done',
                processing_started_at = NULL,
                next_retry_at = NULL,
                last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (note, int(task_id)),
        )

    def mark_retry(self, *, task_id: int, next_retry_at: datetime, last_error: str) -> int:
        if not self._table_exists():
            return 0
        return self._execute_update(
            """
            UPDATE conversation_json_outbox
            SET
                status = 'failed',
                attempt_count = attempt_count + 1,
                next_retry_at = %s,
                processing_started_at = NULL,
                last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (next_retry_at, last_error[:2000], int(task_id)),
        )

    def mark_dead(self, *, task_id: int, last_error: str) -> int:
        if not self._table_exists():
            return 0
        return self._execute_update(
            """
            UPDATE conversation_json_outbox
            SET
                status = 'dead',
                attempt_count = attempt_count + 1,
                next_retry_at = NULL,
                processing_started_at = NULL,
                last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (last_error[:2000], int(task_id)),
        )

    def summarize_counts(self) -> dict[str, int]:
        if not self._table_exists():
            return {"pending": 0, "processing": 0, "failed": 0, "done": 0, "dead": 0}
        rows = self._execute_query(
            """
            SELECT status, COUNT(*) AS total
            FROM conversation_json_outbox
            GROUP BY status
            """
        )
        result = {"pending": 0, "processing": 0, "failed": 0, "done": 0, "dead": 0}
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status in result:
                result[status] = int(row.get("total") or 0)
        return result
