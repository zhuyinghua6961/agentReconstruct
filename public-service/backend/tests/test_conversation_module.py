from __future__ import annotations

import copy
import threading
import pytest
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.deps import AuthContext
from app.main import app
from app.modules.auth import service as auth_service_module
from app.modules.auth.deps import require_auth_context
from app.modules.conversation import api as conversation_api_module
from app.modules.conversation import service as conversation_service_module
from app.modules.conversation.cache import cache_conversation_detail, get_recent_conversation_list_pages
from app.modules.conversation.json_store import ConversationJsonStore
from app.modules.conversation.outbox_worker import ChatJsonOutboxConfig, ChatJsonOutboxWorker
from app.modules.conversation.repository import ConversationRepository
from app.modules.conversation.service import ConversationService
from app.modules.conversation.upload_processing_worker import UploadProcessingWorker
from app.modules.quota import deps as quota_deps
from app.modules.quota import service as quota_service_module
from app.modules.storage.service import storage_service
from app.integrations.redis import RedisService
from app.integrations.storage.local import LocalStorageBackend


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = int(ex)
        return True

    def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
                self.values.pop(key, None)
                self.expirations.pop(key, None)
        return deleted

    def expire(self, key: str, seconds: int):
        if key not in self.values:
            return False
        self.expirations[key] = int(seconds)
        return True

    def ttl(self, key: str):
        return self.expirations.get(key)


class _MemoryConversationRepo:
    def __init__(self) -> None:
        self._next_conversation_id = 1
        self._next_file_id = 1
        self._next_assistant_task_id = 1
        self.conversations: dict[int, dict] = {}
        self.messages: dict[int, list[dict]] = {}
        self.files: dict[int, list[dict]] = {}
        self.assistant_tasks: dict[int, dict] = {}

    def create_conversation(self, *, user_id: int, title: str) -> int:
        conversation_id = self._next_conversation_id
        self._next_conversation_id += 1
        now = datetime.now()
        self.conversations[conversation_id] = {
            "id": conversation_id,
            "user_id": user_id,
            "title": title,
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
            "chat_json_local_path": None,
            "chat_json_storage_ref": None,
            "chat_json_hash": None,
            "chat_json_size_bytes": None,
            "chat_json_version": 0,
            "chat_json_updated_at": None,
            "chat_json_sync_status": None,
        }
        self.messages[conversation_id] = []
        self.files[conversation_id] = []
        return conversation_id

    def update_conversation_title(self, *, conversation_id: int, user_id: int, title: str) -> int:
        row = self.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            return 0
        stored = self.conversations[conversation_id]
        stored["title"] = title
        stored["updated_at"] = datetime.now()
        return 1

    def list_conversations(self, *, user_id: int, offset: int, limit: int) -> list[dict]:
        rows = [dict(row) for row in self.conversations.values() if int(row["user_id"]) == int(user_id)]
        rows.sort(key=lambda item: (item.get("updated_at"), item.get("id")), reverse=True)
        return rows[offset : offset + limit]

    def count_conversations(self, *, user_id: int) -> int:
        return sum(1 for row in self.conversations.values() if int(row["user_id"]) == int(user_id))

    def get_conversation(self, *, conversation_id: int, user_id: int) -> dict | None:
        row = self.conversations.get(int(conversation_id))
        if not row or int(row["user_id"]) != int(user_id):
            return None
        return dict(row)

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
        updated_at,
    ) -> int:
        row = self.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            return 0
        stored = self.conversations[int(conversation_id)]
        stored["chat_json_local_path"] = local_path
        stored["chat_json_storage_ref"] = storage_ref
        stored["chat_json_hash"] = content_hash
        stored["chat_json_size_bytes"] = size_bytes
        stored["chat_json_version"] = int(version)
        stored["chat_json_sync_status"] = sync_status
        stored["chat_json_updated_at"] = updated_at
        stored["updated_at"] = updated_at
        return 1

    def mark_chat_json_sync_ok(
        self,
        *,
        conversation_id: int,
        user_id: int,
        expected_version: int | None = None,
        storage_ref: str | None = None,
        updated_at=None,
    ) -> int:
        row = self.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            return 0
        stored = self.conversations[int(conversation_id)]
        if expected_version is not None and int(stored.get("chat_json_version") or 0) != int(expected_version):
            return 0
        stored["chat_json_sync_status"] = "ok"
        if storage_ref:
            stored["chat_json_storage_ref"] = storage_ref
        stored["chat_json_updated_at"] = updated_at or datetime.now()
        return 1

    def increment_message_count(
        self,
        *,
        conversation_id: int,
        user_id: int,
        delta: int = 1,
        touch_updated_at: bool = True,
    ) -> int:
        row = self.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            return 0
        self.conversations[int(conversation_id)]["message_count"] = max(
            0,
            int(self.conversations[int(conversation_id)]["message_count"]) + int(delta),
        )
        if touch_updated_at:
            self.conversations[int(conversation_id)]["updated_at"] = datetime.now()
        return 1

    def set_message_count(
        self,
        *,
        conversation_id: int,
        user_id: int,
        message_count: int,
        touch_updated_at: bool = True,
    ) -> int:
        row = self.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            return 0
        self.conversations[int(conversation_id)]["message_count"] = max(0, int(message_count))
        if touch_updated_at:
            self.conversations[int(conversation_id)]["updated_at"] = datetime.now()
        return 1

    def delete_conversation(self, *, conversation_id: int, user_id: int) -> int:
        row = self.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            return 0
        self.conversations.pop(int(conversation_id), None)
        self.messages.pop(int(conversation_id), None)
        self.files.pop(int(conversation_id), None)
        return 1

    def list_messages(self, *, conversation_id: int, user_id: int) -> list[dict]:
        if self.get_conversation(conversation_id=conversation_id, user_id=user_id) is None:
            return []
        return [dict(item) for item in self.messages.get(int(conversation_id), [])]

    def get_authority_assistant_task(self, *, task_id: int) -> dict | None:
        row = self.assistant_tasks.get(int(task_id))
        return copy.deepcopy(row) if row else None

    def find_authority_assistant_placeholder_by_idempotency_key(
        self,
        *,
        conversation_id: int,
        user_id: int,
        idempotency_key: str,
    ) -> dict | None:
        lookup = str(idempotency_key or "")
        for row in self.assistant_tasks.values():
            if int(row.get("conversation_id") or 0) != int(conversation_id):
                continue
            if int(row.get("user_id") or 0) != int(user_id):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if str(metadata.get("assistant_async_state") or "").strip().lower() in {"done", "dead"}:
                continue
            if str(metadata.get("idempotency_key") or "") == lookup:
                return copy.deepcopy(row)
        return None

    @staticmethod
    def _assistant_placeholder_terminal_status(metadata: dict) -> str:
        terminal_event = metadata.get("terminal_event") if isinstance(metadata.get("terminal_event"), dict) else {}
        if terminal_event:
            status = str(terminal_event.get("terminal_status") or "").strip().lower()
            return status if status in {"done", "failed", "canceled"} else "done"
        if isinstance(metadata.get("final_event"), dict):
            return "done"
        return "done"

    @staticmethod
    def _assistant_terminal_rank(status: str) -> int:
        normalized = str(status or "").strip().lower()
        if normalized == "done":
            return 3
        if normalized == "failed":
            return 2
        return 1

    def _update_authority_assistant_placeholder(self, *, row: dict, final_event: dict | None = None, terminal_event: dict | None = None) -> dict:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if metadata.get("authority_assistant_terminal_async") is True:
            next_terminal_event = copy.deepcopy(terminal_event or {})
            if not next_terminal_event and isinstance(final_event, dict):
                next_terminal_event = {"terminal_status": "done", **copy.deepcopy(final_event or {})}
            metadata["terminal_event"] = next_terminal_event
            metadata["assistant_async_state"] = "pending"
            metadata["terminal_async_state"] = "accepted"
        else:
            next_final_event = copy.deepcopy(final_event or {})
            if not next_final_event and isinstance(terminal_event, dict):
                next_final_event = {
                    key: value
                    for key, value in copy.deepcopy(terminal_event or {}).items()
                    if key not in {"terminal_status", "failure"}
                }
                next_final_event["done_seen"] = True
            metadata["final_event"] = next_final_event
            metadata["assistant_async_state"] = "pending"
        metadata["processing_started_at"] = None
        metadata["last_error"] = ""
        metadata["materialized_message_id"] = ""
        metadata["next_retry_at"] = None
        row["content"] = str(((terminal_event or final_event) or {}).get("answer_text") or "")
        return {"task_id": int(row["id"]), "deduped": False, "metadata": copy.deepcopy(metadata)}

    def enqueue_authority_assistant_task(
        self,
        *,
        conversation_id: int,
        user_id: int,
        trace_id: str,
        source_service: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        idempotency_key: str,
        final_event: dict[str, object],
    ) -> dict:
        existing = self.find_authority_assistant_placeholder_by_idempotency_key(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if isinstance(existing, dict):
            metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
            current_status = self._assistant_placeholder_terminal_status(metadata)
            if self._assistant_terminal_rank("done") > self._assistant_terminal_rank(current_status):
                row = self.assistant_tasks[int(existing["id"])]
                return self._update_authority_assistant_placeholder(row=row, final_event=final_event)
            return {"task_id": int(existing["id"]), "deduped": True, "metadata": copy.deepcopy(metadata)}
        task_id = self._next_assistant_task_id
        self._next_assistant_task_id += 1
        metadata = {
            "authority_assistant_async": True,
            "assistant_async_state": "pending",
            "trace_id": str(trace_id or ""),
            "source_service": str(source_service or ""),
            "route": str(route or ""),
            "requested_mode": str(requested_mode or ""),
            "actual_mode": str(actual_mode or ""),
            "idempotency_key": str(idempotency_key or ""),
            "final_event": copy.deepcopy(final_event or {}),
            "processing_started_at": None,
            "materialized_message_id": "",
            "last_error": "",
        }
        self.assistant_tasks[int(task_id)] = {
            "id": int(task_id),
            "conversation_id": int(conversation_id),
            "user_id": int(user_id),
            "content": str((final_event or {}).get("answer_text") or ""),
            "metadata": metadata,
        }
        return {"task_id": int(task_id), "deduped": False, "metadata": copy.deepcopy(metadata)}

    def enqueue_authority_assistant_terminal_task(
        self,
        *,
        conversation_id: int,
        user_id: int,
        trace_id: str,
        source_service: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        idempotency_key: str,
        terminal_event: dict[str, object],
    ) -> dict:
        existing = self.find_authority_assistant_placeholder_by_idempotency_key(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if isinstance(existing, dict):
            metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
            current_status = self._assistant_placeholder_terminal_status(metadata)
            incoming_status = self._assistant_placeholder_terminal_status({"terminal_event": terminal_event})
            if self._assistant_terminal_rank(incoming_status) > self._assistant_terminal_rank(current_status):
                row = self.assistant_tasks[int(existing["id"])]
                return self._update_authority_assistant_placeholder(row=row, terminal_event=terminal_event)
            return {"task_id": int(existing["id"]), "deduped": True, "metadata": copy.deepcopy(metadata)}
        task_id = self._next_assistant_task_id
        self._next_assistant_task_id += 1
        metadata = {
            "authority_assistant_terminal_async": True,
            "assistant_async_state": "pending",
            "terminal_async_state": "accepted",
            "trace_id": str(trace_id or ""),
            "source_service": str(source_service or ""),
            "route": str(route or ""),
            "requested_mode": str(requested_mode or ""),
            "actual_mode": str(actual_mode or ""),
            "idempotency_key": str(idempotency_key or ""),
            "terminal_event": copy.deepcopy(terminal_event or {}),
            "processing_started_at": None,
            "materialized_message_id": "",
            "last_error": "",
        }
        self.assistant_tasks[int(task_id)] = {
            "id": int(task_id),
            "conversation_id": int(conversation_id),
            "user_id": int(user_id),
            "content": str((terminal_event or {}).get("answer_text") or ""),
            "metadata": metadata,
        }
        return {"task_id": int(task_id), "deduped": False, "metadata": copy.deepcopy(metadata)}

    def claim_pending_authority_assistant_tasks(self, *, limit: int) -> list[dict]:
        claimed: list[dict] = []
        for task_id in sorted(self.assistant_tasks):
            if len(claimed) >= int(limit):
                break
            row = self.assistant_tasks[int(task_id)]
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if str(metadata.get("assistant_async_state") or "") != "pending":
                continue
            metadata["assistant_async_state"] = "processing"
            metadata["processing_started_at"] = "now"
            claimed.append(copy.deepcopy(row))
        return claimed

    def mark_authority_assistant_task_done(self, *, task_id: int, materialized_message_id: str, note: str = "ok") -> int:
        row = self.assistant_tasks.get(int(task_id))
        if row is None:
            return 0
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        metadata["assistant_async_state"] = "done"
        if metadata.get("authority_assistant_terminal_async") is True:
            metadata["terminal_async_state"] = "materialized"
        metadata["materialized_message_id"] = str(materialized_message_id or "")
        metadata["processing_started_at"] = None
        metadata["last_error"] = str(note or "")
        return 1

    def mark_authority_assistant_task_failed(self, *, task_id: int, last_error: str) -> int:
        row = self.assistant_tasks.get(int(task_id))
        if row is None:
            return 0
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        metadata["assistant_async_state"] = "failed"
        if metadata.get("authority_assistant_terminal_async") is True:
            metadata["terminal_async_state"] = "retryable"
        metadata["processing_started_at"] = None
        metadata["last_error"] = str(last_error or "")
        return 1

    def authority_assistant_inbox_status(self) -> dict:
        backlog = 0
        processing = 0
        failed = 0
        for row in self.assistant_tasks.values():
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            state = str(metadata.get("assistant_async_state") or "")
            if state == "pending":
                backlog += 1
            elif state == "processing":
                processing += 1
            elif state == "failed":
                failed += 1
        return {"backlog": backlog, "processing": processing, "failed": failed, "enabled": True}

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
        if self.get_conversation(conversation_id=conversation_id, user_id=user_id) is None:
            return 0
        file_id = self._next_file_id
        self._next_file_id += 1
        self.files[int(conversation_id)].append(
            {
                "id": file_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "file_type": file_type,
                "file_name": file_name,
                "local_path": local_path,
                "storage_ref": storage_ref,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "created_at": datetime.now(),
            }
        )
        self.conversations[int(conversation_id)]["updated_at"] = datetime.now()
        return file_id

    def list_uploaded_files(self, *, conversation_id: int, user_id: int) -> list[dict]:
        if self.get_conversation(conversation_id=conversation_id, user_id=user_id) is None:
            return []
        return [dict(item) for item in self.files.get(int(conversation_id), [])]

    def get_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int) -> dict | None:
        if self.get_conversation(conversation_id=conversation_id, user_id=user_id) is None:
            return None
        for item in self.files.get(int(conversation_id), []):
            if int(item["id"]) == int(file_id):
                return dict(item)
        return None

    def list_uploaded_files_for_processing_recovery(self, *, limit: int) -> list[dict]:
        rows: list[dict] = []
        for items in self.files.values():
            for item in items:
                rows.append(dict(item))
        rows.sort(key=lambda item: (item.get("created_at"), item.get("id")), reverse=True)
        return rows[:limit]

    def delete_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int) -> int:
        if self.get_conversation(conversation_id=conversation_id, user_id=user_id) is None:
            return 0
        removed = 0
        kept: list[dict] = []
        for item in self.files.get(int(conversation_id), []):
            if int(item["id"]) == int(file_id):
                removed += 1
                continue
            kept.append(item)
        self.files[int(conversation_id)] = kept
        return removed


