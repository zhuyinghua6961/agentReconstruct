"""Conversation JSON outbox repository."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from server.database.connection import execute_query, execute_update


class ConversationOutboxRepository:
    """Persistence operations for conversation JSON outbox."""

    def __init__(self) -> None:
        self._table_exists_cache: bool | None = None

    def _table_exists(self) -> bool:
        if self._table_exists_cache is not None:
            return self._table_exists_cache
        rows = execute_query(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'conversation_json_outbox'
            """
        )
        total = int((rows[0] or {}).get("total", 0)) if rows else 0
        self._table_exists_cache = total > 0
        return self._table_exists_cache

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
        return execute_update(
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
                str(local_path),
                str(object_name),
                content_hash,
                last_error,
            ),
        )

    def reclaim_stuck_processing(self, *, timeout_seconds: int) -> int:
        if not self._table_exists():
            return 0
        cutoff = datetime.now() - timedelta(seconds=max(1, int(timeout_seconds)))
        return execute_update(
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

        rows = execute_query(
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
            affected = execute_update(
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
        rows = execute_query(
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

    def mark_done(self, *, task_id: int, note: str | None = None) -> int:
        if not self._table_exists():
            return 0
        return execute_update(
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
        return execute_update(
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
            (next_retry_at, str(last_error)[:2000], int(task_id)),
        )

    def mark_dead(self, *, task_id: int, last_error: str) -> int:
        if not self._table_exists():
            return 0
        return execute_update(
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
            (str(last_error)[:2000], int(task_id)),
        )

    def summarize_counts(self) -> dict[str, int]:
        if not self._table_exists():
            return {"pending": 0, "processing": 0, "failed": 0, "done": 0, "dead": 0}
        rows = execute_query(
            """
            SELECT status, COUNT(*) AS total
            FROM conversation_json_outbox
            GROUP BY status
            """
        )
        result = {"pending": 0, "processing": 0, "failed": 0, "done": 0, "dead": 0}
        for row in rows:
            status = str(row.get("status") or "").strip()
            if status in result:
                result[status] = int(row.get("total") or 0)
        return result
