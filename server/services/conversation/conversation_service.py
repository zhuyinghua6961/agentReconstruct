"""Conversation business service."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from server.database.connection import DatabaseConfigError, DatabaseConnectionError
from server.repositories.conversation_outbox_repository import ConversationOutboxRepository
from server.repositories.conversation_repository import ConversationRepository
from server.services.conversation.chat_json_store import ConversationJsonStore
from server.services.conversation.conversation_summary_service import build_conversation_summary


class ConversationService:
    """Use-cases for conversation CRUD and message append."""

    def __init__(
        self,
        *,
        repo: ConversationRepository | None = None,
        json_store: ConversationJsonStore | None = None,
        outbox_repo: ConversationOutboxRepository | None = None,
    ):
        self._repo = repo or ConversationRepository()
        self._json_store = json_store or ConversationJsonStore()
        self._outbox_repo = outbox_repo or ConversationOutboxRepository()
        self._logger = logging.getLogger(__name__)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def _to_iso(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc).astimezone().isoformat(timespec="seconds")
            return value.astimezone().isoformat(timespec="seconds")
        text = str(value).strip()
        return text or fallback

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _message_numeric_id(self, message: dict[str, Any], default_value: int) -> int:
        raw = str(message.get("message_id") or "").strip()
        if raw.startswith("m_"):
            return self._safe_int(raw[2:], default=default_value)
        return self._safe_int(raw, default=default_value)

    def _normalize_json_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            db_id = self._safe_int(row.get("id"), default=idx)
            payload: dict[str, Any] = {
                "message_id": f"m_{db_id:06d}",
                "role": str(row.get("role") or "assistant"),
                "content": str(row.get("content") or ""),
                "created_at": self._to_iso(row.get("created_at"), fallback=self._now_iso()),
                "status": "done",
                "metadata": metadata,
            }
            if metadata.get("query_mode"):
                payload["query_mode"] = metadata.get("query_mode")
            if isinstance(metadata.get("references"), list):
                payload["references"] = metadata.get("references")
            if isinstance(metadata.get("steps"), list):
                payload["steps"] = metadata.get("steps")
            if "done_seen" in metadata:
                payload["done_seen"] = bool(metadata.get("done_seen"))
            items.append(payload)
        return items

    def _normalize_json_files(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            file_id = self._safe_int(row.get("id") or row.get("file_id"), default=0)
            file_no = self._safe_int(row.get("file_no"), default=idx)
            status = str(row.get("file_status") or "active").strip().lower()
            if status not in {"active", "deleted"}:
                status = "active"
            items.append(
                {
                    "file_no": file_no,
                    "file_id": file_id,
                    "file_type": str(row.get("file_type") or ""),
                    "file_name": str(row.get("file_name") or ""),
                    "local_path": str(row.get("local_path") or ""),
                    "storage_ref": str(row.get("storage_ref") or ""),
                    "content_type": str(row.get("content_type") or ""),
                    "size_bytes": self._safe_int(row.get("size_bytes"), default=0),
                    "uploaded_at": self._to_iso(
                        row.get("uploaded_at") or row.get("created_at"),
                        fallback=self._now_iso(),
                    ),
                    "file_status": status,
                    "deleted_at": row.get("deleted_at"),
                    "deleted_by": row.get("deleted_by"),
                }
            )
        items.sort(key=lambda item: (self._safe_int(item.get("file_no"), 0), self._safe_int(item.get("file_id"), 0)))
        return items

    def _prepare_response_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(messages, start=1):
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if item.get("query_mode") and not metadata.get("query_mode"):
                metadata["query_mode"] = item.get("query_mode")
            if isinstance(item.get("references"), list) and "references" not in metadata:
                metadata["references"] = item.get("references")
            if isinstance(item.get("steps"), list) and "steps" not in metadata:
                metadata["steps"] = item.get("steps")
            if "done_seen" in item and "done_seen" not in metadata:
                metadata["done_seen"] = bool(item.get("done_seen"))
            rows.append(
                {
                    "id": self._message_numeric_id(item, idx),
                    "role": str(item.get("role") or "assistant"),
                    "content": str(item.get("content") or ""),
                    "metadata": metadata,
                    "created_at": self._to_iso(item.get("created_at"), fallback=self._now_iso()),
                }
            )
        return rows

    def _message_signature(self, row: dict[str, Any]) -> str:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        payload = {
            "role": str(row.get("role") or ""),
            "content": str(row.get("content") or ""),
            "metadata": metadata,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _document_summary(self, document: dict[str, Any]) -> dict[str, Any]:
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        summary = meta.get("summary")
        return dict(summary) if isinstance(summary, dict) else {}

    def _set_document_summary(self, *, document: dict[str, Any], summary: dict[str, Any]) -> None:
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        if summary:
            meta["summary"] = dict(summary)
        else:
            meta.pop("summary", None)
        document["meta"] = meta

    def _file_signature(self, row: dict[str, Any]) -> str:
        payload = {
            "file_type": str(row.get("file_type") or ""),
            "file_name": str(row.get("file_name") or ""),
            "local_path": str(row.get("local_path") or ""),
            "storage_ref": str(row.get("storage_ref") or ""),
            "content_type": str(row.get("content_type") or ""),
            "size_bytes": self._safe_int(row.get("size_bytes"), default=0),
            "created_at": self._to_iso(
                row.get("uploaded_at") or row.get("created_at"),
                fallback="",
            ),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _document_rows_for_backfill(self, document: dict[str, Any]) -> list[dict[str, Any]]:
        messages = document.get("messages") if isinstance(document.get("messages"), list) else []
        return self._prepare_response_messages(messages)

    def _document_file_rows(self, *, document: dict[str, Any], conversation_id: int, user_id: int) -> list[dict[str, Any]]:
        files = document.get("files") if isinstance(document.get("files"), list) else []
        return self._prepare_response_files(
            files=files,
            conversation_id=conversation_id,
            user_id=user_id,
            only_active=False,
        )

    def _backfill_db_messages_from_document(
        self,
        *,
        conversation_id: int,
        user_id: int,
        document: dict[str, Any],
        db_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        doc_rows = self._document_rows_for_backfill(document)
        if not doc_rows:
            return db_rows, False

        if db_rows:
            db_prefix = [self._message_signature(item) for item in db_rows]
            doc_prefix = [self._message_signature(item) for item in doc_rows[: len(db_rows)]]
            if db_prefix != doc_prefix:
                return db_rows, False

        if len(doc_rows) <= len(db_rows):
            return db_rows, False

        for item in doc_rows[len(db_rows) :]:
            self._repo.add_message_with_created_at(
                conversation_id=conversation_id,
                user_id=user_id,
                role=str(item.get("role") or "assistant"),
                content=str(item.get("content") or ""),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                created_at=item.get("created_at"),
            )

        return self._repo.list_messages(conversation_id=conversation_id, user_id=user_id), True

    def _reconcile_document_messages_with_db(
        self,
        *,
        row: dict[str, Any],
        conversation_id: int,
        user_id: int,
        document: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        db_rows = self._repo.list_messages(conversation_id=conversation_id, user_id=user_id)
        db_rows, backfilled = self._backfill_db_messages_from_document(
            conversation_id=conversation_id,
            user_id=user_id,
            document=document,
            db_rows=db_rows,
        )
        if not db_rows:
            return document, backfilled

        normalized_db_messages = self._normalize_json_messages(db_rows)
        current_messages = document.get("messages") if isinstance(document.get("messages"), list) else []
        current_rows = self._document_rows_for_backfill(document)
        db_signatures = [self._message_signature(item) for item in db_rows]
        current_signatures = [self._message_signature(item) for item in current_rows]
        if not backfilled and len(normalized_db_messages) == len(current_messages) and db_signatures == current_signatures:
            return document, backfilled
        if len(normalized_db_messages) < len(current_messages) and not backfilled:
            return document, False

        document["messages"] = normalized_db_messages
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        created_at = self._to_iso(row.get("created_at"), fallback=meta.get("created_at") or self._now_iso())
        last_message_at = normalized_db_messages[-1].get("created_at") if normalized_db_messages else None
        meta["title"] = str(row.get("title") or meta.get("title") or "New Conversation")
        meta["created_at"] = created_at
        meta["updated_at"] = last_message_at or self._to_iso(row.get("updated_at"), fallback=created_at)
        meta["message_count"] = len(normalized_db_messages)
        meta["last_message_at"] = last_message_at
        document["meta"] = meta
        return document, True

    def _reconcile_document_files_with_db(
        self,
        *,
        row: dict[str, Any],
        conversation_id: int,
        user_id: int,
        document: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        db_rows = self._repo.list_uploaded_files(conversation_id=conversation_id, user_id=user_id)
        current_rows = self._document_file_rows(
            document=document,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        current_active = [item for item in current_rows if str(item.get("file_status") or "active") == "active"]
        current_deleted = [item for item in current_rows if str(item.get("file_status") or "") == "deleted"]

        if current_active:
            db_signatures = [self._file_signature(item) for item in db_rows]
            doc_prefix = [self._file_signature(item) for item in current_active[: len(db_rows)]]
            if (not db_rows) or db_signatures == doc_prefix:
                backfilled = False
                if len(current_active) > len(db_rows):
                    for item in current_active[len(db_rows) :]:
                        self._repo.add_uploaded_file_with_created_at(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            file_type=str(item.get("file_type") or ""),
                            file_name=str(item.get("file_name") or ""),
                            local_path=str(item.get("local_path") or "") or None,
                            storage_ref=str(item.get("storage_ref") or "") or None,
                            content_type=str(item.get("content_type") or "") or None,
                            size_bytes=self._safe_int(item.get("size_bytes"), default=0),
                            created_at=item.get("created_at"),
                        )
                    db_rows = self._repo.list_uploaded_files(conversation_id=conversation_id, user_id=user_id)
                    backfilled = True
            else:
                backfilled = False
        else:
            backfilled = False

        normalized_active = self._prepare_response_files(
            files=self._normalize_json_files(db_rows),
            conversation_id=conversation_id,
            user_id=user_id,
            only_active=True,
        )
        active_signatures = [self._file_signature(item) for item in normalized_active]
        current_active_signatures = [self._file_signature(item) for item in current_active]
        if not backfilled and active_signatures == current_active_signatures:
            return document, False

        merged_files = list(normalized_active)
        merged_files.extend(current_deleted)
        merged_files.sort(
            key=lambda item: (
                self._safe_int(item.get("file_no"), default=0),
                self._safe_int(item.get("id") or item.get("file_id"), default=0),
            )
        )
        document["files"] = [
            {
                "file_no": self._safe_int(item.get("file_no"), default=index),
                "file_id": self._safe_int(item.get("id") or item.get("file_id"), default=0),
                "file_type": str(item.get("file_type") or ""),
                "file_name": str(item.get("file_name") or ""),
                "local_path": str(item.get("local_path") or ""),
                "storage_ref": str(item.get("storage_ref") or ""),
                "content_type": str(item.get("content_type") or ""),
                "size_bytes": self._safe_int(item.get("size_bytes"), default=0),
                "uploaded_at": self._to_iso(item.get("created_at") or item.get("uploaded_at"), fallback=self._now_iso()),
                "file_status": str(item.get("file_status") or "active"),
                "deleted_at": item.get("deleted_at"),
                "deleted_by": item.get("deleted_by"),
            }
            for index, item in enumerate(merged_files, start=1)
        ]
        return document, True

    def _prepare_response_files(
        self,
        *,
        files: list[dict[str, Any]],
        conversation_id: int,
        user_id: int,
        only_active: bool,
    ) -> list[dict[str, Any]]:
        items = sorted(
            files,
            key=lambda item: (
                self._safe_int(item.get("file_no"), 0),
                self._safe_int(item.get("file_id"), 0),
            ),
        )
        result: list[dict[str, Any]] = []
        for item in items:
            status = str(item.get("file_status") or "active").strip().lower()
            if status not in {"active", "deleted"}:
                status = "active"
            if only_active and status != "active":
                continue
            file_id = self._safe_int(item.get("file_id"), default=0)
            result.append(
                {
                    "id": file_id,
                    "conversation_id": int(conversation_id),
                    "user_id": int(user_id),
                    "file_type": str(item.get("file_type") or ""),
                    "file_name": str(item.get("file_name") or ""),
                    "local_path": str(item.get("local_path") or ""),
                    "storage_ref": str(item.get("storage_ref") or ""),
                    "content_type": str(item.get("content_type") or ""),
                    "size_bytes": self._safe_int(item.get("size_bytes"), default=0),
                    "created_at": self._to_iso(item.get("uploaded_at"), fallback=self._now_iso()),
                    "file_no": self._safe_int(item.get("file_no"), default=0),
                    "file_status": status,
                    "deleted_at": item.get("deleted_at"),
                    "deleted_by": item.get("deleted_by"),
                }
            )
        return result

    def _load_or_bootstrap_document(
        self,
        *,
        row: dict[str, Any],
        conversation_id: int,
        user_id: int,
    ) -> tuple[dict[str, Any], bool]:
        doc = self._json_store.load_document(user_id=user_id, conversation_id=conversation_id)
        if doc is not None:
            return doc, False

        legacy_messages = self._repo.list_messages(conversation_id=conversation_id, user_id=user_id)
        legacy_files = self._repo.list_uploaded_files(conversation_id=conversation_id, user_id=user_id)
        normalized_messages = self._normalize_json_messages(legacy_messages)
        normalized_files = self._normalize_json_files(legacy_files)
        created_fallback = self._now_iso()
        created_at = self._to_iso(row.get("created_at"), fallback=created_fallback)
        updated_at = self._to_iso(row.get("updated_at"), fallback=created_at)
        doc = self._json_store.build_default_document(
            conversation_id=conversation_id,
            user_id=user_id,
            title=str(row.get("title") or "New Conversation"),
            created_at=created_at,
            updated_at=updated_at,
            message_count=len(normalized_messages),
            messages=normalized_messages,
            files=normalized_files,
        )
        return doc, True

    def _persist_document_and_index(
        self,
        *,
        row: dict[str, Any],
        user_id: int,
        conversation_id: int,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        storage_ref_hint = str(row.get("chat_json_storage_ref") or "")
        write_result = self._json_store.write_document(
            user_id=user_id,
            conversation_id=conversation_id,
            document=document,
            storage_ref_hint=storage_ref_hint,
        )
        next_version = self._safe_int(row.get("chat_json_version"), default=0) + 1
        self._repo.update_chat_json_index(
            conversation_id=conversation_id,
            user_id=user_id,
            local_path=write_result.get("local_path"),
            storage_ref=write_result.get("storage_ref"),
            content_hash=write_result.get("content_hash"),
            size_bytes=write_result.get("size_bytes"),
            version=next_version,
            sync_status=str(write_result.get("sync_status") or "sync_failed"),
            updated_at=datetime.now(),
        )
        sync_status = str(write_result.get("sync_status") or "sync_failed")
        if sync_status != "ok":
            try:
                local_path = str(write_result.get("local_path") or "").strip()
                if local_path:
                    self._outbox_repo.enqueue_task(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        json_version=next_version,
                        local_path=local_path,
                        object_name=self._json_store.conversation_object_name(
                            user_id=user_id,
                            conversation_id=conversation_id,
                        ),
                        content_hash=str(write_result.get("content_hash") or "") or None,
                        last_error=f"initial_sync_status={sync_status}",
                    )
            except Exception as exc:  # pragma: no cover
                self._logger.warning(
                    "conversation json outbox enqueue failed (conversation=%s, version=%s): %s",
                    conversation_id,
                    next_version,
                    exc,
                )
        return write_result

    def create_conversation(self, *, user_id: int, title: str | None = None) -> dict[str, Any]:
        final_title = (title or "").strip() or "New Conversation"
        try:
            conversation_id = self._repo.create_conversation(user_id=user_id, title=final_title[:255])
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                created_fallback = self._now_iso()
                created_at = self._to_iso(row.get("created_at"), fallback=created_fallback)
                updated_at = self._to_iso(row.get("updated_at"), fallback=created_at)
                doc = self._json_store.build_default_document(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=str(row.get("title") or final_title),
                    created_at=created_at,
                    updated_at=updated_at,
                    message_count=0,
                    messages=[],
                    files=[],
                )
                self._persist_document_and_index(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=doc,
                )

            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
            return {
                "success": True,
                "data": {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "title": (row or {}).get("title", final_title),
                    "message_count": int((row or {}).get("message_count", 0)),
                    "created_at": (row or {}).get("created_at"),
                    "updated_at": (row or {}).get("updated_at"),
                },
            }
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_CREATE_ERROR"}

    def list_conversations(self, *, user_id: int, page: int, page_size: int) -> dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = min(100, max(1, int(page_size or 20)))
        offset = (page - 1) * page_size
        try:
            items = self._repo.list_conversations(user_id=user_id, offset=offset, limit=page_size)
            total = self._repo.count_conversations(user_id=user_id)
            return {
                "success": True,
                "data": {
                    "conversations": [
                        {
                            "conversation_id": int(item["id"]),
                            "user_id": int(item["user_id"]),
                            "title": item["title"],
                            "message_count": int(item.get("message_count", 0)),
                            "created_at": item.get("created_at"),
                            "updated_at": item.get("updated_at"),
                        }
                        for item in items
                    ],
                    "total_count": total,
                    "page": page,
                    "page_size": page_size,
                },
            }
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_LIST_ERROR"}

    def get_conversation_detail(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, healed = self._reconcile_document_messages_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                doc, files_healed = self._reconcile_document_files_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if bootstrapped or healed or files_healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )
                    self._repo.set_message_count(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message_count=len(doc.get("messages") or []),
                    )

            messages = self._prepare_response_messages(doc.get("messages") or [])
            files_all = self._prepare_response_files(
                files=doc.get("files") or [],
                conversation_id=conversation_id,
                user_id=user_id,
                only_active=False,
            )
            uploaded_files = [item for item in files_all if str(item.get("file_status")) == "active"]

            if not uploaded_files:
                legacy_files = self._repo.list_uploaded_files(conversation_id=conversation_id, user_id=user_id)
                normalized_legacy_files = self._normalize_json_files(legacy_files)
                uploaded_files = self._prepare_response_files(
                    files=normalized_legacy_files,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    only_active=True,
                )
                files_all = self._prepare_response_files(
                    files=normalized_legacy_files,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    only_active=False,
                )

            pdf_files = [item for item in uploaded_files if str(item.get("file_type")) == "pdf"]
            excel_files = [item for item in uploaded_files if str(item.get("file_type")) == "excel"]
            message_count = self._safe_int((doc.get("meta") or {}).get("message_count"), default=len(messages))

            self._repo.set_message_count(
                conversation_id=conversation_id,
                user_id=user_id,
                message_count=message_count,
            )

            return {
                "success": True,
                "data": {
                    "conversation_id": int(row["id"]),
                    "user_id": int(row["user_id"]),
                    "title": row["title"],
                    "message_count": int(message_count),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "messages": messages,
                    "summary": self._document_summary(doc),
                    "uploaded_files": uploaded_files,
                    "uploaded_files_all": files_all,
                    "pdf_files": pdf_files,
                    "excel_files": excel_files,
                },
            }
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_FETCH_ERROR"}

    def get_conversation_summary(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, healed = self._reconcile_document_messages_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                doc, files_healed = self._reconcile_document_files_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if bootstrapped or healed or files_healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )

            return {"success": True, "data": {"summary": self._document_summary(doc)}}
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_FETCH_ERROR"}

    def get_conversation_context_snapshot(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, healed = self._reconcile_document_messages_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if bootstrapped or healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )
                    self._repo.set_message_count(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message_count=len(doc.get("messages") or []),
                    )

            messages = self._prepare_response_messages(doc.get("messages") or [])
            return {
                "success": True,
                "data": {
                    "messages": messages,
                    "summary": self._document_summary(doc),
                    "conversation_id": int(conversation_id),
                    "user_id": int(user_id),
                },
            }
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_FETCH_ERROR"}

    def refresh_conversation_summary(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, healed = self._reconcile_document_messages_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                doc, files_healed = self._reconcile_document_files_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                summary = build_conversation_summary(
                    messages=doc.get("messages") if isinstance(doc.get("messages"), list) else [],
                    previous_summary=self._document_summary(doc),
                )
                self._set_document_summary(document=doc, summary=summary)
                self._persist_document_and_index(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=doc,
                )
                if healed or files_healed:
                    self._repo.set_message_count(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message_count=len(doc.get("messages") or []),
                    )
            return {"success": True, "data": {"summary": summary}}
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_SUMMARY_ERROR"}

    def add_message(
        self,
        *,
        user_id: int,
        conversation_id: int,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        role_text = (role or "").strip().lower()
        if role_text not in {"user", "assistant"}:
            return {"success": False, "error": "invalid_role", "code": "VALIDATION_ERROR"}
        content_text = (content or "").strip()
        if not content_text:
            return {"success": False, "error": "empty_content", "code": "VALIDATION_ERROR"}
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, healed = self._reconcile_document_messages_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )

                messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []
                now_iso = self._now_iso()
                db_message_id = self._repo.add_message(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role=role_text,
                    content=content_text,
                    metadata=metadata or {},
                )
                message_payload: dict[str, Any] = {
                    "message_id": f"m_{int(db_message_id):06d}",
                    "role": role_text,
                    "content": content_text,
                    "created_at": now_iso,
                    "status": "done",
                    "metadata": metadata or {},
                }
                if role_text == "assistant" and isinstance(metadata, dict):
                    if metadata.get("query_mode"):
                        message_payload["query_mode"] = metadata.get("query_mode")
                    if isinstance(metadata.get("references"), list):
                        message_payload["references"] = metadata.get("references")
                    if isinstance(metadata.get("steps"), list):
                        message_payload["steps"] = metadata.get("steps")
                    if "done_seen" in metadata:
                        message_payload["done_seen"] = bool(metadata.get("done_seen"))

                messages.append(message_payload)
                doc["messages"] = messages
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["title"] = str(row.get("title") or meta.get("title") or "New Conversation")
                meta["updated_at"] = now_iso
                meta["message_count"] = len(messages)
                meta["last_message_at"] = now_iso
                doc["meta"] = meta

                try:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )
                except Exception:
                    try:
                        self._repo.delete_message(
                            message_id=int(db_message_id),
                            conversation_id=conversation_id,
                            user_id=user_id,
                        )
                    except Exception as rollback_exc:  # pragma: no cover
                        self._logger.warning(
                            "message rollback failed (conversation=%s, user=%s, message=%s): %s",
                            conversation_id,
                            user_id,
                            db_message_id,
                            rollback_exc,
                        )
                    raise

                self._repo.set_message_count(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    message_count=len(messages),
                )

                numeric_id = int(db_message_id)

            return {
                "success": True,
                "data": {
                    "message_id": int(numeric_id),
                    "conversation_id": int(conversation_id),
                },
            }
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "MESSAGE_ADD_ERROR"}

    def delete_conversation(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            self._repo.delete_conversation(conversation_id=conversation_id, user_id=user_id)
            try:
                local_path = self._json_store.conversation_local_path(user_id=user_id, conversation_id=conversation_id)
                if local_path.exists():
                    local_path.unlink()
            except Exception:
                pass
            return {"success": True, "message": "deleted"}
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_DELETE_ERROR"}

    def add_uploaded_file(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_type: str,
        file_name: str,
        local_path: str | None,
        storage_ref: str | None,
        content_type: str | None,
        size_bytes: int | None,
    ) -> dict[str, Any]:
        file_type_text = (file_type or "").strip().lower()
        if file_type_text not in {"pdf", "excel"}:
            return {"success": False, "error": "invalid_file_type", "code": "VALIDATION_ERROR"}
        if not (file_name or "").strip():
            return {"success": False, "error": "empty_file_name", "code": "VALIDATION_ERROR"}

        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, files_healed = self._reconcile_document_files_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if files_healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )
                file_id = self._repo.add_uploaded_file(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    file_type=file_type_text,
                    file_name=file_name.strip(),
                    local_path=local_path,
                    storage_ref=storage_ref,
                    content_type=content_type,
                    size_bytes=size_bytes,
                )
                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                current_max_file_no = 0
                existing_index = -1
                for idx, item in enumerate(files):
                    current_max_file_no = max(current_max_file_no, self._safe_int(item.get("file_no"), default=0))
                    if self._safe_int(item.get("file_id"), default=0) == int(file_id):
                        existing_index = idx

                payload = {
                    "file_no": current_max_file_no + 1,
                    "file_id": int(file_id),
                    "file_type": file_type_text,
                    "file_name": file_name.strip(),
                    "local_path": str(local_path or ""),
                    "storage_ref": str(storage_ref or ""),
                    "content_type": str(content_type or ""),
                    "size_bytes": self._safe_int(size_bytes, default=0),
                    "uploaded_at": self._now_iso(),
                    "file_status": "active",
                    "deleted_at": None,
                    "deleted_by": None,
                }

                if existing_index >= 0:
                    payload["file_no"] = self._safe_int(files[existing_index].get("file_no"), default=payload["file_no"])
                    files[existing_index] = {**files[existing_index], **payload}
                else:
                    files.append(payload)

                files.sort(
                    key=lambda item: (
                        self._safe_int(item.get("file_no"), default=0),
                        self._safe_int(item.get("file_id"), default=0),
                    )
                )
                doc["files"] = files
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["updated_at"] = self._now_iso()
                doc["meta"] = meta

                self._persist_document_and_index(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=doc,
                )

            return {
                "success": True,
                "data": {
                    "file_id": int(file_id),
                    "conversation_id": int(conversation_id),
                    "file_type": file_type_text,
                    "file_name": file_name.strip(),
                },
            }
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_RECORD_ERROR"}

    def list_uploaded_files(
        self,
        *,
        user_id: int,
        conversation_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, files_healed = self._reconcile_document_files_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if bootstrapped or files_healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )

            raw_files = doc.get("files") if isinstance(doc.get("files"), list) else []
            files = self._prepare_response_files(
                files=raw_files,
                conversation_id=conversation_id,
                user_id=user_id,
                only_active=not bool(include_deleted),
            )
            if not raw_files:
                legacy_files = self._repo.list_uploaded_files(conversation_id=conversation_id, user_id=user_id)
                files = self._prepare_response_files(
                    files=self._normalize_json_files(legacy_files),
                    conversation_id=conversation_id,
                    user_id=user_id,
                    only_active=not bool(include_deleted),
                )
            return {"success": True, "data": {"files": files}}
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_LIST_ERROR"}

    def get_uploaded_file(self, *, user_id: int, conversation_id: int, file_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, files_healed = self._reconcile_document_files_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if bootstrapped or files_healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )

            files_all = self._prepare_response_files(
                files=doc.get("files") or [],
                conversation_id=conversation_id,
                user_id=user_id,
                only_active=False,
            )
            for item in files_all:
                if self._safe_int(item.get("id"), default=0) != int(file_id):
                    continue
                if str(item.get("file_status") or "").strip().lower() != "active":
                    return {"success": False, "error": "file_deleted", "code": "NOT_FOUND"}
                return {"success": True, "data": item}

            file_row = self._repo.get_uploaded_file(
                conversation_id=conversation_id,
                user_id=user_id,
                file_id=file_id,
            )
            if not file_row:
                return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
            fallback_items = self._prepare_response_files(
                files=self._normalize_json_files([file_row]),
                conversation_id=conversation_id,
                user_id=user_id,
                only_active=True,
            )
            if not fallback_items:
                return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
            return {"success": True, "data": fallback_items[0]}
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_FETCH_ERROR"}

    def remove_uploaded_file(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_id: int,
    ) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                doc, files_healed = self._reconcile_document_files_with_db(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    document=doc,
                )
                if bootstrapped or files_healed:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=doc,
                    )

                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                target_idx = -1
                for idx, item in enumerate(files):
                    if self._safe_int(item.get("file_id"), default=0) == int(file_id):
                        target_idx = idx
                        break
                if target_idx < 0:
                    file_row = self._repo.get_uploaded_file(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        file_id=file_id,
                    )
                    if not file_row:
                        return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
                    normalized_files = self._normalize_json_files([file_row])
                    current = normalized_files[0] if normalized_files else {}
                    target_idx = len(files)
                    files.append(current)

                current = files[target_idx] if isinstance(files[target_idx], dict) else {}
                current_status = str(current.get("file_status") or "active").strip().lower()
                if current_status == "deleted":
                    return {
                        "success": True,
                        "data": {
                            "conversation_id": int(conversation_id),
                            "file_id": int(file_id),
                            "file_status": "deleted",
                            "already_deleted": True,
                        },
                    }

                now_iso = self._now_iso()
                files[target_idx] = {
                    **current,
                    "file_status": "deleted",
                    "deleted_at": now_iso,
                    "deleted_by": int(user_id),
                }
                doc["files"] = files
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["updated_at"] = now_iso
                doc["meta"] = meta

                self._persist_document_and_index(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=doc,
                )
                try:
                    deleted = self._repo.delete_uploaded_file(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        file_id=file_id,
                    )
                    if not deleted:
                        self._logger.warning(
                            "conversation file db delete skipped (conversation=%s, user=%s, file=%s)",
                            conversation_id,
                            user_id,
                            file_id,
                        )
                except Exception as exc:  # pragma: no cover
                    self._logger.warning(
                        "conversation file db delete failed (conversation=%s, user=%s, file=%s): %s",
                        conversation_id,
                        user_id,
                        file_id,
                        exc,
                    )

            return {
                "success": True,
                "data": {
                    "conversation_id": int(conversation_id),
                    "file_id": int(file_id),
                    "file_status": "deleted",
                },
            }
        except (DatabaseConfigError, DatabaseConnectionError) as exc:
            return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_DELETE_ERROR"}


conversation_service = ConversationService()