def test_normalize_json_messages_skips_authority_placeholders():
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = ConversationService(
        repo=_MemoryConversationRepo(),
        json_store=ConversationJsonStore(project_root="/tmp"),
        outbox_repo=_OutboxRecorder(),
        workspace_root="/tmp",
        redis_service=redis_service,
    )

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": 1,
            "role": "assistant",
            "content": "",
            "created_at": now,
            "metadata": {"authority_assistant_async": True, "assistant_async_state": "pending"},
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "",
            "created_at": now,
            "metadata": {"authority_assistant_terminal_async": True, "terminal_async_state": "accepted"},
        },
        {
            "id": 3,
            "role": "assistant",
            "content": "materialized answer",
            "created_at": now,
            "metadata": {"done_seen": True},
        },
    ]

    normalized = service._normalize_json_messages(rows)

    assert len(normalized) == 1
    assert normalized[0]["content"] == "materialized answer"


def test_build_document_from_cached_detail_preserves_terminal_metadata():
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = ConversationService(
        repo=_MemoryConversationRepo(),
        json_store=ConversationJsonStore(project_root="/tmp", redis_service=redis_service),
        outbox_repo=_OutboxRecorder(),
        workspace_root="/tmp",
        redis_service=redis_service,
    )
    row = {
        "id": 12,
        "user_id": 7,
        "title": "Cached Terminal",
        "message_count": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "chat_json_version": 1,
    }
    payload = {
        "success": True,
        "data": {
            "conversation_id": 12,
            "user_id": 7,
            "title": "Cached Terminal",
            "message_count": 1,
            "created_at": service._to_iso(row["created_at"], fallback=service._now_iso()),
            "updated_at": service._to_iso(row["updated_at"], fallback=service._now_iso()),
            "messages": [
                {
                    "message_id": "m_000001",
                    "role": "assistant",
                    "content": "partial answer",
                    "created_at": service._to_iso(row["updated_at"], fallback=service._now_iso()),
                    "status": "failed",
                    "terminal_status": "failed",
                    "failure_stage": "llm_stream",
                    "failure_code": "LLM_TIMEOUT",
                    "failure_message": "timeout",
                    "retriable": True,
                    "done_seen": False,
                    "metadata": {
                        "trace_id": "trace-cached-terminal",
                        "route": "kb_qa",
                    },
                }
            ],
            "uploaded_files_all": [],
        },
        "cache_meta": {"cached_at": service._now_iso()},
    }
    cache_conversation_detail(
        redis_service=redis_service,
        user_id=7,
        conversation_id=12,
        payload=payload,
    )

    document = service._build_document_from_cached_detail(row=row, conversation_id=12, user_id=7)

    assert isinstance(document, dict)
    message = document["messages"][0]
    metadata = message["metadata"]
    assert message["status"] == "failed"
    assert metadata["terminal_status"] == "failed"
    assert metadata["failure_stage"] == "llm_stream"
    assert metadata["failure_code"] == "LLM_TIMEOUT"
    assert metadata["failure_message"] == "timeout"
    assert metadata["retriable"] is True


def test_build_document_from_cached_detail_prefers_explicit_terminal_status():
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = ConversationService(
        repo=_MemoryConversationRepo(),
        json_store=ConversationJsonStore(project_root="/tmp", redis_service=redis_service),
        outbox_repo=_OutboxRecorder(),
        workspace_root="/tmp",
        redis_service=redis_service,
    )
    row = {
        "id": 13,
        "user_id": 7,
        "title": "Cached Terminal Explicit Status",
        "message_count": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "chat_json_version": 1,
    }
    payload = {
        "success": True,
        "data": {
            "conversation_id": 13,
            "user_id": 7,
            "title": "Cached Terminal Explicit Status",
            "message_count": 1,
            "created_at": service._to_iso(row["created_at"], fallback=service._now_iso()),
            "updated_at": service._to_iso(row["updated_at"], fallback=service._now_iso()),
            "messages": [
                {
                    "message_id": "m_000001",
                    "role": "assistant",
                    "content": "partial answer",
                    "created_at": service._to_iso(row["updated_at"], fallback=service._now_iso()),
                    "status": "done",
                    "terminal_status": "failed",
                    "failure_stage": "llm_stream",
                    "failure_message": "timeout",
                    "retriable": True,
                    "done_seen": False,
                    "metadata": {
                        "trace_id": "trace-cached-explicit-terminal",
                        "route": "kb_qa",
                    },
                }
            ],
            "uploaded_files_all": [],
        },
        "cache_meta": {"cached_at": service._now_iso()},
    }
    cache_conversation_detail(
        redis_service=redis_service,
        user_id=7,
        conversation_id=13,
        payload=payload,
    )

    document = service._build_document_from_cached_detail(row=row, conversation_id=13, user_id=7)

    assert isinstance(document, dict)
    message = document["messages"][0]
    metadata = message["metadata"]
    assert message["status"] == "done"
    assert metadata["terminal_status"] == "failed"


