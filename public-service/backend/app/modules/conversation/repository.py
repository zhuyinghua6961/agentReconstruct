from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.config import get_settings
from app.core.db import Database


class ConversationRepository:
    def __init__(self, *, database: Database | None = None) -> None:
        self._db = database or Database(settings=get_settings())
        self._conversation_columns_cache: set[str] | None = None

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

    def _load_conversation_columns(self) -> set[str]:
        rows = self._execute_query("SHOW COLUMNS FROM conversations")
        result: set[str] = set()
        for row in rows:
            name = str(row.get("Field") or "").strip().lower()
            if name:
                result.add(name)
        return result

    def _conversation_columns(self) -> set[str]:
        if self._conversation_columns_cache is None:
            self._conversation_columns_cache = self._load_conversation_columns()
        return self._conversation_columns_cache

    def _has_conversation_column(self, column_name: str) -> bool:
        return column_name.strip().lower() in self._conversation_columns()

    def _conversation_select_fields(self) -> str:
        fields = ["id", "user_id", "title", "message_count", "created_at", "updated_at"]
        optional_fields = [
            "chat_json_local_path",
            "chat_json_storage_ref",
            "chat_json_hash",
            "chat_json_size_bytes",
            "chat_json_version",
            "chat_json_updated_at",
            "chat_json_sync_status",
        ]
        for field in optional_fields:
            if self._has_conversation_column(field):
                fields.append(field)
        return ", ".join(fields)

    def create_conversation(self, *, user_id: int, title: str) -> int:
        return self._execute_update(
            """
            INSERT INTO conversations (user_id, title, message_count)
            VALUES (%s, %s, 0)
            """,
            (user_id, title),
        )

    def update_conversation_title(self, *, conversation_id: int, user_id: int, title: str) -> int:
        return self._execute_update(
            """
            UPDATE conversations
            SET title = %s, updated_at = %s
            WHERE id = %s AND user_id = %s
            """,
            (title, datetime.now(), conversation_id, user_id),
        )

    def list_conversations(self, *, user_id: int, offset: int, limit: int) -> list[dict[str, Any]]:
        return self._execute_query(
            """
            SELECT id, user_id, title, message_count, created_at, updated_at
            FROM conversations
            WHERE user_id = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (user_id, limit, offset),
        )

    def count_conversations(self, *, user_id: int) -> int:
        rows = self._execute_query(
            """
            SELECT COUNT(*) AS total
            FROM conversations
            WHERE user_id = %s
            """,
            (user_id,),
        )
        return int((rows[0] or {}).get("total", 0)) if rows else 0

    def get_conversation(self, *, conversation_id: int, user_id: int) -> dict[str, Any] | None:
        select_fields = self._conversation_select_fields()
        rows = self._execute_query(
            f"""
            SELECT {select_fields}
            FROM conversations
            WHERE id = %s AND user_id = %s
            LIMIT 1
            """,
            (conversation_id, user_id),
        )
        return rows[0] if rows else None

    def update_chat_json_index(
        self,
        *,
        conversation_id: int,
        user_id: int,
        local_path: str | None,
        storage_ref: str | None,
        content_hash: str | None,
        size_bytes: int | None,
        version: int,
        sync_status: str,
        updated_at: Any,
    ) -> int:
        assignments: list[str] = []
        params: list[Any] = []

        if self._has_conversation_column("chat_json_local_path"):
            assignments.append("chat_json_local_path = %s")
            params.append(local_path)
        if self._has_conversation_column("chat_json_storage_ref"):
            assignments.append("chat_json_storage_ref = %s")
            params.append(storage_ref)
        if self._has_conversation_column("chat_json_hash"):
            assignments.append("chat_json_hash = %s")
            params.append(content_hash)
        if self._has_conversation_column("chat_json_size_bytes"):
            assignments.append("chat_json_size_bytes = %s")
            params.append(size_bytes)
        if self._has_conversation_column("chat_json_version"):
            assignments.append("chat_json_version = %s")
            params.append(int(version))
        if self._has_conversation_column("chat_json_updated_at"):
            assignments.append("chat_json_updated_at = %s")
            params.append(updated_at)
        if self._has_conversation_column("chat_json_sync_status"):
            assignments.append("chat_json_sync_status = %s")
            params.append(sync_status)

        if not assignments:
            return 0

        params.extend([conversation_id, user_id])
        return self._execute_update(
            f"""
            UPDATE conversations
            SET
                {", ".join(assignments)}
            WHERE id = %s AND user_id = %s
            """,
            tuple(params),
        )

    def mark_chat_json_sync_ok(
        self,
        *,
        conversation_id: int,
        user_id: int,
        expected_version: int | None = None,
        storage_ref: str | None = None,
        updated_at: Any | None = None,
    ) -> int:
        assignments: list[str] = []
        params: list[Any] = []

        if self._has_conversation_column("chat_json_sync_status"):
            assignments.append("chat_json_sync_status = %s")
            params.append("ok")
        if storage_ref and self._has_conversation_column("chat_json_storage_ref"):
            assignments.append("chat_json_storage_ref = %s")
            params.append(storage_ref)
        if self._has_conversation_column("chat_json_updated_at"):
            assignments.append("chat_json_updated_at = %s")
            params.append(updated_at or datetime.now())

        if not assignments:
            return 0

        conditions = ["id = %s", "user_id = %s"]
        params.extend([conversation_id, user_id])
        if expected_version is not None and self._has_conversation_column("chat_json_version"):
            conditions.append("chat_json_version = %s")
            params.append(int(expected_version))

        return self._execute_update(
            f"""
            UPDATE conversations
            SET
                {", ".join(assignments)}
            WHERE {" AND ".join(conditions)}
            """,
            tuple(params),
        )

    def increment_message_count(
        self,
        *,
        conversation_id: int,
        user_id: int,
        delta: int = 1,
        touch_updated_at: bool = True,
    ) -> int:
        assignments = ["message_count = GREATEST(0, message_count + %s)"]
        params: list[Any] = [int(delta)]
        if touch_updated_at:
            assignments.append("updated_at = %s")
            params.append(datetime.now())
        params.extend([conversation_id, user_id])
        return self._execute_update(
            f"""
            UPDATE conversations
            SET {", ".join(assignments)}
            WHERE id = %s AND user_id = %s
            """,
            tuple(params),
        )

    def set_message_count(
        self,
        *,
        conversation_id: int,
        user_id: int,
        message_count: int,
        touch_updated_at: bool = True,
    ) -> int:
        assignments = ["message_count = %s"]
        params: list[Any] = [max(0, int(message_count))]
        if touch_updated_at:
            assignments.append("updated_at = %s")
            params.append(datetime.now())
        params.extend([conversation_id, user_id])
        return self._execute_update(
            f"""
            UPDATE conversations
            SET {", ".join(assignments)}
            WHERE id = %s AND user_id = %s
            """,
            tuple(params),
        )

    def delete_conversation(self, *, conversation_id: int, user_id: int) -> int:
        return self._execute_update(
            """
            DELETE FROM conversations
            WHERE id = %s AND user_id = %s
            """,
            (conversation_id, user_id),
        )

    def add_message(
        self,
        *,
        conversation_id: int,
        user_id: int,
        role: str,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> int:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False) if metadata is not None else None
        message_id = self._execute_update(
            """
            INSERT INTO conversation_messages (conversation_id, user_id, role, content, metadata_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (conversation_id, user_id, role, content, metadata_json),
        )
        self._execute_update(
            """
            UPDATE conversations
            SET message_count = message_count + 1, updated_at = %s
            WHERE id = %s AND user_id = %s
            """,
            (datetime.now(), conversation_id, user_id),
        )
        return message_id

    def list_messages(self, *, conversation_id: int, user_id: int) -> list[dict[str, Any]]:
        rows = self._execute_query(
            """
            SELECT m.id, m.role, m.content, m.metadata_json, m.created_at
            FROM conversation_messages AS m
            INNER JOIN conversations AS c
                ON c.id = m.conversation_id
            WHERE m.conversation_id = %s AND c.user_id = %s
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (conversation_id, user_id),
        )
        for row in rows:
            metadata_raw = row.get("metadata_json")
            if isinstance(metadata_raw, str):
                try:
                    row["metadata"] = json.loads(metadata_raw)
                except Exception:
                    row["metadata"] = {}
            else:
                row["metadata"] = metadata_raw or {}
            row.pop("metadata_json", None)
        return rows

    def add_uploaded_file(
        self,
        *,
        conversation_id: int,
        user_id: int,
        file_type: str,
        file_name: str,
        local_path: str | None,
        storage_ref: str | None,
        content_type: str | None,
        size_bytes: int | None,
    ) -> int:
        return self._execute_update(
            """
            INSERT INTO conversation_files (
                conversation_id,
                user_id,
                file_type,
                file_name,
                local_path,
                storage_ref,
                content_type,
                size_bytes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                conversation_id,
                user_id,
                file_type,
                file_name,
                local_path,
                storage_ref,
                content_type,
                size_bytes,
            ),
        )

    def list_uploaded_files(self, *, conversation_id: int, user_id: int) -> list[dict[str, Any]]:
        return self._execute_query(
            """
            SELECT
                id,
                conversation_id,
                user_id,
                file_type,
                file_name,
                local_path,
                storage_ref,
                content_type,
                size_bytes,
                created_at
            FROM conversation_files
            WHERE conversation_id = %s AND user_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (conversation_id, user_id),
        )

    def get_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int) -> dict[str, Any] | None:
        rows = self._execute_query(
            """
            SELECT
                id,
                conversation_id,
                user_id,
                file_type,
                file_name,
                local_path,
                storage_ref,
                content_type,
                size_bytes,
                created_at
            FROM conversation_files
            WHERE conversation_id = %s AND user_id = %s AND id = %s
            LIMIT 1
            """,
            (conversation_id, user_id, file_id),
        )
        return rows[0] if rows else None

    def list_uploaded_files_for_processing_recovery(self, *, limit: int) -> list[dict[str, Any]]:
        return self._execute_query(
            """
            SELECT
                id,
                conversation_id,
                user_id,
                file_type,
                file_name,
                local_path,
                storage_ref,
                content_type,
                size_bytes,
                created_at
            FROM conversation_files
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (max(1, int(limit)),),
        )

    def delete_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int) -> int:
        return self._execute_update(
            """
            DELETE FROM conversation_files
            WHERE id = %s AND conversation_id = %s AND user_id = %s
            """,
            (file_id, conversation_id, user_id),
        )
