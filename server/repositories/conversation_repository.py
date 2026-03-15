"""Conversation repository for CRUD and message persistence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from server.database.connection import execute_query, execute_update


class ConversationRepository:
    """Persistence operations for conversations and related tables."""

    def __init__(self) -> None:
        self._conversation_columns_cache: set[str] | None = None

    def _load_conversation_columns(self) -> set[str]:
        rows = execute_query("SHOW COLUMNS FROM conversations")
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
        return execute_update(
            """
            INSERT INTO conversations (user_id, title, message_count)
            VALUES (%s, %s, 0)
            """,
            (int(user_id), str(title)),
        )

    def list_conversations(self, *, user_id: int, offset: int, limit: int) -> list[dict[str, Any]]:
        return execute_query(
            """
            SELECT id, user_id, title, message_count, created_at, updated_at
            FROM conversations
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (int(user_id), int(limit), int(offset)),
        )

    def count_conversations(self, *, user_id: int) -> int:
        rows = execute_query(
            """
            SELECT COUNT(*) AS total
            FROM conversations
            WHERE user_id = %s
            """,
            (int(user_id),),
        )
        return int((rows[0] or {}).get("total", 0)) if rows else 0

    def get_conversation(self, *, conversation_id: int, user_id: int) -> dict[str, Any] | None:
        select_fields = self._conversation_select_fields()
        rows = execute_query(
            f"""
            SELECT {select_fields}
            FROM conversations
            WHERE id = %s AND user_id = %s
            LIMIT 1
            """,
            (int(conversation_id), int(user_id)),
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
            params.append(str(sync_status))

        if not assignments:
            return 0

        params.extend([int(conversation_id), int(user_id)])
        return execute_update(
            f"""
            UPDATE conversations
            SET {", ".join(assignments)}
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
            params.append(str(storage_ref))
        if self._has_conversation_column("chat_json_updated_at"):
            assignments.append("chat_json_updated_at = %s")
            params.append(updated_at or datetime.now())

        if not assignments:
            return 0

        conditions = ["id = %s", "user_id = %s"]
        params.extend([int(conversation_id), int(user_id)])

        if expected_version is not None and self._has_conversation_column("chat_json_version"):
            conditions.append("chat_json_version = %s")
            params.append(int(expected_version))

        return execute_update(
            f"""
            UPDATE conversations
            SET {", ".join(assignments)}
            WHERE {' AND '.join(conditions)}
            """,
            tuple(params),
        )

    def set_message_count(self, *, conversation_id: int, user_id: int, message_count: int) -> int:
        return execute_update(
            """
            UPDATE conversations
            SET message_count = %s
            WHERE id = %s AND user_id = %s
            """,
            (max(0, int(message_count)), int(conversation_id), int(user_id)),
        )

    def delete_conversation(self, *, conversation_id: int, user_id: int) -> int:
        return execute_update(
            """
            DELETE FROM conversations
            WHERE id = %s AND user_id = %s
            """,
            (int(conversation_id), int(user_id)),
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
        message_id = execute_update(
            """
            INSERT INTO conversation_messages (conversation_id, user_id, role, content, metadata_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (int(conversation_id), int(user_id), str(role), str(content), metadata_json),
        )
        execute_update(
            """
            UPDATE conversations
            SET message_count = message_count + 1
            WHERE id = %s AND user_id = %s
            """,
            (int(conversation_id), int(user_id)),
        )
        return int(message_id)

    def add_message_with_created_at(
        self,
        *,
        conversation_id: int,
        user_id: int,
        role: str,
        content: str,
        metadata: dict[str, Any] | None,
        created_at: Any,
    ) -> int:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False) if metadata is not None else None
        message_id = execute_update(
            """
            INSERT INTO conversation_messages (conversation_id, user_id, role, content, metadata_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (int(conversation_id), int(user_id), str(role), str(content), metadata_json, created_at),
        )
        execute_update(
            """
            UPDATE conversations
            SET message_count = message_count + 1
            WHERE id = %s AND user_id = %s
            """,
            (int(conversation_id), int(user_id)),
        )
        return int(message_id)

    def delete_message(
        self,
        *,
        message_id: int,
        conversation_id: int,
        user_id: int,
    ) -> int:
        affected = execute_update(
            """
            DELETE FROM conversation_messages
            WHERE id = %s AND conversation_id = %s AND user_id = %s
            """,
            (int(message_id), int(conversation_id), int(user_id)),
        )
        if affected:
            execute_update(
                """
                UPDATE conversations
                SET message_count = GREATEST(message_count - 1, 0)
                WHERE id = %s AND user_id = %s
                """,
                (int(conversation_id), int(user_id)),
            )
        return int(affected)

    def list_messages(self, *, conversation_id: int, user_id: int) -> list[dict[str, Any]]:
        rows = execute_query(
            """
            SELECT m.id, m.role, m.content, m.metadata_json, m.created_at
            FROM conversation_messages AS m
            INNER JOIN conversations AS c ON c.id = m.conversation_id
            WHERE m.conversation_id = %s AND c.user_id = %s
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (int(conversation_id), int(user_id)),
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
        return execute_update(
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
                int(conversation_id),
                int(user_id),
                str(file_type),
                str(file_name),
                local_path,
                storage_ref,
                content_type,
                size_bytes,
            ),
        )

    def add_uploaded_file_with_created_at(
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
        created_at: Any,
    ) -> int:
        return execute_update(
            """
            INSERT INTO conversation_files (
                conversation_id,
                user_id,
                file_type,
                file_name,
                local_path,
                storage_ref,
                content_type,
                size_bytes,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(conversation_id),
                int(user_id),
                str(file_type),
                str(file_name),
                local_path,
                storage_ref,
                content_type,
                size_bytes,
                created_at,
            ),
        )

    def list_uploaded_files(self, *, conversation_id: int, user_id: int) -> list[dict[str, Any]]:
        return execute_query(
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
            (int(conversation_id), int(user_id)),
        )

    def get_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int) -> dict[str, Any] | None:
        rows = execute_query(
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
            (int(conversation_id), int(user_id), int(file_id)),
        )
        return rows[0] if rows else None

    def delete_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int) -> int:
        return int(
            execute_update(
                """
                DELETE FROM conversation_files
                WHERE conversation_id = %s AND user_id = %s AND id = %s
                """,
                (int(conversation_id), int(user_id), int(file_id)),
            )
        )