class _TrackingConversationRepo(_MemoryConversationRepo):
    def __init__(self) -> None:
        super().__init__()
        self.list_messages_calls = 0
        self.list_uploaded_files_calls = 0
        self.get_uploaded_file_calls = 0

    def list_messages(self, *, conversation_id: int, user_id: int) -> list[dict]:
        self.list_messages_calls += 1
        return super().list_messages(conversation_id=conversation_id, user_id=user_id)

    def list_uploaded_files(self, *, conversation_id: int, user_id: int) -> list[dict]:
        self.list_uploaded_files_calls += 1
        return super().list_uploaded_files(conversation_id=conversation_id, user_id=user_id)

    def get_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int) -> dict | None:
        self.get_uploaded_file_calls += 1
        return super().get_uploaded_file(conversation_id=conversation_id, user_id=user_id, file_id=file_id)


class _OutboxRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue_task(self, **kwargs) -> int:
        self.calls.append(dict(kwargs))
        return len(self.calls)


class _FailingStorageBackend:
    def object_exists(self, *, object_name: str, bucket: str | None = None) -> bool:
        _ = object_name, bucket
        return False

    def upload_file(self, *, local_path: str, object_name: str, content_type: str | None = None) -> str:
        _ = local_path, object_name, content_type
        raise RuntimeError("upload_failed")

    def download_file(self, *, object_name: str, local_path: str) -> bool:
        _ = object_name, local_path
        return False

    def get_file_url(self, *, object_name: str, expires_seconds: int = 3600) -> str:
        _ = object_name, expires_seconds
        return ""

    def delete_object(self, *, object_name: str, bucket: str | None = None) -> bool:
        _ = object_name, bucket
        return False


def _route_for(path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


def test_conversation_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/v1/conversations" in paths
    assert "/api/conversations" in paths
    assert "/api/v1/conversations/{conversation_id}" in paths
    assert "/api/v1/conversations/{conversation_id}/files/{file_id}/download" in paths

    list_route = _route_for("/api/v1/conversations", "GET")
    detail_route = _route_for("/api/v1/conversations/{conversation_id}", "GET")
    assert require_auth_context in {dep.call for dep in list_route.dependant.dependencies}
    assert require_auth_context in {dep.call for dep in detail_route.dependant.dependencies}


def test_conversation_runtime_service_is_bound_to_live_http_route():
    with TestClient(app) as client:
        assert client.app.state.conversation_service is conversation_service_module.conversation_service


def test_update_conversation_title_route_contract(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(
            conversation_service_module.conversation_service,
            "update_conversation_title",
            lambda **kwargs: {
                "success": True,
                "data": {
                    "conversation_id": kwargs["conversation_id"],
                    "title": kwargs["title"],
                    "message_count": 2,
                    "created_at": "2026-03-17T10:00:00+08:00",
                    "updated_at": "2026-03-17T10:05:00+08:00",
                },
            },
        )

        response = client.put(
            "/api/v1/conversations/12/title",
            json={"title": "updated title"},
        )
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["conversation_id"] == 12
    assert payload["data"]["title"] == "updated title"


def test_conversation_http_crud_contracts(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(
            conversation_service_module.conversation_service,
            "create_conversation",
            lambda **kwargs: {
                "success": True,
                "data": {
                    "conversation_id": 21,
                    "title": kwargs["title"],
                    "message_count": 0,
                    "created_at": "2026-03-17T10:00:00+08:00",
                    "updated_at": "2026-03-17T10:00:00+08:00",
                },
            },
        )
        monkeypatch.setattr(
            conversation_service_module.conversation_service,
            "list_conversations",
            lambda **kwargs: {
                "success": True,
                "data": {
                    "conversations": [
                        {
                            "conversation_id": 21,
                            "title": "Alpha",
                            "message_count": 2,
                            "created_at": "2026-03-17T10:00:00+08:00",
                            "updated_at": "2026-03-17T10:05:00+08:00",
                        }
                    ],
                    "total_count": 1,
                    "page": kwargs["page"],
                    "page_size": kwargs["page_size"],
                },
            },
        )
        monkeypatch.setattr(
            conversation_service_module.conversation_service,
            "get_conversation_detail",
            lambda **kwargs: {
                "success": True,
                "data": {
                    "conversation_id": kwargs["conversation_id"],
                    "title": "Alpha",
                    "message_count": 2,
                    "created_at": "2026-03-17T10:00:00+08:00",
                    "updated_at": "2026-03-17T10:05:00+08:00",
                    "messages": [{"role": "user", "content": "hello", "metadata": {}}],
                    "uploaded_files": [],
                },
            },
        )
        monkeypatch.setattr(
            conversation_service_module.conversation_service,
            "delete_conversation",
            lambda **kwargs: {"success": True, "message": "deleted"},
        )

        create_resp = client.post("/api/v1/conversations", json={"title": "Alpha"})
        list_resp = client.get("/api/v1/conversations?page=1&page_size=20")
        detail_resp = client.get("/api/v1/conversations/21")
        delete_resp = client.delete("/api/v1/conversations/21")
        client.app.dependency_overrides.clear()

    assert create_resp.status_code == 201
    assert create_resp.json()["data"]["conversation_id"] == 21
    assert list_resp.status_code == 200
    assert list_resp.json()["data"]["conversations"][0]["title"] == "Alpha"
    assert detail_resp.status_code == 200
    assert detail_resp.json()["data"]["messages"][0]["content"] == "hello"
    assert delete_resp.status_code == 200
    assert delete_resp.json()["message"] == "deleted"


def test_download_conversation_file_route_accepts_query_token(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.4\n")
    quota_calls: list[tuple[str, str]] = []

    with TestClient(app) as client:
        monkeypatch.setattr(auth_service_module.auth_service, "decode_token", lambda token: {"user_id": 7, "role": "user"} if token == "token-1" else None)
        monkeypatch.setattr(
            auth_service_module.auth_service,
            "get_user_by_id",
            lambda user_id: {"id": user_id, "status": "active", "role": "user", "user_type": 3, "username": "alice"},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: quota_calls.append(("check", kwargs["quota_type"])) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: quota_calls.append(("increment", kwargs["quota_type"])) or {"success": True},
        )
        monkeypatch.setattr(
            conversation_service_module.conversation_service,
            "resolve_uploaded_file_download",
            lambda **kwargs: (
                {"success": True},
                200,
                {"mode": "local_file", "target": str(file_path), "file_name": "sample.pdf"},
            ),
        )

        response = client.get("/api/v1/conversations/12/files/8/download?token=token-1")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.content.startswith(b"%PDF-1.4")
    assert quota_calls == [("check", "file_view"), ("increment", "file_view")]


def test_conversation_service_round_trip():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Alpha")
        assert created["success"] is True
        conversation_id = int(created["data"]["conversation_id"])

        listed = service.list_conversations(user_id=7, page=1, page_size=20)
        assert listed["success"] is True
        assert listed["data"]["conversations"][0]["title"] == "Alpha"
        assert get_recent_conversation_list_pages(redis_service=redis_service, user_id=7) == [{"page": 1, "page_size": 20}]

        added_user = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="user",
            content="hello",
            metadata={"source": "test"},
        )
        assert added_user["success"] is True

        added_assistant = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="world",
            metadata={
                "route": "chat",
                "trace_id": "trace-1",
                "used_files": [{"file_id": 3}],
                "done_seen": True,
                "references": [{"id": "r1"}],
                "reference_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "pdf_links": [{"doi": "10.1000/demo", "pdf_url": "/api/v1/view_pdf/10.1000/demo"}],
                "doi_locations": {"10.1000/demo": [{"start": 1, "end": 3}]},
                "steps": [{"step": "s1"}],
                "query_mode": "normal",
            },
        )
        assert added_assistant["success"] is True

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assert detail["data"]["message_count"] == 2
        assert len(detail["data"]["messages"]) == 2
        assert detail["data"]["messages"][1]["metadata"]["reference_links"][0]["doi"] == "10.1000/demo"
        assert detail["data"]["messages"][1]["doi_locations"]["10.1000/demo"][0]["start"] == 1

        latest = service.get_latest_turn_context(user_id=7, conversation_id=conversation_id)
        assert latest["success"] is True
        assert latest["data"]["last_turn_route"] == "chat"
        assert latest["data"]["trace_id"] == "trace-1"
        assert latest["data"]["last_focus_file_ids"] == [3]

        file_path = Path(tempdir) / "sample.pdf"
        file_path.write_bytes(b"pdf")
        added_file = service.add_uploaded_file(
            user_id=7,
            conversation_id=conversation_id,
            file_type="pdf",
            file_name="sample.pdf",
            local_path=str(file_path),
            storage_ref=None,
            content_type="application/pdf",
            size_bytes=3,
        )
        assert added_file["success"] is True
        file_id = int(added_file["data"]["file_id"])

        listed_files = service.list_uploaded_files(user_id=7, conversation_id=conversation_id, include_deleted=False)
        assert listed_files["success"] is True
        assert len(listed_files["data"]["files"]) == 1

        fetched_file = service.get_uploaded_file(user_id=7, conversation_id=conversation_id, file_id=file_id)
        assert fetched_file["success"] is True
        assert fetched_file["data"]["file_name"] == "sample.pdf"

        payload, status_code, download = service.resolve_uploaded_file_download(
            user_id=7,
            conversation_id=conversation_id,
            file_id=file_id,
        )
        assert payload["success"] is True
        assert status_code == 200
        assert download == {"mode": "local_file", "target": str(file_path), "file_name": "sample.pdf"}

        removed = service.remove_uploaded_file(user_id=7, conversation_id=conversation_id, file_id=file_id)
        assert removed["success"] is True
        assert removed["data"]["cleanup_pending"] is False

        listed_deleted = service.list_uploaded_files(user_id=7, conversation_id=conversation_id, include_deleted=True)
        assert listed_deleted["success"] is True
        assert listed_deleted["data"]["files"][0]["file_status"] == "deleted"

        deleted = service.delete_conversation(user_id=7, conversation_id=conversation_id)
        assert deleted["success"] is True


def test_conversation_service_add_message_and_detail_hide_raw_patent_id_citation_markers():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Patent Citation Visibility")
        conversation_id = int(created["data"]["conversation_id"])

        added_user = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="user",
            content="请总结这个专利",
        )
        assert added_user["success"] is True

        added_assistant = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="结论来自专利 (patent_id=CN115132975B)。",
            metadata={
                "trace_id": "trace-patent-citation",
                "source_service": "patentQA",
                "requested_mode": "patent",
                "actual_mode": "patent",
                "route": "kb_qa",
            },
        )
        assert added_assistant["success"] is True

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assistant_message = detail["data"]["messages"][-1]
        assert "patent_id=" not in assistant_message["content"]
        assert "CN115132975B" in assistant_message["content"]


def test_add_message_dedupes_repeated_trailing_patent_citation_in_user_visible_content():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="patent citation dedupe")
        conversation_id = int(created["data"]["conversation_id"])

        added_user = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="user",
            content="请总结这个专利",
        )
        assert added_user["success"] is True

        added_assistant = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="双颗粒级配：CN109192948B 使用球形小颗粒填充大颗粒空隙，同时 1C 放电比容量为 149–150 mAh/g (patent_id=CN109192948B)。",
            metadata={
                "trace_id": "trace-patent-citation-dedupe",
                "query_mode": "patent_kb_qa",
                "route": "kb_qa",
            },
        )
        assert added_assistant["success"] is True

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assistant_message = detail["data"]["messages"][-1]
        assert "patent_id=" not in assistant_message["content"]
        assert assistant_message["content"].count("CN109192948B") == 1
        assert "(CN109192948B)" not in assistant_message["content"]


def test_add_message_dedupes_repeated_trailing_patent_citation_when_patent_id_is_followed_by_chinese_text():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="patent citation dedupe chinese adjacency")
        conversation_id = int(created["data"]["conversation_id"])

        added_user = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="user",
            content="请总结这个专利",
        )
        assert added_user["success"] is True

        added_assistant = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="双颗粒级配：CN109192948B使用球形小颗粒填充大颗粒空隙，同时 1C 放电比容量为 149–150 mAh/g (patent_id=CN109192948B)。",
            metadata={
                "trace_id": "trace-patent-citation-dedupe-chinese-adjacency",
                "query_mode": "patent_kb_qa",
                "route": "kb_qa",
            },
        )
        assert added_assistant["success"] is True

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assistant_message = detail["data"]["messages"][-1]
        assert assistant_message["content"].count("CN109192948B") == 1
        assert "(CN109192948B)" not in assistant_message["content"]


def test_conversation_detail_dedupes_existing_patent_tail_citation_when_loading_history():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="patent citation history dedupe")
        conversation_id = int(created["data"]["conversation_id"])

        added_user = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="user",
            content="请总结这个专利",
        )
        assert added_user["success"] is True

        added_assistant = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="双颗粒级配：CN109192948B 使用球形小颗粒填充大颗粒空隙，同时 1C 放电比容量为 149–150 mAh/g。",
            metadata={
                "trace_id": "trace-patent-citation-history-dedupe",
                "query_mode": "patent_kb_qa",
                "route": "kb_qa",
            },
        )
        assert added_assistant["success"] is True

        row = service._repo.get_conversation(conversation_id=conversation_id, user_id=7)
        assert row is not None
        with service._json_store.conversation_lock(user_id=7, conversation_id=conversation_id):
            document, _ = service._load_or_bootstrap_document(
                row=row,
                conversation_id=conversation_id,
                user_id=7,
                prefer_cached_detail=False,
            )
            document["messages"][-1]["content"] = (
                "双颗粒级配：CN109192948B 使用球形小颗粒填充大颗粒空隙，同时 1C 放电比容量为 149–150 mAh/g (CN109192948B)。"
            )
            service._persist_document_and_index(
                row=row,
                conversation_id=conversation_id,
                user_id=7,
                document=document,
            )
        service._invalidate_detail_cache(user_id=7, conversation_id=conversation_id)

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assistant_message = detail["data"]["messages"][-1]
        assert assistant_message["content"].count("CN109192948B") == 1
        assert "(CN109192948B)" not in assistant_message["content"]


def test_add_message_preserves_trailing_patent_citation_when_earlier_match_only_appears_inside_url():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="patent citation url preservation")
        conversation_id = int(created["data"]["conversation_id"])

        added_user = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="user",
            content="请总结这个专利",
        )
        assert added_user["success"] is True

        added_assistant = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="原始链接：https://example.com/?id=CN109192948B (patent_id=CN109192948B)。",
            metadata={
                "trace_id": "trace-patent-citation-url-preservation",
                "query_mode": "patent_kb_qa",
                "route": "kb_qa",
            },
        )
        assert added_assistant["success"] is True

        row = service._repo.get_conversation(conversation_id=conversation_id, user_id=7)
        assert row is not None
        with service._json_store.conversation_lock(user_id=7, conversation_id=conversation_id):
            document, _ = service._load_or_bootstrap_document(
                row=row,
                conversation_id=conversation_id,
                user_id=7,
                prefer_cached_detail=False,
            )
            stored_content = document["messages"][-1]["content"]

        assert "patent_id=" not in stored_content
        assert "(CN109192948B)" in stored_content


def test_prepare_response_messages_preserves_trailing_patent_citation_when_earlier_match_only_appears_inside_url():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        prepared = service._prepare_response_messages(
            [
                {
                    "message_id": "msg_1",
                    "role": "assistant",
                    "content": "原始链接：https://example.com/?id=CN109192948B (CN109192948B)。",
                    "created_at": service._now_iso(),
                    "status": "done",
                    "metadata": {
                        "trace_id": "trace-patent-citation-history-url-preservation",
                        "query_mode": "patent_kb_qa",
                        "route": "kb_qa",
                    },
                }
            ]
        )

        assert len(prepared) == 1
        assert "https://example.com/?id=CN109192948B" in prepared[0]["content"]
        assert "(CN109192948B)" in prepared[0]["content"]


def test_conversation_repository_list_uses_stable_ordering():
    class _QueryCapturingConversationRepo(ConversationRepository):
        def __init__(self) -> None:
            self.queries: list[tuple[str, tuple]] = []
            self._conversation_columns_cache = None

        def _execute_query(self, query: str, params: tuple = ()):
            self.queries.append((query, params))
            return []

    repo = _QueryCapturingConversationRepo()

    repo.list_conversations(user_id=7, offset=20, limit=10)

    assert repo.queries
    query, params = repo.queries[0]
    assert "ORDER BY updated_at DESC, id DESC" in " ".join(query.split())
    assert params == (7, 10, 20)


def test_add_message_prefers_json_document_over_stale_detail_cache():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Cache Drift")
        conversation_id = int(created["data"]["conversation_id"])

        first = service.add_message(user_id=7, conversation_id=conversation_id, role="user", content="first")
        assert first["success"] is True

        stale_payload = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert stale_payload["success"] is True
        stale_payload["data"]["messages"] = []
        stale_payload["data"]["message_count"] = 0
        stale_payload["data"]["updated_at"] = created["data"]["updated_at"]
        cache_conversation_detail(
            redis_service=redis_service,
            user_id=7,
            conversation_id=conversation_id,
            payload=stale_payload,
        )

        second = service.add_message(user_id=7, conversation_id=conversation_id, role="assistant", content="second")
        assert second["success"] is True

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

        assert detail["success"] is True
        assert [item["content"] for item in detail["data"]["messages"]] == ["first", "second"]
        assert detail["data"]["message_count"] == 2


def test_add_message_does_not_bootstrap_from_stale_cache_when_json_is_missing():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Source Of Truth")
        conversation_id = int(created["data"]["conversation_id"])
        local_path = json_store.conversation_local_path(user_id=7, conversation_id=conversation_id)
        local_path.unlink()

        stale_payload = {
            "success": True,
            "data": {
                "conversation_id": conversation_id,
                "user_id": 7,
                "title": "Cached Ghost",
                "message_count": 1,
                "created_at": created["data"]["created_at"],
                "updated_at": created["data"]["updated_at"],
                "messages": [
                    {
                        "id": 99,
                        "message_id": "m_000099",
                        "role": "user",
                        "content": "ghost",
                        "metadata": {},
                        "created_at": created["data"]["updated_at"],
                    }
                ],
                "uploaded_files": [],
                "uploaded_files_all": [],
                "pdf_files": [],
                "excel_files": [],
            },
        }
        cache_conversation_detail(
            redis_service=redis_service,
            user_id=7,
            conversation_id=conversation_id,
            payload=stale_payload,
        )

        added = service.add_message(user_id=7, conversation_id=conversation_id, role="user", content="real")
        assert added["success"] is True

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

        assert detail["success"] is True
        assert detail["data"]["title"] == "Source Of Truth"
        assert [item["content"] for item in detail["data"]["messages"]] == ["real"]
        assert detail["data"]["message_count"] == 1



def test_authority_user_write_is_immediately_visible_to_context_snapshot():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Snapshot")
        conversation_id = int(created["data"]["conversation_id"])

        written = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-1",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key="conv-1:trace-1:user",
            content="hello authority",
            context_hints={"selected_file_ids": [11], "last_turn_route_hint": "kb_qa"},
        )

        assert written["success"] is True
        assert written["deduped"] is False
        assert written["message_id"] == "m_000001"

        snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)

        assert snapshot["success"] is True
        assert snapshot["data"]["conversation_id"] == conversation_id
        assert snapshot["data"]["user_id"] == 7
        assert snapshot["data"]["snapshot_version"] >= 2
        assert snapshot["data"]["summary"] == {
            "short_summary": "主题：hello authority",
            "memory_facts": [],
            "open_threads": ["hello authority"],
        }
        assert snapshot["data"]["recent_turns"] == [
            {
                "message_id": "m_000001",
                "role": "user",
                "content": "hello authority",
                "created_at": written["created_at"],
                "trace_id": "trace-1",
                "status": "done",
                "terminal_status": "done",
            }
        ]
        assert snapshot["data"]["conversation_state"] == {
            "last_turn_route": "",
            "last_focus_file_ids": [],
            "last_assistant_trace_id": "",
        }


def test_authority_user_write_dedupes_same_idempotency_key():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Dedupe")
        conversation_id = int(created["data"]["conversation_id"])

        first = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-2",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key="conv-1:trace-2:user",
            content="same message",
            context_hints={},
        )
        second = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-2",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key="conv-1:trace-2:user",
            content="same message",
            context_hints={},
        )

        assert first["success"] is True
        assert first["deduped"] is False
        assert second["success"] is True
        assert second["deduped"] is True
        assert second["message_id"] == first["message_id"]

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assert [item["content"] for item in detail["data"]["messages"]] == ["same message"]
        assert detail["data"]["message_count"] == 1

        snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)
        assert snapshot["success"] is True
        assert len(snapshot["data"]["recent_turns"]) == 1


def test_authority_user_write_accepts_patentqa_source_service():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Patent User Write")
        conversation_id = int(created["data"]["conversation_id"])

        written = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-patent-user",
            source_service="patentQA",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            idempotency_key=f"{conversation_id}:trace-patent-user:user",
            content="Explain the claim scope.",
            context_hints={},
        )

        assert written["success"] is True
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assert [item["content"] for item in detail["data"]["messages"]] == ["Explain the claim scope."]


def test_authority_assistant_async_accepts_patentqa_source_service():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Patent Assistant Write")
        conversation_id = int(created["data"]["conversation_id"])

        accepted = service.accept_authority_assistant_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-patent-assistant",
            source_service="patentQA",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            idempotency_key=f"{conversation_id}:trace-patent-assistant:assistant",
            final_event={
                "done_seen": True,
                "answer_text": "Patent answer.",
                "steps": [],
                "references": [],
                "used_files": [],
                "timings": {},
            },
        )

        assert accepted["success"] is True
        assert accepted["accepted"] is True
        task = repo.get_authority_assistant_task(task_id=int(accepted["task_id"]))
        assert task is not None
        metadata = task["metadata"]
        assert metadata["source_service"] == "patentQA"
        assert metadata["requested_mode"] == "patent"
        assert metadata["actual_mode"] == "patent"


def test_patent_authority_assistant_async_rejects_stale_runtime_owner(monkeypatch):
    monkeypatch.setenv("PATENT_ENV", "test")
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    fake_redis = _FakeRedis()
    redis_service = RedisService.from_prefix(client=fake_redis, key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Stale Patent Owner")
        conversation_id = int(created["data"]["conversation_id"])
        fake_redis.set(f"patent:test:exec:turn:{conversation_id}:trace-stale", "owner-2")
        fake_redis.set(f"patent:test:coord:inflight:{conversation_id}:trace-stale", "owner-2")

        accepted = service.accept_authority_assistant_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-stale",
            source_service="patentQA",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            idempotency_key=f"{conversation_id}:trace-stale:assistant",
            runtime_owner_token="owner-1",
            final_event={
                "done_seen": True,
                "answer_text": "Stale answer.",
                "steps": [],
                "references": [],
                "used_files": [],
                "timings": {},
            },
        )

        assert accepted["success"] is False
        assert accepted["code"] == "SERVICE_NOT_READY"
        assert repo.assistant_tasks == {}


def test_patent_runtime_owner_check_defaults_to_patent_dev_env_when_public_app_env_differs(monkeypatch):
    monkeypatch.delenv("PATENT_ENV", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    fake_redis = _FakeRedis()
    redis_service = RedisService.from_prefix(client=fake_redis, key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Patent Owner Env")
        conversation_id = int(created["data"]["conversation_id"])
        fake_redis.set(f"patent:dev:exec:turn:{conversation_id}:trace-dev-owner", "owner-1")
        fake_redis.set(f"patent:dev:coord:inflight:{conversation_id}:trace-dev-owner", "owner-1")

        accepted = service.accept_authority_assistant_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-dev-owner",
            source_service="patentQA",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            idempotency_key=f"{conversation_id}:trace-dev-owner:assistant",
            runtime_owner_token="owner-1",
            final_event={
                "done_seen": True,
                "answer_text": "Owned answer.",
                "steps": [],
                "references": [],
                "used_files": [],
                "timings": {},
            },
        )

        assert accepted["success"] is True
        assert accepted["accepted"] is True


def test_patent_authority_terminal_async_rejects_stale_runtime_owner(monkeypatch):
    monkeypatch.setenv("PATENT_ENV", "test")
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    fake_redis = _FakeRedis()
    redis_service = RedisService.from_prefix(client=fake_redis, key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Stale Patent Terminal Owner")
        conversation_id = int(created["data"]["conversation_id"])
        fake_redis.set(f"patent:test:exec:turn:{conversation_id}:trace-terminal-stale", "owner-2")
        fake_redis.set(f"patent:test:coord:inflight:{conversation_id}:trace-terminal-stale", "owner-2")

        accepted = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-stale",
            source_service="patentQA",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            idempotency_key=f"{conversation_id}:trace-terminal-stale:assistant",
            runtime_owner_token="owner-1",
            terminal_event={
                "terminal_status": "canceled",
                "done_seen": False,
                "answer_text": "",
                "steps": [],
                "references": [],
                "used_files": [],
                "timings": {},
                "failure": {"message": "cancelled", "retriable": False},
            },
        )

        assert accepted["success"] is False
        assert accepted["code"] == "SERVICE_NOT_READY"
        assert repo.assistant_tasks == {}


def test_authority_context_snapshot_uses_last_assistant_state():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority State")
        conversation_id = int(created["data"]["conversation_id"])

        user_added = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-3-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key="conv-1:trace-3:user",
            content="question",
            context_hints={},
        )
        assert user_added["success"] is True

        assistant_added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="answer",
            metadata={
                "trace_id": "trace-3-assistant",
                "route": "hybrid_qa",
                "used_files": [{"file_id": 5}, {"file_id": 9}],
            },
        )
        assert assistant_added["success"] is True

        snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)

        assert snapshot["success"] is True
        assert [item["role"] for item in snapshot["data"]["recent_turns"]] == ["user", "assistant"]
        assert snapshot["data"]["conversation_state"] == {
            "last_turn_route": "hybrid_qa",
            "last_focus_file_ids": [5, 9],
            "last_assistant_trace_id": "trace-3-assistant",
        }


def test_authority_context_snapshot_keeps_failed_turn_truth_but_summary_stays_open():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Failed Truth")
        conversation_id = int(created["data"]["conversation_id"])
        user_added = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-failed-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-failed-user:user",
            content="why did this fail?",
            context_hints={},
        )
        assert user_added["success"] is True

        assistant_added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="partial answer",
            metadata={
                "trace_id": "trace-failed-assistant",
                "route": "kb_qa",
                "terminal_status": "failed",
                "failure_stage": "llm_stream",
                "failure_message": "timeout",
                "retriable": True,
                "done_seen": False,
            },
        )
        assert assistant_added["success"] is True

        snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)

        assert snapshot["success"] is True
        assert snapshot["data"]["recent_turns"] == [
            {
                "message_id": "m_000001",
                "role": "user",
                "content": "why did this fail?",
                "created_at": user_added["created_at"],
                "trace_id": "trace-failed-user",
                "status": "done",
                "terminal_status": "done",
            },
            {
                "message_id": "m_000002",
                "role": "assistant",
                "content": "partial answer",
                "created_at": snapshot["data"]["recent_turns"][1]["created_at"],
                "trace_id": "trace-failed-assistant",
                "status": "failed",
                "terminal_status": "failed",
                "failure_stage": "llm_stream",
                "failure_message": "timeout",
                "retriable": True,
            },
        ]
        assert snapshot["data"]["summary"]["memory_facts"] == []
        assert snapshot["data"]["summary"]["open_threads"] == ["why did this fail?"]


def test_authority_context_snapshot_filters_non_final_messages():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Contract")
        conversation_id = int(created["data"]["conversation_id"])

        user_added = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-contract-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-contract-user:user",
            content="Need the final answer only.",
            context_hints={},
        )
        assert user_added["success"] is True

        assistant_added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="Final answer only.",
            metadata={
                "trace_id": "trace-contract-assistant",
                "route": "kb_qa",
                "used_files": [{"file_id": 21}],
                "steps": [{"stage": "retrieve"}],
                "timings": {"latency_ms": 20},
            },
        )
        assert assistant_added["success"] is True

        with service._json_store.conversation_lock(user_id=7, conversation_id=conversation_id):
            document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)
            messages = document.get("messages") if isinstance(document.get("messages"), list) else []
            messages.append(
                {
                    "message_id": "m_999999",
                    "role": "system",
                    "content": "debug trace should stay out of recent turns",
                    "created_at": user_added["created_at"],
                    "metadata": {
                        "trace_id": "trace-internal",
                        "steps": [{"stage": "debug"}],
                        "timings": {"latency_ms": 1},
                    },
                }
            )
            document["messages"] = messages
            service._json_store.write_document(user_id=7, conversation_id=conversation_id, document=document)

        snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)

        assert snapshot["success"] is True
        assert [item["role"] for item in snapshot["data"]["recent_turns"]] == ["user", "assistant"]
        assert [item["content"] for item in snapshot["data"]["recent_turns"]] == [
            "Need the final answer only.",
            "Final answer only.",
        ]
        assert snapshot["data"]["conversation_state"] == {
            "last_turn_route": "kb_qa",
            "last_focus_file_ids": [21],
            "last_assistant_trace_id": "trace-contract-assistant",
        }



def test_authority_context_snapshot_builds_minimal_summary_from_recent_turns():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Summary")
        conversation_id = int(created["data"]["conversation_id"])

        user_added = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-summary-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-summary-user:user",
            content="Summarize the conversation.",
            context_hints={},
        )
        assert user_added["success"] is True

        assistant_added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="The conversation is about catalyst stability over 48 hours.",
            metadata={
                "trace_id": "trace-summary-assistant",
                "route": "kb_qa",
                "steps": [{"stage": "answer"}],
                "timings": {"latency_ms": 66},
            },
        )
        assert assistant_added["success"] is True

        snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)

        assert snapshot["success"] is True
        assert snapshot["data"]["summary"] == {
            "short_summary": (
                "主题：Summarize the conversation.；最新结论："
                "The conversation is about catalyst stability over 48 hours."
            ),
            "memory_facts": [
                "The conversation is about catalyst stability over 48 hours.",
            ],
            "open_threads": [],
        }



def test_authority_context_snapshot_tracks_latest_open_thread_and_recent_facts():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Open Thread")
        conversation_id = int(created["data"]["conversation_id"])

        user_added = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-thread-user-1",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-thread-user-1:user",
            content="请总结厚电极在高倍率下的问题。",
            context_hints={},
        )
        assert user_added["success"] is True

        assistant_added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="厚电极在高倍率下更容易出现液相浓差极化。",
            metadata={"trace_id": "trace-thread-assistant", "route": "kb_qa"},
        )
        assert assistant_added["success"] is True

        followup_added = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-thread-user-2",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-thread-user-2:user",
            content="那对倍率性能的直接影响是什么？",
            context_hints={},
        )
        assert followup_added["success"] is True

        snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)

        assert snapshot["success"] is True
        assert snapshot["data"]["summary"] == {
            "short_summary": (
                "主题：请总结厚电极在高倍率下的问题。；当前问题：那对倍率性能的直接影响是什么？"
            ),
            "memory_facts": ["厚电极在高倍率下更容易出现液相浓差极化。"],
            "open_threads": ["那对倍率性能的直接影响是什么？"],
        }


def test_authority_context_snapshot_rejects_wrong_owner():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Authority Ownership")
        conversation_id = int(created["data"]["conversation_id"])

        snapshot = service.get_conversation_context_snapshot(user_id=8, conversation_id=conversation_id)

        assert snapshot == {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

def test_conversation_detail_rejects_stale_cache_when_db_activity_is_newer():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Freshness")
        conversation_id = int(created["data"]["conversation_id"])
        added = service.add_message(user_id=7, conversation_id=conversation_id, role="user", content="fresh")
        assert added["success"] is True

        stale_payload = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert stale_payload["success"] is True
        stale_payload = copy.deepcopy(stale_payload)
        stale_payload["data"]["messages"] = []
        stale_payload["data"]["message_count"] = 0
        stale_payload["data"]["updated_at"] = created["data"]["updated_at"]
        cache_conversation_detail(
            redis_service=redis_service,
            user_id=7,
            conversation_id=conversation_id,
            payload=stale_payload,
        )

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

        assert detail["success"] is True
        assert [item["content"] for item in detail["data"]["messages"]] == ["fresh"]
        assert detail["data"]["message_count"] == 1


def test_repository_updates_activity_timestamp_on_user_visible_writes():
    class _UpdateCapturingConversationRepo(ConversationRepository):
        def __init__(self) -> None:
            self.queries: list[tuple[str, tuple]] = []
            self._conversation_columns_cache = None

        def _execute_update(self, query: str, params: tuple = ()) -> int:
            self.queries.append((" ".join(query.split()), params))
            return 1

    repo = _UpdateCapturingConversationRepo()

    repo.update_conversation_title(conversation_id=3, user_id=7, title="Renamed")
    repo.set_message_count(conversation_id=3, user_id=7, message_count=2)

    assert len(repo.queries) == 2
    assert "SET title = %s, updated_at = %s" in repo.queries[0][0]
    assert repo.queries[0][1][0] == "Renamed"
    assert "SET message_count = %s, updated_at = %s" in repo.queries[1][0]
    assert repo.queries[1][1][0] == 2


def test_conversation_service_formats_naive_datetimes_as_beijing_time():
    service = ConversationService(repo=_MemoryConversationRepo())

    formatted = service._to_iso(datetime(2026, 3, 21, 9, 30, 45), fallback="unused")

    assert formatted == "2026-03-21T09:30:45+08:00"


def test_conversation_service_list_payload_uses_beijing_time_for_naive_rows():
    repo = _MemoryConversationRepo()
    conversation_id = repo.create_conversation(user_id=7, title="Beijing")
    repo.conversations[conversation_id]["created_at"] = datetime(2026, 3, 21, 9, 30, 45)
    repo.conversations[conversation_id]["updated_at"] = datetime(2026, 3, 21, 11, 5, 6)
    service = ConversationService(repo=repo)

    payload = service.list_conversations(user_id=7, page=1, page_size=20)

    assert payload["success"] is True
    item = payload["data"]["conversations"][0]
    assert item["created_at"] == "2026-03-21T09:30:45+08:00"
    assert item["updated_at"] == "2026-03-21T11:05:06+08:00"


def test_repository_writes_beijing_aware_timestamps_for_user_visible_updates():
    class _UpdateCapturingConversationRepo(ConversationRepository):
        def __init__(self) -> None:
            self.queries: list[tuple[str, tuple]] = []
            self._conversation_columns_cache = None

        def _execute_update(self, query: str, params: tuple = ()) -> int:
            self.queries.append((" ".join(query.split()), params))
            return 1

    repo = _UpdateCapturingConversationRepo()

    repo.update_conversation_title(conversation_id=3, user_id=7, title="Renamed")
    repo.set_message_count(conversation_id=3, user_id=7, message_count=2)

    beijing = timezone(timedelta(hours=8))
    for _, params in repo.queries:
        timestamp = params[1]
        assert isinstance(timestamp, datetime)
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() == beijing.utcoffset(None)


def test_repository_writes_beijing_aware_timestamps_for_inserted_entities():
    class _InsertCapturingConversationRepo(ConversationRepository):
        def __init__(self) -> None:
            self.queries: list[tuple[str, tuple]] = []
            self._conversation_columns_cache = None

        def _execute_update(self, query: str, params: tuple = ()) -> int:
            self.queries.append((" ".join(query.split()), params))
            return 1

    repo = _InsertCapturingConversationRepo()

    repo.create_conversation(user_id=7, title="Alpha")
    repo.add_message(conversation_id=3, user_id=7, role="user", content="hello", metadata={})
    repo.add_uploaded_file(
        conversation_id=3,
        user_id=7,
        file_type="pdf",
        file_name="a.pdf",
        local_path="/tmp/a.pdf",
        storage_ref=None,
        content_type="application/pdf",
        size_bytes=123,
    )

    assert "INSERT INTO conversations (user_id, title, message_count, created_at, updated_at)" in repo.queries[0][0]
    assert "INSERT INTO conversation_messages (conversation_id, user_id, role, content, metadata_json, created_at)" in repo.queries[1][0]
    assert "UPDATE conversations SET message_count = message_count + 1, updated_at = %s" in repo.queries[2][0]
    assert "INSERT INTO conversation_files ( conversation_id, user_id, file_type, file_name, local_path, storage_ref, content_type, size_bytes, created_at )".replace("  ", " ")[:70] in repo.queries[3][0]

    beijing = timezone(timedelta(hours=8))
    timestamp_params = [repo.queries[0][1][2], repo.queries[0][1][3], repo.queries[1][1][5], repo.queries[2][1][0], repo.queries[3][1][8]]
    for timestamp in timestamp_params:
        assert isinstance(timestamp, datetime)
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() == beijing.utcoffset(None)


def test_conversation_detail_does_not_fallback_to_legacy_tables_by_default(monkeypatch):
    monkeypatch.delenv("PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK", raising=False)
    get_settings.cache_clear()
    repo = _TrackingConversationRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend, redis_service=redis_service)
        conversation_id = repo.create_conversation(user_id=7, title="Legacy Only")
        repo.messages[conversation_id].append(
            {
                "id": 1,
                "conversation_id": conversation_id,
                "user_id": 7,
                "role": "user",
                "content": "legacy-message",
                "metadata": {"source": "legacy"},
                "created_at": datetime.now(),
            }
        )
        repo.files[conversation_id].append(
            {
                "id": 1,
                "conversation_id": conversation_id,
                "user_id": 7,
                "file_type": "pdf",
                "file_name": "legacy.pdf",
                "local_path": str(Path(tempdir) / "legacy.pdf"),
                "storage_ref": None,
                "content_type": "application/pdf",
                "size_bytes": 3,
                "created_at": datetime.now(),
            }
        )
        service = ConversationService(repo=repo, json_store=json_store, workspace_root=tempdir, redis_service=redis_service)

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        files = service.list_uploaded_files(user_id=7, conversation_id=conversation_id, include_deleted=False)
        file_item = service.get_uploaded_file(user_id=7, conversation_id=conversation_id, file_id=1)

        assert detail["success"] is True
        assert detail["data"]["messages"] == []
        assert detail["data"]["uploaded_files"] == []
        assert files == {"success": True, "data": {"files": []}}
        assert file_item["success"] is False
        assert file_item["code"] == "NOT_FOUND"
        assert repo.list_messages_calls == 0
        assert repo.list_uploaded_files_calls == 0
        assert repo.get_uploaded_file_calls == 0

    get_settings.cache_clear()


def test_conversation_detail_can_fallback_to_legacy_tables_when_enabled(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK", "1")
    get_settings.cache_clear()
    repo = _TrackingConversationRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend, redis_service=redis_service)
        legacy_file_path = Path(tempdir) / "legacy.pdf"
        legacy_file_path.write_bytes(b"pdf")
        conversation_id = repo.create_conversation(user_id=7, title="Legacy Only")
        repo.messages[conversation_id].append(
            {
                "id": 1,
                "conversation_id": conversation_id,
                "user_id": 7,
                "role": "user",
                "content": "legacy-message",
                "metadata": {"source": "legacy"},
                "created_at": datetime.now(),
            }
        )
        repo.files[conversation_id].append(
            {
                "id": 1,
                "conversation_id": conversation_id,
                "user_id": 7,
                "file_type": "pdf",
                "file_name": "legacy.pdf",
                "local_path": str(legacy_file_path),
                "storage_ref": None,
                "content_type": "application/pdf",
                "size_bytes": 3,
                "created_at": datetime.now(),
            }
        )
        service = ConversationService(repo=repo, json_store=json_store, workspace_root=tempdir, redis_service=redis_service)

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        files = service.list_uploaded_files(user_id=7, conversation_id=conversation_id, include_deleted=False)
        file_item = service.get_uploaded_file(user_id=7, conversation_id=conversation_id, file_id=1)

        assert detail["success"] is True
        assert len(detail["data"]["messages"]) == 1
        assert detail["data"]["messages"][0]["content"] == "legacy-message"
        assert len(detail["data"]["uploaded_files"]) == 1
        assert detail["data"]["uploaded_files"][0]["file_name"] == "legacy.pdf"
        assert files["success"] is True
        assert len(files["data"]["files"]) == 1
        assert file_item["success"] is True
        assert file_item["data"]["file_name"] == "legacy.pdf"
        assert repo.list_messages_calls == 1
        assert repo.list_uploaded_files_calls == 1
        assert repo.get_uploaded_file_calls == 0

    get_settings.cache_clear()


def test_conversation_service_enqueues_outbox_when_json_mirror_fails():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()

    with TemporaryDirectory() as tempdir:
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=_FailingStorageBackend(),
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
        )

        created = service.create_conversation(user_id=9, title="MirrorFail")
        assert created["success"] is True
        assert len(outbox.calls) == 1
        assert outbox.calls[0]["conversation_id"] == created["data"]["conversation_id"]


def test_conversation_json_version_stays_monotonic_across_multiple_writes():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
        )

        created = service.create_conversation(user_id=7, title="Versioned")
        conversation_id = int(created["data"]["conversation_id"])
        version_after_create = int(repo.get_conversation(conversation_id=conversation_id, user_id=7)["chat_json_version"])
        assert version_after_create == 1

        added_user = service.add_message(user_id=7, conversation_id=conversation_id, role="user", content="hello")
        assert added_user["success"] is True
        version_after_message = int(repo.get_conversation(conversation_id=conversation_id, user_id=7)["chat_json_version"])
        assert version_after_message == 2

        file_path = Path(tempdir) / "sample.pdf"
        file_path.write_bytes(b"pdf")
        added_file = service.add_uploaded_file(
            user_id=7,
            conversation_id=conversation_id,
            file_type="pdf",
            file_name="sample.pdf",
            local_path=str(file_path),
            storage_ref=None,
            content_type="application/pdf",
            size_bytes=3,
        )
        assert added_file["success"] is True
        version_after_file = int(repo.get_conversation(conversation_id=conversation_id, user_id=7)["chat_json_version"])
        assert version_after_file == 3


def test_conversation_load_prefers_valid_local_json_over_stale_remote_copy():
    with TemporaryDirectory() as tempdir:
        project_root = Path(tempdir)
        local_path = project_root / "data" / "conversations" / "7" / "3.json"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text('{"meta":{"title":"Local"},"messages":[],"files":[],"runtime":{}}\n', encoding="utf-8")

        class _RemoteWouldOverwriteBackend(LocalStorageBackend):
            def __init__(self, *, root_dir: str) -> None:
                super().__init__(root_dir=root_dir)
                self.download_calls = 0

            def download_file(self, *, object_name: str, local_path: str) -> bool:
                self.download_calls += 1
                Path(local_path).write_text(
                    '{"meta":{"title":"Remote"},"messages":[{"content":"stale"}],"files":[],"runtime":{}}\n',
                    encoding="utf-8",
                )
                return True

        storage_backend = _RemoteWouldOverwriteBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)

        loaded = json_store.load_document(user_id=7, conversation_id=3)

        assert loaded is not None
        assert loaded["meta"]["title"] == "Local"
        assert storage_backend.download_calls == 0


def test_conversation_add_uploaded_file_rolls_back_row_and_json_on_persist_error():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
        )

        created = service.create_conversation(user_id=7, title="Rollback")
        conversation_id = int(created["data"]["conversation_id"])
        file_path = Path(tempdir) / "sample.pdf"
        file_path.write_bytes(b"pdf")

        original_persist = service._persist_document_and_index

        def _failing_persist(**kwargs):
            original_persist(**kwargs)
            raise RuntimeError("persist_failed_after_write")

        service._persist_document_and_index = _failing_persist  # type: ignore[method-assign]

        added_file = service.add_uploaded_file(
            user_id=7,
            conversation_id=conversation_id,
            file_type="pdf",
            file_name="sample.pdf",
            local_path=str(file_path),
            storage_ref=None,
            content_type="application/pdf",
            size_bytes=3,
        )

        assert added_file["success"] is False
        assert repo.list_uploaded_files(conversation_id=conversation_id, user_id=7) == []
        doc = json_store.load_document(user_id=7, conversation_id=conversation_id)
        assert isinstance(doc, dict)
        assert doc.get("files") == []


def test_upload_processing_worker_materializes_file_from_storage_ref_when_local_path_is_missing(tmp_path):
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    source_file = storage_root / "mirrored.pdf"
    source_file.write_bytes(b"pdf-data")

    class _ConversationService:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self._workspace_root = tmp_path

        def get_uploaded_file(self, **kwargs):
            _ = kwargs
            return {
                "success": True,
                "data": {
                    "id": 9,
                    "file_name": "sample.pdf",
                    "local_path": str(tmp_path / "missing.pdf"),
                    "storage_ref": f"local://{source_file}",
                },
            }

        def update_uploaded_file_processing_state(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {"success": True}

    parse_paths: list[Path] = []

    def _parse(path: Path, *_args, **_kwargs):
        parse_paths.append(Path(path))
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"pdf-data"
        return "parsed text"

    service = _ConversationService()
    worker = UploadProcessingWorker(
        conversation_service=service,
        extract_pdf_text_fn=_parse,
    )

    worker._run_task(
        user_id=7,
        conversation_id=3,
        file_id=9,
        file_type="pdf",
        local_path=str(tmp_path / "missing.pdf"),
    )

    assert len(parse_paths) == 1
    assert str(parse_paths[0]) != str(tmp_path / "missing.pdf")
    assert parse_paths[0] == source_file
    assert any(call.get("parse_status") == "parsing" for call in service.calls)
    assert any(call.get("index_status") == "ready" for call in service.calls)


def test_upload_processing_worker_treats_legacy_pdf_error_text_as_failure():
    worker = UploadProcessingWorker(
        conversation_service=object(),
        extract_pdf_text_fn=lambda *_args, **_kwargs: "[错误] pdf parse failed",
    )

    file_path = Path(__file__)

    try:
        worker._parse_pdf(file_path)
    except RuntimeError as exc:
        assert "[错误]" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for legacy pdf error text")


def test_upload_processing_worker_stops_before_parse_when_state_persist_fails():
    class _StateFailingConversationService:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def update_uploaded_file_processing_state(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {"success": False, "code": "DB_UNAVAILABLE"}

    service = _StateFailingConversationService()
    parse_called = {"value": False}
    worker = UploadProcessingWorker(
        conversation_service=service,
        extract_pdf_text_fn=lambda *_args, **_kwargs: parse_called.__setitem__("value", True) or "parsed text",
    )

    worker._run_task(
        user_id=7,
        conversation_id=3,
        file_id=9,
        file_type="pdf",
        local_path=str(Path(__file__)),
    )

    assert parse_called["value"] is False
    assert len(service.calls) == 1


def test_update_uploaded_file_processing_state_skips_primary_list_cache_refresh():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        service = ConversationService(
            repo=repo,
            json_store=ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend),
            outbox_repo=outbox,
            workspace_root=tempdir,
        )

        created = service.create_conversation(user_id=7, title="CacheTest")
        conversation_id = int(created["data"]["conversation_id"])
        file_path = Path(tempdir) / "sample.pdf"
        file_path.write_bytes(b"pdf")
        added_file = service.add_uploaded_file(
            user_id=7,
            conversation_id=conversation_id,
            file_type="pdf",
            file_name="sample.pdf",
            local_path=str(file_path),
            storage_ref=None,
            content_type="application/pdf",
            size_bytes=3,
        )
        file_id = int(added_file["data"]["file_id"])

        refreshed = {"list": 0, "detail": 0}
        service._refresh_primary_list_cache = lambda **kwargs: refreshed.__setitem__("list", refreshed["list"] + 1)  # type: ignore[method-assign]
        service._refresh_detail_cache = lambda **kwargs: refreshed.__setitem__("detail", refreshed["detail"] + 1)  # type: ignore[method-assign]

        result = service.update_uploaded_file_processing_state(
            user_id=7,
            conversation_id=conversation_id,
            file_id=file_id,
            parse_status="parsed",
            index_status="ready",
            processing_stage="ready",
        )

        assert result["success"] is True
        assert refreshed == {"list": 0, "detail": 1}


def test_conversation_json_store_uses_distributed_lock_across_instances(monkeypatch):
    monkeypatch.setenv("CONVERSATION_LOCK_TTL_SECONDS", "5")
    monkeypatch.setenv("CONVERSATION_LOCK_WAIT_SECONDS", "1")
    monkeypatch.setenv("CONVERSATION_LOCK_RETRY_INTERVAL_MS", "20")
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        first = ConversationJsonStore(project_root=tempdir, redis_service=redis_service)
        second = ConversationJsonStore(project_root=tempdir, redis_service=redis_service)
        results: dict[str, object] = {}
        ready = threading.Event()

        def _attempt_second_lock() -> None:
            ready.set()
            try:
                with second.conversation_lock(user_id=7, conversation_id=11):
                    results["acquired"] = True
            except Exception as exc:
                results["error"] = exc

        with first.conversation_lock(user_id=7, conversation_id=11):
            worker = threading.Thread(target=_attempt_second_lock, daemon=True)
            worker.start()
            assert ready.wait(timeout=1.0)
            worker.join(timeout=2.0)

        assert results.get("acquired") is not True
        assert isinstance(results.get("error"), TimeoutError)


def test_rename_and_add_message_keep_latest_title_under_lock_handoff():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    before_add_message_lock = threading.Event()
    allow_add_message_lock = threading.Event()

    class _BlockingConversationJsonStore(ConversationJsonStore):
        def __init__(self, *, project_root: str, storage_backend: LocalStorageBackend) -> None:
            super().__init__(project_root=project_root, storage_backend=storage_backend)
            self._blocked_once = False
            self.block_enabled = False

        def conversation_lock(self, *, user_id: int, conversation_id: int):
            base_context = super().conversation_lock(user_id=user_id, conversation_id=conversation_id)

            class _WrappedContext:
                def __enter__(_self):
                    if self.block_enabled and not self._blocked_once:
                        self._blocked_once = True
                        before_add_message_lock.set()
                        assert allow_add_message_lock.wait(timeout=1.0)
                    return base_context.__enter__()

                def __exit__(_self, exc_type, exc, tb):
                    return base_context.__exit__(exc_type, exc, tb)

            return _WrappedContext()

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = _BlockingConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
        )

        created = service.create_conversation(user_id=7, title="Old Title")
        conversation_id = int(created["data"]["conversation_id"])
        json_store.block_enabled = True
        result_holder: dict[str, dict] = {}

        def _run_add_message() -> None:
            result_holder["add_message"] = service.add_message(
                user_id=7,
                conversation_id=conversation_id,
                role="user",
                content="hello",
            )

        worker = threading.Thread(target=_run_add_message, daemon=True)
        worker.start()
        assert before_add_message_lock.wait(timeout=1.0)

        renamed = service.update_conversation_title(
            user_id=7,
            conversation_id=conversation_id,
            title="New Title",
        )
        assert renamed["success"] is True

        allow_add_message_lock.set()
        worker.join(timeout=2.0)

        assert result_holder["add_message"]["success"] is True
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assert detail["data"]["title"] == "New Title"


def test_outbox_worker_hash_mismatch_fails_closed_without_upload(tmp_path):
    local_path = tmp_path / "conversation.json"
    local_path.write_text('{"meta":{"title":"new"}}\n', encoding="utf-8")

    class _OutboxRepo:
        def __init__(self) -> None:
            self.retry_calls: list[dict] = []
            self.done_calls: list[dict] = []

        def touch_processing(self, *, task_id: int) -> int:
            _ = task_id
            return 1

        def mark_retry(self, **kwargs) -> int:
            self.retry_calls.append(dict(kwargs))
            return 1

        def mark_done(self, **kwargs) -> int:
            self.done_calls.append(dict(kwargs))
            return 1

        def mark_dead(self, **kwargs) -> int:
            raise AssertionError(f"unexpected dead: {kwargs}")

    class _ConversationRepo:
        def get_conversation(self, **kwargs):
            _ = kwargs
            return {"chat_json_version": 5}

        def mark_chat_json_sync_ok(self, **kwargs):
            raise AssertionError(f"should not update sync state: {kwargs}")

    class _StorageBackend:
        def __init__(self) -> None:
            self.upload_calls = 0

        def upload_file(self, **kwargs):
            self.upload_calls += 1
            return "minio://bucket/conversations/7/3.json"

    outbox_repo = _OutboxRepo()
    conversation_repo = _ConversationRepo()
    storage_backend = _StorageBackend()
    worker = ChatJsonOutboxWorker(
        outbox_repo=outbox_repo,
        conversation_repo=conversation_repo,
        storage_backend=storage_backend,
    )

    outcome = worker._process_task(
        {
            "id": 1,
            "conversation_id": 3,
            "user_id": 7,
            "json_version": 5,
            "local_path": str(local_path),
            "object_name": "conversations/7/3.json",
            "content_hash": "deadbeef",
            "attempt_count": 0,
        }
    )

    assert outcome == "retry"
    assert storage_backend.upload_calls == 0
    assert outbox_repo.done_calls == []
    assert outbox_repo.retry_calls
    assert outbox_repo.retry_calls[0]["last_error"] == "local_content_hash_mismatch"


def test_outbox_worker_heartbeats_during_slow_upload(tmp_path):
    local_path = tmp_path / "conversation.json"
    local_path.write_text('{"meta":{"title":"ok"}}\n', encoding="utf-8")

    class _OutboxRepo:
        def __init__(self) -> None:
            self.touch_calls = 0
            self.done_calls: list[dict] = []

        def touch_processing(self, *, task_id: int) -> int:
            assert task_id == 1
            self.touch_calls += 1
            return 1

        def mark_retry(self, **kwargs) -> int:
            raise AssertionError(f"unexpected retry: {kwargs}")

        def mark_done(self, **kwargs) -> int:
            self.done_calls.append(dict(kwargs))
            return 1

        def mark_dead(self, **kwargs) -> int:
            raise AssertionError(f"unexpected dead: {kwargs}")

    class _ConversationRepo:
        def get_conversation(self, **kwargs):
            _ = kwargs
            return {"chat_json_version": 5}

        def mark_chat_json_sync_ok(self, **kwargs):
            return 1

    class _StorageBackend:
        def upload_file(self, **kwargs):
            _ = kwargs
            time.sleep(1.15)
            return "minio://bucket/conversations/7/3.json"

    worker = ChatJsonOutboxWorker(
        outbox_repo=_OutboxRepo(),
        conversation_repo=_ConversationRepo(),
        storage_backend=_StorageBackend(),
        config=ChatJsonOutboxConfig(processing_timeout_seconds=1),
    )

    outcome = worker._process_task(
        {
            "id": 1,
            "conversation_id": 3,
            "user_id": 7,
            "json_version": 5,
            "local_path": str(local_path),
            "object_name": "conversations/7/3.json",
            "content_hash": "",
            "attempt_count": 0,
        }
    )

    assert outcome == "done"
    assert worker._outbox_repo.touch_calls >= 1
    assert worker._outbox_repo.done_calls[0]["note"] == "ok"


def test_upload_processing_worker_uses_distributed_file_lease():
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    parse_started = threading.Event()
    parse_calls = {"count": 0}

    class _ConversationService:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def update_uploaded_file_processing_state(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {"success": True}

    service = _ConversationService()

    def _parse(*_args, **_kwargs):
        parse_calls["count"] += 1
        parse_started.set()
        time.sleep(0.2)
        return "parsed text"

    worker_one = UploadProcessingWorker(
        conversation_service=service,
        extract_pdf_text_fn=_parse,
        redis_service=redis_service,
    )
    worker_two = UploadProcessingWorker(
        conversation_service=service,
        extract_pdf_text_fn=_parse,
        redis_service=redis_service,
    )

    thread_one = threading.Thread(
        target=worker_one._run_task,
        kwargs={
            "user_id": 7,
            "conversation_id": 3,
            "file_id": 9,
            "file_type": "pdf",
            "local_path": str(Path(__file__)),
        },
        daemon=True,
    )
    thread_two = threading.Thread(
        target=worker_two._run_task,
        kwargs={
            "user_id": 7,
            "conversation_id": 3,
            "file_id": 9,
            "file_type": "pdf",
            "local_path": str(Path(__file__)),
        },
        daemon=True,
    )

    thread_one.start()
    assert parse_started.wait(timeout=1.0)
    thread_two.start()
    thread_one.join(timeout=2.0)
    thread_two.join(timeout=2.0)

    assert parse_calls["count"] == 1


def test_recover_pending_upload_processing_tasks_resubmits_uploaded_files():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()

    class _Worker:
        enabled = True

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def submit(self, **kwargs):
            self.calls.append(dict(kwargs))
            return True

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        service = ConversationService(
            repo=repo,
            json_store=ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend),
            outbox_repo=outbox,
            workspace_root=tempdir,
        )

        created = service.create_conversation(user_id=7, title="Recover")
        conversation_id = int(created["data"]["conversation_id"])
        file_path = Path(tempdir) / "recover.pdf"
        file_path.write_bytes(b"pdf")
        added = service.add_uploaded_file(
            user_id=7,
            conversation_id=conversation_id,
            file_type="pdf",
            file_name="recover.pdf",
            local_path=str(file_path),
            storage_ref=None,
            content_type="application/pdf",
            size_bytes=3,
        )

        worker = _Worker()
        summary = service.recover_pending_upload_processing_tasks(worker=worker)

    assert summary["submitted"] == 1
    assert worker.calls[0]["file_id"] == int(added["data"]["file_id"])
    assert worker.calls[0]["conversation_id"] == conversation_id


def test_conversation_download_route_contracts(monkeypatch):
    monkeypatch.setattr(
        conversation_service_module.conversation_service,
        "resolve_uploaded_file_download",
        lambda **kwargs: (
            {"success": True, "data": {"id": kwargs["file_id"]}},
            200,
            {"mode": "redirect", "target": "https://example.com/file.pdf", "file_name": "file.pdf"},
        ),
    )
    redirect_response = conversation_api_module.download_conversation_file(
        1,
        2,
        AuthContext(user_id=7, role="user", username="alice"),
        None,
    )
    assert isinstance(redirect_response, RedirectResponse)
    assert redirect_response.status_code == 302

    with TemporaryDirectory() as tempdir:
        local_path = Path(tempdir) / "download.pdf"
        local_path.write_bytes(b"data")
        monkeypatch.setattr(
            conversation_service_module.conversation_service,
            "resolve_uploaded_file_download",
            lambda **kwargs: (
                {"success": True, "data": {"id": kwargs["file_id"]}},
                200,
                {"mode": "local_file", "target": str(local_path), "file_name": "download.pdf"},
            ),
        )
        file_response = conversation_api_module.download_conversation_file(
            1,
            2,
            AuthContext(user_id=7, role="user", username="alice"),
            None,
        )
        assert isinstance(file_response, FileResponse)

    monkeypatch.setattr(
        conversation_service_module.conversation_service,
        "resolve_uploaded_file_download",
        lambda **kwargs: ({"success": False, "code": "FILE_UNAVAILABLE"}, 404, None),
    )
    error_response = conversation_api_module.download_conversation_file(
        1,
        2,
        AuthContext(user_id=7, role="user", username="alice"),
        None,
    )
    assert isinstance(error_response, JSONResponse)
    assert error_response.status_code == 404


def test_resolve_uploaded_file_download_always_uses_minio_proxy(monkeypatch):
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(
            project_root=tempdir,
            storage_backend=storage_backend,
        )
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode"),
        )
        created = service.create_conversation(user_id=7, title="MinIO download")
        conversation_id = int(created["data"]["conversation_id"])
        remote_object = Path(tempdir) / "folder" / "remote.pdf"
        remote_object.parent.mkdir(parents=True, exist_ok=True)
        remote_object.write_bytes(b"pdf")
        monkeypatch.setattr(
            "app.modules.storage.service.get_storage_backend",
            lambda project_root=None: storage_backend,
        )
        added = service.add_uploaded_file(
            user_id=7,
            conversation_id=conversation_id,
            file_type="pdf",
            file_name="remote.pdf",
            local_path="",
            storage_ref="minio://bucket/folder/remote.pdf",
            content_type="application/pdf",
            size_bytes=3,
        )
        file_id = int(added["data"]["file_id"])
        monkeypatch.setenv("MINIO_USE_PROXY", "0")

        payload, status_code, download = service.resolve_uploaded_file_download(
            user_id=7,
            conversation_id=conversation_id,
            file_id=file_id,
        )

    assert payload["success"] is True
    assert status_code == 200
    assert download["mode"] == "proxy_file"
    assert Path(download["target"]).exists()


def test_conversation_download_route_soft_warns_when_quota_finalize_fails(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(
        conversation_service_module.conversation_service,
        "resolve_uploaded_file_download",
        lambda **kwargs: (
            {"success": True},
            200,
            {"mode": "local_file", "target": str(file_path), "file_name": "sample.pdf"},
        ),
    )
    monkeypatch.setattr(
        quota_service_module.quota_service,
        "increment_quota",
        lambda **kwargs: {"success": False, "error": "redis_down"},
    )

    response = conversation_api_module.download_conversation_file(
        1,
        2,
        AuthContext(user_id=7, role="user", username="alice"),
        quota_deps.QuotaGrant(user_id=7, quota_type="file_view", checked={"config_active": True}),
    )

    assert isinstance(response, FileResponse)
    assert response.status_code == 200
    assert response.headers["x-quota-counted"] == "false"
    assert response.headers["x-quota-warning"] == "redis_down"
