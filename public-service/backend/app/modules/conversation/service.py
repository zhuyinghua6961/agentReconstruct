from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import DatabaseUnavailableError
from app.core.timezone import BEIJING_TIMEZONE, ensure_beijing_datetime, now_beijing, now_beijing_iso
from app.integrations.redis import RedisService, build_redis_bindings
from app.modules.conversation.authority_summary import build_authority_summary
from app.modules.conversation.cache import (
    cache_conversation_detail,
    cache_conversation_list,
    get_cached_conversation_detail,
    get_cached_conversation_list,
    get_conversation_detail_freshness_grace_seconds,
    get_recent_conversation_list_pages,
    invalidate_conversation_detail_cache,
    invalidate_conversation_list_cache,
    note_conversation_detail_miss,
    note_conversation_list_access,
    note_conversation_list_miss,
)
from app.modules.conversation.json_store import ConversationJsonStore
from app.modules.conversation.outbox import ConversationOutboxRepository
from app.modules.conversation.repository import ConversationRepository
from app.modules.storage.service import storage_service
from app.integrations.storage.factory import get_storage_backend


class ConversationService:
    def __init__(
        self,
        *,
        repo: ConversationRepository | None = None,
        json_store: ConversationJsonStore | None = None,
        outbox_repo: ConversationOutboxRepository | None = None,
        cleanup_uploaded_file_fn: Any | None = None,
        workspace_root: str | Path | None = None,
        redis_service: RedisService | None = None,
    ) -> None:
        settings = get_settings()
        self._workspace_root = Path(workspace_root or settings.data_root).resolve()
        self._repo = repo or ConversationRepository()
        self._logger = logging.getLogger(__name__)
        self._json_store = json_store or ConversationJsonStore(
            project_root=str(self._workspace_root),
            logger=self._logger,
            redis_service=redis_service,
        )
        self._outbox_repo = outbox_repo or ConversationOutboxRepository()
        self._cleanup_uploaded_file_fn = cleanup_uploaded_file_fn
        self._redis_service = redis_service
        self._redis_service_resolved = redis_service is not None
        self._legacy_conversation_fallback_enabled = bool(settings.conversation_legacy_fallback_enabled)
        self._parse_status_set = {"uploaded", "parsing", "parsed", "failed"}
        self._index_status_set = {"pending", "indexing", "ready", "failed"}
        self._processing_stage_set = {"uploaded", "parsing", "parsed", "indexing", "ready", "failed"}
        self._live_task_status_set = {"queued", "admitted", "running"}
        self._terminal_task_status_set = {"done", "completed", "failed", "canceled", "expired"}

    def status_code_for(self, result: dict[str, Any], *, ok_status: int) -> int:
        if result.get("success"):
            return ok_status
        code = str(result.get("code") or "").strip().upper()
        if code in {"VALIDATION_ERROR"}:
            return 400
        if code in {"NOT_FOUND", "FILE_UNAVAILABLE"}:
            return 404
        if code in {"DB_UNAVAILABLE"}:
            return 503
        return 500

    def _now_iso(self) -> str:
        return now_beijing_iso()

    def _to_iso(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback
        if isinstance(value, datetime):
            return ensure_beijing_datetime(value).isoformat(timespec="seconds")
        text = str(value).strip()
        return text or fallback

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            text = str(value).strip()
            if not text:
                return None
            normalized = text.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TIMEZONE)
        return parsed.astimezone(timezone.utc)

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _normalize_parse_status(self, value: Any, *, default: str = "uploaded") -> str:
        text = str(value or "").strip().lower() or default
        return text if text in self._parse_status_set else default

    def _normalize_index_status(self, value: Any, *, default: str = "pending") -> str:
        text = str(value or "").strip().lower() or default
        return text if text in self._index_status_set else default

    def _derive_processing_stage(self, *, parse_status: str, index_status: str, fallback: Any = "") -> str:
        if parse_status == "failed" or index_status == "failed":
            return "failed"
        if index_status == "ready":
            return "ready"
        if index_status == "indexing":
            return "indexing"
        if parse_status == "parsed":
            return "parsed"
        if parse_status == "parsing":
            return "parsing"
        candidate = str(fallback or "").strip().lower()
        if candidate in self._processing_stage_set:
            return candidate
        return "uploaded"

    def _should_use_legacy_conversation_fallback(self) -> bool:
        return bool(self._legacy_conversation_fallback_enabled)

    def _cleanup_uploaded_file_resources(self, *, file_row: dict[str, Any]) -> dict[str, Any]:
        if callable(self._cleanup_uploaded_file_fn):
            return self._cleanup_uploaded_file_fn(file_row=file_row, logger=self._logger)
        return storage_service.cleanup_resources(file_row=file_row, project_root=str(self._workspace_root))

    def _rollback_uploaded_file_insert(
        self,
        *,
        row: dict[str, Any],
        user_id: int,
        conversation_id: int,
        file_id: int,
    ) -> None:
        try:
            self._repo.delete_uploaded_file(
                conversation_id=conversation_id,
                user_id=user_id,
                file_id=file_id,
            )
        except Exception:
            self._logger.warning("uploaded file row rollback failed", exc_info=True)

        try:
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc = self._json_store.load_document(user_id=user_id, conversation_id=conversation_id)
                if not isinstance(doc, dict):
                    return
                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                filtered = [
                    item
                    for item in files
                    if not isinstance(item, dict) or self._safe_int(item.get("file_id"), default=0) != int(file_id)
                ]
                if len(filtered) == len(files):
                    return
                doc["files"] = filtered
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["updated_at"] = self._now_iso()
                doc["meta"] = meta
                self._json_store.write_document(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=doc,
                    storage_ref_hint=str(row.get("chat_json_storage_ref") or "") or None,
                )
        except Exception:
            self._logger.warning("uploaded file json rollback failed", exc_info=True)

    def _get_redis_service(self) -> RedisService | None:
        if self._redis_service_resolved:
            return self._redis_service
        self._redis_service_resolved = True
        try:
            settings = get_settings()
            bindings = build_redis_bindings(settings=settings)
            self._redis_service = RedisService.from_prefix(
                client=bindings.client,
                key_prefix=str(settings.redis_key_prefix or "agentcode"),
            )
        except Exception:
            self._redis_service = None
        return self._redis_service

    def _invalidate_list_cache(self, *, user_id: int) -> None:
        invalidate_conversation_list_cache(redis_service=self._get_redis_service(), user_id=user_id)

    def _invalidate_detail_cache(self, *, user_id: int, conversation_id: int) -> None:
        invalidate_conversation_detail_cache(
            redis_service=self._get_redis_service(),
            user_id=user_id,
            conversation_id=conversation_id,
        )

    def _get_cached_detail_payload(self, *, user_id: int, conversation_id: int) -> dict[str, Any] | None:
        cached = get_cached_conversation_detail(
            redis_service=self._get_redis_service(),
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return cached if isinstance(cached, dict) else None

    def _get_cached_detail_data(
        self,
        *,
        user_id: int,
        conversation_id: int,
        row: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        cached = self._get_cached_detail_payload(user_id=user_id, conversation_id=conversation_id)
        if not isinstance(cached, dict):
            return None
        if row is not None and not self._is_detail_cache_payload_fresh(row=row, payload=cached):
            return None
        data = cached.get("data")
        return data if isinstance(data, dict) else None

    def _is_detail_cache_payload_fresh(self, *, row: dict[str, Any], payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return False
        data = payload.get("data")
        if not isinstance(data, dict):
            return False

        row_title = str(row.get("title") or "").strip()
        cached_title = str(data.get("title") or "").strip()
        if row_title and cached_title and row_title != cached_title:
            return False

        row_message_count = self._safe_int(row.get("message_count"), default=0)
        cached_message_count = self._safe_int(data.get("message_count"), default=row_message_count)
        if row_message_count > cached_message_count:
            return False

        grace_seconds = get_conversation_detail_freshness_grace_seconds()
        row_updated_at = self._parse_timestamp(row.get("updated_at"))
        cached_updated_at = self._parse_timestamp(data.get("updated_at"))
        if row_updated_at is not None and cached_updated_at is not None:
            if (row_updated_at - cached_updated_at).total_seconds() > grace_seconds:
                return False
        elif row_updated_at is not None and cached_updated_at is None:
            cache_meta = payload.get("cache_meta") if isinstance(payload.get("cache_meta"), dict) else {}
            cached_at = self._parse_timestamp(cache_meta.get("cached_at"))
            if cached_at is None or (row_updated_at - cached_at).total_seconds() > grace_seconds:
                return False

        return True

    def _build_conversation_detail_payload(
        self,
        *,
        row: dict[str, Any],
        conversation_id: int,
        user_id: int,
        document: dict[str, Any],
        include_legacy_files_fallback: bool,
    ) -> dict[str, Any]:
        messages = self._prepare_response_messages(document.get("messages") or [])
        raw_files = document.get("files") if isinstance(document.get("files"), list) else []
        files_all = self._prepare_response_files(
            files=raw_files,
            conversation_id=conversation_id,
            user_id=user_id,
            only_active=False,
        )
        uploaded_files = [item for item in files_all if str(item.get("file_status")) == "active"]

        if include_legacy_files_fallback and not raw_files:
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

        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        title = str(row.get("title") or meta.get("title") or "New Conversation")
        created_at = self._to_iso(meta.get("created_at") or row.get("created_at"), fallback=self._now_iso())
        updated_at = self._to_iso(meta.get("updated_at") or row.get("updated_at"), fallback=created_at)
        pdf_files = [item for item in uploaded_files if str(item.get("file_type")) == "pdf"]
        excel_files = [item for item in uploaded_files if str(item.get("file_type")) == "excel"]
        message_count = len(messages)

        return {
            "success": True,
            "data": {
                "conversation_id": int(row["id"]),
                "user_id": int(row["user_id"]),
                "title": title,
                "message_count": int(message_count),
                "created_at": created_at,
                "updated_at": updated_at,
                "messages": messages,
                "uploaded_files": uploaded_files,
                "uploaded_files_all": files_all,
                "pdf_files": pdf_files,
                "excel_files": excel_files,
            },
        }

    def _build_conversation_list_payload(self, *, user_id: int, page: int, page_size: int) -> dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = min(100, max(1, int(page_size or 20)))
        offset = (page - 1) * page_size
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
                        "created_at": self._to_iso(item.get("created_at"), fallback=self._now_iso()),
                        "updated_at": self._to_iso(
                            item.get("updated_at"),
                            fallback=self._to_iso(item.get("created_at"), fallback=self._now_iso()),
                        ),
                    }
                    for item in items
                ],
                "total_count": total,
                "page": page,
                "page_size": page_size,
            },
        }

    def _refresh_detail_cache(
        self,
        *,
        row: dict[str, Any],
        conversation_id: int,
        user_id: int,
        document: dict[str, Any],
    ) -> None:
        self._invalidate_detail_cache(user_id=user_id, conversation_id=conversation_id)
        cache_conversation_detail(
            redis_service=self._get_redis_service(),
            user_id=user_id,
            conversation_id=conversation_id,
            payload=self._build_conversation_detail_payload(
                row=row,
                conversation_id=conversation_id,
                user_id=user_id,
                document=document,
                include_legacy_files_fallback=False,
            ),
        )

    def _refresh_primary_list_cache(self, *, user_id: int) -> None:
        redis_service = self._get_redis_service()
        recent_pages = get_recent_conversation_list_pages(redis_service=redis_service, user_id=user_id)
        targets: list[tuple[int, int]] = [(1, 20)]
        for item in recent_pages:
            candidate = (int(item.get("page") or 0), int(item.get("page_size") or 0))
            if candidate[0] <= 0 or candidate[1] <= 0 or candidate in targets:
                continue
            targets.append(candidate)

        self._invalidate_list_cache(user_id=user_id)
        for page, page_size in targets:
            cache_conversation_list(
                redis_service=redis_service,
                user_id=user_id,
                page=page,
                page_size=page_size,
                payload=self._build_conversation_list_payload(user_id=user_id, page=page, page_size=page_size),
            )

    def recover_pending_upload_processing_tasks(self, *, worker: Any, limit: int | None = None) -> dict[str, Any]:
        if worker is None or not bool(getattr(worker, "enabled", True)):
            return {"success": True, "scanned": 0, "submitted": 0, "skipped": 0, "reason": "worker_disabled"}
        if not hasattr(self._repo, "list_uploaded_files_for_processing_recovery"):
            return {"success": True, "scanned": 0, "submitted": 0, "skipped": 0, "reason": "repo_unsupported"}

        try:
            configured_limit = int(str(os.getenv("UPLOAD_PROCESSING_RECOVERY_SCAN_LIMIT", "500") or "500").strip())
        except Exception:
            configured_limit = 500
        scan_limit = max(1, min(5000, int(limit if limit is not None else configured_limit)))

        submitted = 0
        skipped = 0
        scanned_rows = self._repo.list_uploaded_files_for_processing_recovery(limit=scan_limit)
        for item in scanned_rows:
            try:
                file_id = self._safe_int(item.get("id"), default=0)
                conversation_id = self._safe_int(item.get("conversation_id"), default=0)
                user_id = self._safe_int(item.get("user_id"), default=0)
                file_type = str(item.get("file_type") or "").strip().lower()
                local_path = str(item.get("local_path") or "").strip()
                if file_id <= 0 or conversation_id <= 0 or user_id <= 0 or file_type not in {"pdf", "excel"}:
                    skipped += 1
                    continue
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
                if not row:
                    skipped += 1
                    continue
                doc = self._json_store.load_document(user_id=user_id, conversation_id=conversation_id)
                if doc is None:
                    doc, _ = self._load_or_bootstrap_document(
                        row=row,
                        conversation_id=conversation_id,
                        user_id=user_id,
                    )
                files = doc.get("files") if isinstance(doc, dict) and isinstance(doc.get("files"), list) else []
                target = None
                for file_item in files:
                    if self._safe_int((file_item or {}).get("file_id"), default=0) == file_id:
                        target = file_item if isinstance(file_item, dict) else None
                        break
                if not isinstance(target, dict):
                    skipped += 1
                    continue
                if str(target.get("file_status") or "active").strip().lower() != "active":
                    skipped += 1
                    continue
                parse_status = self._normalize_parse_status(target.get("parse_status"), default="uploaded")
                index_status = self._normalize_index_status(target.get("index_status"), default="pending")
                stage = self._derive_processing_stage(
                    parse_status=parse_status,
                    index_status=index_status,
                    fallback=target.get("processing_stage"),
                )
                needs_processing = (
                    parse_status in {"uploaded", "parsing", "parsed"}
                    or index_status in {"pending", "indexing"}
                    or stage in {"uploaded", "parsing", "parsed", "indexing"}
                )
                if not needs_processing or stage in {"ready", "failed"}:
                    skipped += 1
                    continue
                if worker.submit(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    file_id=file_id,
                    file_type=file_type,
                    local_path=local_path,
                ):
                    submitted += 1
                else:
                    skipped += 1
            except Exception as exc:
                self._logger.warning("upload processing recovery scan failed for row=%s error=%s", item, exc)
                skipped += 1

        return {"success": True, "scanned": len(scanned_rows), "submitted": submitted, "skipped": skipped}

    def _build_cleanup_meta_patch(self, *, current_meta: dict[str, Any], cleanup_result: dict[str, Any]) -> dict[str, Any]:
        now_iso = self._now_iso()
        errors = cleanup_result.get("errors") if isinstance(cleanup_result.get("errors"), list) else []
        error_text = "; ".join(str(item) for item in errors if str(item).strip())[:2000]
        attempt_count = self._safe_int(current_meta.get("cleanup_attempt_count"), default=0) + 1
        pending = bool(error_text)
        return {
            **current_meta,
            "cleanup_attempt_count": attempt_count,
            "cleanup_last_attempt_at": now_iso,
            "cleanup_pending": pending,
            "cleanup_error": error_text,
            "cleanup_storage_deleted": bool(cleanup_result.get("storage_deleted")),
            "cleanup_local_deleted": bool(cleanup_result.get("local_deleted")),
        }

    def _reconcile_deleted_file_cleanup(
        self,
        *,
        row: dict[str, Any],
        user_id: int,
        conversation_id: int,
        document: dict[str, Any],
    ) -> bool:
        files = document.get("files") if isinstance(document.get("files"), list) else []
        if not files:
            return False

        try:
            raw = str(os.getenv("DELETED_FILE_CLEANUP_RECONCILE_LIMIT", "3") or "3").strip()
            limit = max(0, min(20, int(raw)))
        except Exception:
            limit = 3
        if limit <= 0:
            return False

        changed = False
        handled = 0
        for idx, item in enumerate(files):
            if handled >= limit or not isinstance(item, dict):
                continue
            status = str(item.get("file_status") or "active").strip().lower()
            if status != "deleted":
                continue
            meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
            pending = bool(meta.get("cleanup_pending", False))
            if not pending and str(meta.get("cleanup_last_attempt_at") or "").strip():
                continue
            handled += 1
            cleanup_result = self._cleanup_uploaded_file_resources(file_row=item)
            meta_patch = self._build_cleanup_meta_patch(current_meta=meta, cleanup_result=cleanup_result)
            files[idx] = {
                **item,
                "file_meta": meta_patch,
                "status_updated_at": self._now_iso(),
            }
            changed = True

        if not changed:
            return False

        document["files"] = files
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        meta["updated_at"] = self._now_iso()
        document["meta"] = meta
        self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=document)
        return True

    def _message_numeric_id(self, message: dict[str, Any], default_value: int) -> int:
        raw = str(message.get("message_id") or "").strip()
        if raw.startswith("m_"):
            return self._safe_int(raw[2:], default=default_value)
        return self._safe_int(raw, default=default_value)

    def _next_message_id(self, messages: list[dict[str, Any]]) -> str:
        max_id = 0
        for idx, item in enumerate(messages, start=1):
            max_id = max(max_id, self._message_numeric_id(item, idx))
        return f"m_{max_id + 1:06d}"

    def _normalize_json_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if metadata.get("authority_assistant_async") is True or metadata.get("authority_assistant_terminal_async") is True:
                continue
            db_id = self._safe_int(row.get("id"), default=idx)
            status = self._normalize_terminal_status(metadata.get("terminal_status"), default="done")
            payload: dict[str, Any] = {
                "message_id": f"m_{db_id:06d}",
                "role": str(row.get("role") or "assistant"),
                "content": str(row.get("content") or ""),
                "created_at": self._to_iso(row.get("created_at"), fallback=self._now_iso()),
                "status": status,
                "metadata": metadata,
            }
            if metadata.get("query_mode"):
                payload["query_mode"] = metadata.get("query_mode")
            if isinstance(metadata.get("references"), list):
                payload["references"] = metadata.get("references")
            if isinstance(metadata.get("reference_objects"), list):
                payload["reference_objects"] = metadata.get("reference_objects")
            if isinstance(metadata.get("reference_links"), list):
                payload["reference_links"] = metadata.get("reference_links")
            if isinstance(metadata.get("pdf_links"), list):
                payload["pdf_links"] = metadata.get("pdf_links")
            if isinstance(metadata.get("doi_locations"), dict):
                payload["doi_locations"] = metadata.get("doi_locations")
            if isinstance(metadata.get("steps"), list):
                payload["steps"] = metadata.get("steps")
            if "done_seen" in metadata:
                payload["done_seen"] = bool(metadata.get("done_seen"))
            if str(metadata.get("failure_stage") or "").strip():
                payload["failure_stage"] = str(metadata.get("failure_stage") or "")
            if str(metadata.get("failure_code") or "").strip():
                payload["failure_code"] = str(metadata.get("failure_code") or "")
            if str(metadata.get("failure_message") or "").strip():
                payload["failure_message"] = str(metadata.get("failure_message") or "")
            if "retriable" in metadata:
                payload["retriable"] = bool(metadata.get("retriable"))
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
            parse_status = self._normalize_parse_status(row.get("parse_status"), default="uploaded")
            index_status = self._normalize_index_status(row.get("index_status"), default="pending")
            processing_stage = self._derive_processing_stage(
                parse_status=parse_status,
                index_status=index_status,
                fallback=row.get("processing_stage"),
            )
            file_meta = row.get("file_meta") if isinstance(row.get("file_meta"), dict) else {}
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
                    "uploaded_at": self._to_iso(row.get("uploaded_at") or row.get("created_at"), fallback=self._now_iso()),
                    "file_status": status,
                    "parse_status": parse_status,
                    "index_status": index_status,
                    "processing_stage": processing_stage,
                    "status_updated_at": self._to_iso(
                        row.get("status_updated_at") or row.get("uploaded_at") or row.get("created_at"),
                        fallback=self._now_iso(),
                    ),
                    "last_error": str(row.get("last_error") or ""),
                    "file_meta": dict(file_meta),
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
            if isinstance(item.get("reference_objects"), list) and "reference_objects" not in metadata:
                metadata["reference_objects"] = item.get("reference_objects")
            if isinstance(item.get("reference_links"), list) and "reference_links" not in metadata:
                metadata["reference_links"] = item.get("reference_links")
            if isinstance(item.get("pdf_links"), list) and "pdf_links" not in metadata:
                metadata["pdf_links"] = item.get("pdf_links")
            if isinstance(item.get("doi_locations"), dict) and "doi_locations" not in metadata:
                metadata["doi_locations"] = item.get("doi_locations")
            if isinstance(item.get("steps"), list) and "steps" not in metadata:
                metadata["steps"] = item.get("steps")
            if "done_seen" in item and "done_seen" not in metadata:
                metadata["done_seen"] = bool(item.get("done_seen"))
            status = self._message_terminal_status(item)
            rows.append(
                {
                    "id": self._message_numeric_id(item, idx),
                    "message_id": str(item.get("message_id") or f"m_{self._message_numeric_id(item, idx):06d}"),
                    "role": str(item.get("role") or "assistant"),
                    "content": str(item.get("content") or ""),
                    "metadata": metadata,
                    "created_at": self._to_iso(item.get("created_at"), fallback=self._now_iso()),
                    "status": status,
                    "terminal_status": status,
                    **({"query_mode": metadata.get("query_mode")} if metadata.get("query_mode") else {}),
                    **({"references": metadata.get("references")} if isinstance(metadata.get("references"), list) else {}),
                    **({"reference_objects": metadata.get("reference_objects")} if isinstance(metadata.get("reference_objects"), list) else {}),
                    **({"reference_links": metadata.get("reference_links")} if isinstance(metadata.get("reference_links"), list) else {}),
                    **({"pdf_links": metadata.get("pdf_links")} if isinstance(metadata.get("pdf_links"), list) else {}),
                    **({"doi_locations": metadata.get("doi_locations")} if isinstance(metadata.get("doi_locations"), dict) else {}),
                    **({"steps": metadata.get("steps")} if isinstance(metadata.get("steps"), list) else {}),
                    **({"done_seen": bool(metadata.get("done_seen"))} if "done_seen" in metadata else {}),
                    **({"failure_stage": metadata.get("failure_stage")} if str(metadata.get("failure_stage") or "").strip() else {}),
                    **({"failure_code": metadata.get("failure_code")} if str(metadata.get("failure_code") or "").strip() else {}),
                    **({"failure_message": metadata.get("failure_message")} if str(metadata.get("failure_message") or "").strip() else {}),
                    **({"retriable": bool(metadata.get("retriable"))} if "retriable" in metadata else {}),
                }
            )
        return rows

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
            key=lambda item: (self._safe_int(item.get("file_no"), 0), self._safe_int(item.get("file_id"), 0)),
        )
        result: list[dict[str, Any]] = []
        display_no = 0
        for item in items:
            status = str(item.get("file_status") or "active").strip().lower()
            if status not in {"active", "deleted"}:
                status = "active"
            if only_active and status != "active":
                continue
            current_display_no = 0
            if status == "active":
                display_no += 1
                current_display_no = display_no
            file_id = self._safe_int(item.get("file_id"), default=0)
            parse_status = self._normalize_parse_status(item.get("parse_status"), default="uploaded")
            index_status = self._normalize_index_status(item.get("index_status"), default="pending")
            processing_stage = self._derive_processing_stage(
                parse_status=parse_status,
                index_status=index_status,
                fallback=item.get("processing_stage"),
            )
            file_meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
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
                    "display_no": current_display_no,
                    "file_status": status,
                    "parse_status": parse_status,
                    "index_status": index_status,
                    "processing_stage": processing_stage,
                    "status_updated_at": self._to_iso(item.get("status_updated_at"), fallback=self._now_iso()),
                    "last_error": str(item.get("last_error") or ""),
                    "file_meta": dict(file_meta),
                    "deleted_at": item.get("deleted_at"),
                    "deleted_by": item.get("deleted_by"),
                }
            )
        return result

    def _build_document_from_cached_detail(
        self,
        *,
        row: dict[str, Any],
        conversation_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        cached_payload = self._get_cached_detail_payload(user_id=user_id, conversation_id=conversation_id)
        if not self._is_detail_cache_payload_fresh(row=row, payload=cached_payload):
            return None
        cached_data = cached_payload.get("data") if isinstance(cached_payload, dict) else None
        if not isinstance(cached_data, dict):
            return None

        created_fallback = self._now_iso()
        created_at = self._to_iso(cached_data.get("created_at") or row.get("created_at"), fallback=created_fallback)
        updated_at = self._to_iso(cached_data.get("updated_at") or row.get("updated_at"), fallback=created_at)

        messages_raw = cached_data.get("messages") if isinstance(cached_data.get("messages"), list) else []
        messages: list[dict[str, Any]] = []
        for idx, item in enumerate(messages_raw, start=1):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if item.get("query_mode") and not metadata.get("query_mode"):
                metadata["query_mode"] = item.get("query_mode")
            if isinstance(item.get("references"), list) and "references" not in metadata:
                metadata["references"] = list(item.get("references") or [])
            if isinstance(item.get("reference_objects"), list) and "reference_objects" not in metadata:
                metadata["reference_objects"] = list(item.get("reference_objects") or [])
            if isinstance(item.get("reference_links"), list) and "reference_links" not in metadata:
                metadata["reference_links"] = list(item.get("reference_links") or [])
            if isinstance(item.get("pdf_links"), list) and "pdf_links" not in metadata:
                metadata["pdf_links"] = list(item.get("pdf_links") or [])
            if isinstance(item.get("doi_locations"), dict) and "doi_locations" not in metadata:
                metadata["doi_locations"] = dict(item.get("doi_locations") or {})
            if isinstance(item.get("steps"), list) and "steps" not in metadata:
                metadata["steps"] = list(item.get("steps") or [])
            if "done_seen" in item and "done_seen" not in metadata:
                metadata["done_seen"] = bool(item.get("done_seen"))
            if item.get("terminal_status") and "terminal_status" not in metadata:
                metadata["terminal_status"] = str(item.get("terminal_status") or "")
            if item.get("status") and "terminal_status" not in metadata:
                metadata["terminal_status"] = str(item.get("status") or "")
            if item.get("failure_stage") and "failure_stage" not in metadata:
                metadata["failure_stage"] = str(item.get("failure_stage") or "")
            if item.get("failure_code") and "failure_code" not in metadata:
                metadata["failure_code"] = str(item.get("failure_code") or "")
            if item.get("failure_message") and "failure_message" not in metadata:
                metadata["failure_message"] = str(item.get("failure_message") or "")
            if "retriable" in item and "retriable" not in metadata:
                metadata["retriable"] = bool(item.get("retriable"))
            messages.append(
                {
                    "message_id": str(item.get("message_id") or f"m_{idx:06d}"),
                    "role": str(item.get("role") or "assistant"),
                    "content": str(item.get("content") or ""),
                    "created_at": self._to_iso(item.get("created_at"), fallback=updated_at),
                    "status": str(item.get("status") or "done"),
                    "metadata": metadata,
                    **({"query_mode": metadata.get("query_mode")} if metadata.get("query_mode") else {}),
                    **({"references": metadata.get("references")} if isinstance(metadata.get("references"), list) else {}),
                    **({"reference_objects": metadata.get("reference_objects")} if isinstance(metadata.get("reference_objects"), list) else {}),
                    **({"reference_links": metadata.get("reference_links")} if isinstance(metadata.get("reference_links"), list) else {}),
                    **({"pdf_links": metadata.get("pdf_links")} if isinstance(metadata.get("pdf_links"), list) else {}),
                    **({"doi_locations": metadata.get("doi_locations")} if isinstance(metadata.get("doi_locations"), dict) else {}),
                    **({"steps": metadata.get("steps")} if isinstance(metadata.get("steps"), list) else {}),
                    **({"done_seen": bool(metadata.get("done_seen"))} if "done_seen" in metadata else {}),
                }
            )

        files_raw = cached_data.get("uploaded_files_all") if isinstance(cached_data.get("uploaded_files_all"), list) else []
        files: list[dict[str, Any]] = []
        for idx, item in enumerate(files_raw, start=1):
            if not isinstance(item, dict):
                continue
            file_meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
            files.append(
                {
                    "file_no": self._safe_int(item.get("file_no"), default=idx),
                    "file_id": self._safe_int(item.get("id"), default=0),
                    "file_type": str(item.get("file_type") or ""),
                    "file_name": str(item.get("file_name") or ""),
                    "local_path": str(item.get("local_path") or ""),
                    "storage_ref": str(item.get("storage_ref") or ""),
                    "content_type": str(item.get("content_type") or ""),
                    "size_bytes": self._safe_int(item.get("size_bytes"), default=0),
                    "uploaded_at": self._to_iso(item.get("created_at"), fallback=created_at),
                    "file_status": str(item.get("file_status") or "active"),
                    "parse_status": self._normalize_parse_status(item.get("parse_status"), default="uploaded"),
                    "index_status": self._normalize_index_status(item.get("index_status"), default="pending"),
                    "processing_stage": self._derive_processing_stage(
                        parse_status=self._normalize_parse_status(item.get("parse_status"), default="uploaded"),
                        index_status=self._normalize_index_status(item.get("index_status"), default="pending"),
                        fallback=item.get("processing_stage"),
                    ),
                    "status_updated_at": self._to_iso(item.get("status_updated_at"), fallback=updated_at),
                    "last_error": str(item.get("last_error") or ""),
                    "file_meta": dict(file_meta),
                    "deleted_at": item.get("deleted_at"),
                    "deleted_by": item.get("deleted_by"),
                }
            )

        last_message_at = messages[-1]["created_at"] if messages else None
        return {
            "meta": {
                "schema_version": "chatlog.v1",
                "conversation_id": int(conversation_id),
                "user_id": int(user_id),
                "title": str(cached_data.get("title") or row.get("title") or "New Conversation"),
                "created_at": created_at,
                "updated_at": updated_at,
                "message_count": self._safe_int(cached_data.get("message_count"), default=len(messages)),
                "last_message_at": last_message_at,
            },
            "messages": messages,
            "files": files,
            "runtime": {},
        }

    def _load_or_bootstrap_document(
        self,
        *,
        row: dict[str, Any],
        conversation_id: int,
        user_id: int,
        prefer_cached_detail: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        doc = self._json_store.load_document(user_id=user_id, conversation_id=conversation_id)
        if doc is not None:
            return doc, False

        cached_doc = self._build_document_from_cached_detail(
            row=row,
            conversation_id=conversation_id,
            user_id=user_id,
        ) if prefer_cached_detail else None
        if cached_doc is not None:
            return cached_doc, False
        normalized_messages: list[dict[str, Any]] = []
        normalized_files: list[dict[str, Any]] = []
        if self._should_use_legacy_conversation_fallback():
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
        current_row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
        storage_ref_hint = str(current_row.get("chat_json_storage_ref") or row.get("chat_json_storage_ref") or "")
        write_result = self._json_store.write_document(
            user_id=user_id,
            conversation_id=conversation_id,
            document=document,
            storage_ref_hint=storage_ref_hint,
        )
        self._json_store.assert_lock_healthy()
        next_version = self._safe_int(current_row.get("chat_json_version"), default=self._safe_int(row.get("chat_json_version"), default=0)) + 1
        self._repo.update_chat_json_index(
            conversation_id=conversation_id,
            user_id=user_id,
            local_path=write_result.get("local_path"),
            storage_ref=write_result.get("storage_ref"),
            content_hash=write_result.get("content_hash"),
            size_bytes=write_result.get("size_bytes"),
            version=next_version,
            sync_status=str(write_result.get("sync_status") or "sync_failed"),
            updated_at=now_beijing(),
        )
        self._json_store.assert_lock_healthy()
        row["chat_json_local_path"] = write_result.get("local_path")
        row["chat_json_storage_ref"] = write_result.get("storage_ref")
        row["chat_json_hash"] = write_result.get("content_hash")
        row["chat_json_size_bytes"] = write_result.get("size_bytes")
        row["chat_json_version"] = next_version
        row["chat_json_sync_status"] = str(write_result.get("sync_status") or "sync_failed")
        row["chat_json_updated_at"] = now_beijing()
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
            except Exception as exc:
                self._logger.warning(
                    "conversation json outbox enqueue failed "
                    f"(conversation={conversation_id}, version={next_version}): {exc}"
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
                self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                self._refresh_detail_cache(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
            self._refresh_primary_list_cache(user_id=user_id)
            return {
                "success": True,
                "data": {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "title": (row or {}).get("title", final_title),
                    "message_count": int((row or {}).get("message_count", 0)),
                    "created_at": self._to_iso((row or {}).get("created_at"), fallback=self._now_iso()),
                    "updated_at": self._to_iso(
                        (row or {}).get("updated_at"),
                        fallback=self._to_iso((row or {}).get("created_at"), fallback=self._now_iso()),
                    ),
                },
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_CREATE_ERROR"}

    def update_conversation_title(self, *, user_id: int, conversation_id: int, title: str | None) -> dict[str, Any]:
        final_title = (title or "").strip()
        if not final_title:
            return {"success": False, "error": "title_required", "code": "VALIDATION_ERROR"}

        final_title = final_title[:255]
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}

            self._repo.update_conversation_title(conversation_id=conversation_id, user_id=user_id, title=final_title)
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                if bootstrapped:
                    row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row

                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["title"] = str(row.get("title") or final_title)
                meta["updated_at"] = self._to_iso(row.get("updated_at"), fallback=self._now_iso())
                doc["meta"] = meta
                self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
                self._refresh_detail_cache(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)

            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
            self._refresh_primary_list_cache(user_id=user_id)
            return {
                "success": True,
                "data": {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "title": str((row or {}).get("title") or final_title),
                    "message_count": int((row or {}).get("message_count", 0)),
                    "created_at": self._to_iso((row or {}).get("created_at"), fallback=self._now_iso()),
                    "updated_at": self._to_iso(
                        (row or {}).get("updated_at"),
                        fallback=self._to_iso((row or {}).get("created_at"), fallback=self._now_iso()),
                    ),
                },
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_TITLE_UPDATE_ERROR"}

    def list_conversations(self, *, user_id: int, page: int, page_size: int) -> dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = min(100, max(1, int(page_size or 20)))
        try:
            redis_service = self._get_redis_service()
            cached = get_cached_conversation_list(
                redis_service=redis_service,
                user_id=user_id,
                page=page,
                page_size=page_size,
            )
            if cached is not None:
                note_conversation_list_access(redis_service=redis_service, user_id=user_id, page=page, page_size=page_size)
                return cached
            note_conversation_list_miss()
            payload = self._build_conversation_list_payload(user_id=user_id, page=page, page_size=page_size)
            cache_conversation_list(
                redis_service=redis_service,
                user_id=user_id,
                page=page,
                page_size=page_size,
                payload=payload,
            )
            note_conversation_list_access(redis_service=redis_service, user_id=user_id, page=page, page_size=page_size)
            return payload
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_LIST_ERROR"}


    def _find_message_by_idempotency_key(
        self,
        *,
        messages: list[dict[str, Any]],
        role: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        target_role = str(role or "").strip().lower()
        target_key = str(idempotency_key or "").strip()
        if not target_role or not target_key:
            return None
        for item in messages:
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").strip().lower() != target_role:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if str(metadata.get("idempotency_key") or "").strip() == target_key:
                return item
        return None

    def _normalize_terminal_status(self, value: Any, *, default: str = "done") -> str:
        status = str(value or "").strip().lower() or default
        if status not in self._terminal_task_status_set.union(self._live_task_status_set):
            return default
        return status

    def _message_terminal_status(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        return self._normalize_terminal_status(
            item.get("status") or metadata.get("task_status") or metadata.get("terminal_status"),
            default="done",
        )

    def _is_completed_status(self, status: str) -> bool:
        normalized = self._normalize_terminal_status(status, default="done")
        return normalized in {"done", "completed"}

    def _terminal_status_rank(self, status: str) -> int:
        normalized = self._normalize_terminal_status(status, default="canceled")
        if normalized in {"done", "completed"}:
            return 4
        if normalized == "failed":
            return 3
        if normalized == "canceled":
            return 2
        if normalized == "expired":
            return 1
        if normalized in self._live_task_status_set:
            return 0
        return 1

    def _normalize_authority_terminal_status(self, value: Any, *, default: str = "done") -> str:
        normalized = self._normalize_terminal_status(value, default=default)
        if normalized == "completed":
            return "done"
        return normalized

    def _terminal_failure_metadata(self, *, terminal_status: str, terminal_event: dict[str, Any]) -> dict[str, Any]:
        if self._is_completed_status(terminal_status):
            return {}
        failure = terminal_event.get("failure") if isinstance(terminal_event.get("failure"), dict) else {}
        failure_stage = str(failure.get("stage") or "").strip() or "unknown"
        failure_code = str(failure.get("code") or "").strip()
        default_message = "已过期" if terminal_status == "expired" else ("已取消" if terminal_status == "canceled" else "处理失败")
        failure_message = str(failure.get("message") or "").strip() or default_message
        retriable_raw = failure.get("retriable")
        retriable = False if terminal_status in {"canceled", "expired"} else bool(retriable_raw)
        return {
            "failure_stage": failure_stage,
            "failure_code": failure_code,
            "failure_message": failure_message,
            "retriable": retriable,
        }

    def _build_authority_recent_turns(self, *, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        recent_turns: list[dict[str, Any]] = []
        for idx, item in enumerate(messages, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            status = self._normalize_authority_terminal_status(self._message_terminal_status(item))
            recent_turns.append(
                {
                    "message_id": str(item.get("message_id") or f"m_{idx:06d}"),
                    "role": role,
                    "content": str(item.get("content") or ""),
                    "created_at": self._to_iso(item.get("created_at"), fallback=self._now_iso()),
                    "trace_id": str(metadata.get("trace_id") or "").strip(),
                    "status": status,
                    "terminal_status": status,
                    **({"failure_stage": str(metadata.get("failure_stage") or "")} if str(metadata.get("failure_stage") or "").strip() else {}),
                    **({"failure_code": str(metadata.get("failure_code") or "")} if str(metadata.get("failure_code") or "").strip() else {}),
                    **({"failure_message": str(metadata.get("failure_message") or "")} if str(metadata.get("failure_message") or "").strip() else {}),
                    **({"retriable": bool(metadata.get("retriable"))} if "retriable" in metadata else {}),
                }
            )
        return recent_turns

    def _build_authority_summary(self, *, recent_turns: list[dict[str, Any]]) -> dict[str, Any]:
        summary_turns: list[dict[str, Any]] = []
        for item in recent_turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            status = self._normalize_terminal_status(item.get("status"), default="done")
            if role == "assistant" and not self._is_completed_status(status):
                continue
            summary_turns.append(item)
        return build_authority_summary(recent_turns=summary_turns)

    def _build_authority_conversation_state(self, *, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last_turn_route = ""
        last_focus_file_ids: list[int] = []
        last_assistant_trace_id = ""
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").strip().lower() != "assistant":
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            last_turn_route = str(metadata.get("route") or "").strip().lower()
            last_assistant_trace_id = str(metadata.get("trace_id") or "").strip()
            seen: set[int] = set()
            used_files = metadata.get("used_files") if isinstance(metadata.get("used_files"), list) else []
            for file_item in used_files:
                if not isinstance(file_item, dict):
                    continue
                file_id = self._safe_int(file_item.get("file_id"), default=0)
                if file_id <= 0 or file_id in seen:
                    continue
                seen.add(file_id)
                last_focus_file_ids.append(file_id)
            break
        return {
            "last_turn_route": last_turn_route,
            "last_focus_file_ids": last_focus_file_ids,
            "last_assistant_trace_id": last_assistant_trace_id,
        }

    def _build_context_snapshot_payload(
        self,
        *,
        row: dict[str, Any],
        user_id: int,
        conversation_id: int,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        messages = self._prepare_response_messages(document.get("messages") or [])
        recent_turns = self._build_authority_recent_turns(messages=messages)
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        created_at = self._to_iso(meta.get("created_at") or row.get("created_at"), fallback=self._now_iso())
        updated_at = self._to_iso(meta.get("updated_at") or row.get("updated_at"), fallback=created_at)
        snapshot_version = self._safe_int(
            row.get("chat_json_version"),
            default=self._safe_int(meta.get("message_count"), default=len(recent_turns)),
        )
        return {
            "success": True,
            "data": {
                "conversation_id": int(conversation_id),
                "user_id": int(user_id),
                "snapshot_version": max(0, int(snapshot_version)),
                "updated_at": updated_at,
                "summary": self._build_authority_summary(recent_turns=recent_turns),
                "recent_turns": recent_turns,
                "conversation_state": self._build_authority_conversation_state(messages=messages),
            },
        }

    def get_conversation_detail(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            redis_service = self._get_redis_service()
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            cached = get_cached_conversation_detail(
                redis_service=redis_service,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if self._is_detail_cache_payload_fresh(row=row, payload=cached):
                return cached  # type: ignore[return-value]
            if cached is not None:
                self._invalidate_detail_cache(user_id=user_id, conversation_id=conversation_id)
            note_conversation_detail_miss()

            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                if bootstrapped:
                    self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
                    self._repo.set_message_count(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message_count=len(doc.get("messages") or []),
                        touch_updated_at=False,
                    )
                self._reconcile_deleted_file_cleanup(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            payload = self._build_conversation_detail_payload(
                row=row,
                conversation_id=conversation_id,
                user_id=user_id,
                document=doc,
                include_legacy_files_fallback=self._should_use_legacy_conversation_fallback(),
            )
            payload_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            message_count = self._safe_int(payload_data.get("message_count"), default=0)
            cached_message_count = self._safe_int(row.get("message_count"), default=0)
            if cached_message_count != message_count:
                self._repo.set_message_count(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    message_count=message_count,
                    touch_updated_at=False,
                )
                row["message_count"] = message_count
                self._refresh_primary_list_cache(user_id=user_id)
            cache_conversation_detail(
                redis_service=redis_service,
                user_id=user_id,
                conversation_id=conversation_id,
                payload=payload,
            )
            return payload
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONVERSATION_FETCH_ERROR"}


    def get_conversation_context_snapshot(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                if bootstrapped:
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=document,
                    )
                    self._repo.set_message_count(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message_count=len(document.get("messages") or []),
                        touch_updated_at=False,
                    )
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                self._refresh_detail_cache(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            return self._build_context_snapshot_payload(
                row=row,
                user_id=user_id,
                conversation_id=conversation_id,
                document=document,
            )
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONTEXT_SNAPSHOT_ERROR"}

    def get_latest_turn_context(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            cached_data = self._get_cached_detail_data(user_id=user_id, conversation_id=conversation_id, row=row)
            if cached_data is None:
                detail_result = self.get_conversation_detail(user_id=user_id, conversation_id=conversation_id)
                if not detail_result.get("success"):
                    return detail_result
                cached_data = detail_result.get("data") if isinstance(detail_result.get("data"), dict) else {}
            messages = cached_data.get("messages") if isinstance(cached_data.get("messages"), list) else []
            last_turn_route = ""
            last_focus_file_ids: list[int] = []
            last_trace_id = ""
            for item in reversed(messages):
                if not isinstance(item, dict):
                    continue
                if str(item.get("role") or "").strip().lower() != "assistant":
                    continue
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                last_turn_route = str(metadata.get("route") or "").strip().lower()
                last_trace_id = str(metadata.get("trace_id") or "").strip()
                used_files = metadata.get("used_files") if isinstance(metadata.get("used_files"), list) else []
                seen: set[int] = set()
                for file_item in used_files:
                    if not isinstance(file_item, dict):
                        continue
                    file_id = self._safe_int(file_item.get("file_id"), default=0)
                    if file_id <= 0 or file_id in seen:
                        continue
                    seen.add(file_id)
                    last_focus_file_ids.append(file_id)
                if last_turn_route or last_focus_file_ids:
                    break
            return {
                "success": True,
                "data": {
                    "conversation_id": int(conversation_id),
                    "last_turn_route": last_turn_route,
                    "last_focus_file_ids": last_focus_file_ids,
                    "trace_id": last_trace_id,
                },
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "CONTEXT_FETCH_ERROR"}


    def add_authority_user_message(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        source_service: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        idempotency_key: str,
        content: str,
        context_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        content_text = str(content or "").strip()
        idempotency_text = str(idempotency_key or "").strip()
        source_service_text = str(source_service or "").strip()
        if not content_text:
            return {"success": False, "error": "empty_content", "code": "VALIDATION_ERROR"}
        if not idempotency_text:
            return {"success": False, "error": "idempotency_key_required", "code": "VALIDATION_ERROR"}
        if source_service_text not in {"fastQA", "highThinkingQA", "patentQA"}:
            return {"success": False, "error": "invalid_source_service", "code": "VALIDATION_ERROR"}
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            deduped = False
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                existing = self._find_message_by_idempotency_key(
                    messages=messages,
                    role="user",
                    idempotency_key=idempotency_text,
                )
                if isinstance(existing, dict):
                    message_payload = existing
                    deduped = True
                else:
                    now_iso = self._now_iso()
                    metadata = {
                        "trace_id": str(trace_id or "").strip(),
                        "source_service": source_service_text,
                        "route": str(route or "").strip(),
                        "requested_mode": str(requested_mode or "").strip(),
                        "actual_mode": str(actual_mode or "").strip(),
                        "idempotency_key": idempotency_text,
                        "context_hints": dict(context_hints or {}),
                    }
                    message_payload = {
                        "message_id": self._next_message_id(messages),
                        "role": "user",
                        "content": content_text,
                        "created_at": now_iso,
                        "status": "done",
                        "metadata": metadata,
                    }
                    messages.append(message_payload)
                    document["messages"] = messages
                    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                    meta["title"] = str(row.get("title") or meta.get("title") or "New Conversation")
                    meta["updated_at"] = now_iso
                    meta["message_count"] = len(messages)
                    meta["last_message_at"] = now_iso
                    document["meta"] = meta
                    self._persist_document_and_index(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=document,
                    )
                    self._repo.set_message_count(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message_count=len(messages),
                        touch_updated_at=False,
                    )
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                self._refresh_detail_cache(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            self._refresh_primary_list_cache(user_id=user_id)
            return {
                "success": True,
                "conversation_id": int(conversation_id),
                "message_id": str(message_payload.get("message_id") or ""),
                "trace_id": str(trace_id or "").strip(),
                "idempotency_key": idempotency_text,
                "created_at": self._to_iso(message_payload.get("created_at"), fallback=self._now_iso()),
                "deduped": deduped,
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_USER_WRITE_ERROR"}

    def create_authority_task_turn(
        self,
        *,
        user_id: int,
        conversation_id: int,
        task_id: str,
        trace_id: str,
        source_service: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        content: str,
        context_hints: dict[str, Any] | None = None,
        status: str = "queued",
        last_seq: int = 0,
    ) -> dict[str, Any]:
        content_text = str(content or "").strip()
        source_service_text = str(source_service or "").strip()
        user_idempotency_key = f"{conversation_id}:{str(task_id or '').strip()}:user"
        if not content_text:
            return {"success": False, "error": "empty_content", "code": "VALIDATION_ERROR"}
        if source_service_text not in {"fastQA", "highThinkingQA", "patentQA"}:
            return {"success": False, "error": "invalid_source_service", "code": "VALIDATION_ERROR"}
        live_status = self._normalize_terminal_status(status, default="queued")
        if live_status not in self._live_task_status_set:
            return {"success": False, "error": "invalid_task_status", "code": "VALIDATION_ERROR"}
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            deduped = False
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                now_iso = self._now_iso()

                user_message = self._find_message_by_idempotency_key(
                    messages=messages,
                    role="user",
                    idempotency_key=user_idempotency_key,
                )
                if isinstance(user_message, dict):
                    deduped = True
                else:
                    user_message = {
                        "message_id": self._next_message_id(messages),
                        "role": "user",
                        "content": content_text,
                        "created_at": now_iso,
                        "status": "done",
                        "metadata": {
                            "trace_id": str(trace_id or "").strip(),
                            "source_service": source_service_text,
                            "route": str(route or "").strip(),
                            "requested_mode": str(requested_mode or "").strip(),
                            "actual_mode": str(actual_mode or "").strip(),
                            "idempotency_key": user_idempotency_key,
                            "context_hints": dict(context_hints or {}),
                        },
                    }
                    messages.append(user_message)

                existing = self._find_task_placeholder(messages=messages, task_id=task_id)
                if isinstance(existing, dict):
                    assistant_message = dict(existing)
                    deduped = True
                else:
                    assistant_message = {
                        "message_id": self._next_message_id(messages),
                        "role": "assistant",
                        "content": "",
                        "created_at": now_iso,
                        "status": live_status,
                        "metadata": {
                            "task_id": str(task_id or "").strip(),
                            "task_status": live_status,
                            "trace_id": str(trace_id or "").strip(),
                            "source_service": source_service_text,
                            "route": str(route or "").strip(),
                            "requested_mode": str(requested_mode or "").strip(),
                            "actual_mode": str(actual_mode or "").strip(),
                            "last_seq": max(0, int(last_seq)),
                            "steps": [],
                        },
                    }
                    messages.append(assistant_message)
                metadata = assistant_message.get("metadata") if isinstance(assistant_message.get("metadata"), dict) else {}
                current_status = self._message_terminal_status(assistant_message)
                current_last_seq = max(0, self._safe_int(metadata.get("last_seq"), default=0))
                if current_status in {"completed", "failed", "canceled", "expired"}:
                    live_status = current_status
                else:
                    metadata["task_id"] = str(task_id or "").strip()
                    metadata["trace_id"] = str(trace_id or "").strip()
                    metadata["source_service"] = source_service_text
                    metadata["route"] = str(route or "").strip()
                    metadata["requested_mode"] = str(requested_mode or "").strip()
                    metadata["actual_mode"] = str(actual_mode or "").strip()
                    metadata["task_status"] = (
                        current_status
                        if current_status in self._live_task_status_set
                        and self._terminal_status_rank(current_status) >= self._terminal_status_rank(live_status)
                        else live_status
                    )
                    metadata["last_seq"] = max(current_last_seq, max(0, int(last_seq)))
                    assistant_message["status"] = str(metadata.get("task_status") or live_status)
                    assistant_message["metadata"] = metadata
                    self._replace_message(
                        messages=messages,
                        message_id=str(assistant_message.get("message_id") or ""),
                        next_message=assistant_message,
                    )
                    live_status = str(assistant_message.get("status") or live_status)

                document["messages"] = messages
                meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                meta["title"] = str(row.get("title") or meta.get("title") or "New Conversation")
                if live_status in self._live_task_status_set:
                    meta["active_task_id"] = str(task_id or "").strip()
                elif str(meta.get("active_task_id") or "").strip() == str(task_id or "").strip():
                    meta.pop("active_task_id", None)
                meta["updated_at"] = now_iso
                meta["message_count"] = len(messages)
                meta["last_message_at"] = now_iso
                document["meta"] = meta
                self._persist_task_runtime_document(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            return {
                "success": True,
                "conversation_id": int(conversation_id),
                "task_id": str(task_id or "").strip(),
                "user_message_id": str(user_message.get("message_id") or ""),
                "assistant_message_id": str(assistant_message.get("message_id") or ""),
                "status": str(live_status or "queued"),
                "deduped": deduped,
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_TASK_CREATE_ERROR"}

    def _find_task_placeholder(
        self,
        *,
        messages: list[dict[str, Any]],
        task_id: str,
    ) -> dict[str, Any] | None:
        lookup = str(task_id or "").strip()
        if not lookup:
            return None
        for item in messages:
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").strip().lower() != "assistant":
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if str(metadata.get("task_id") or "").strip() == lookup:
                return item
        return None

    def _replace_message(
        self,
        *,
        messages: list[dict[str, Any]],
        message_id: str,
        next_message: dict[str, Any],
    ) -> None:
        lookup = str(message_id or "").strip()
        for index, item in enumerate(messages):
            if not isinstance(item, dict):
                continue
            if str(item.get("message_id") or "").strip() == lookup:
                messages[index] = next_message
                return

    def _task_mutation_response(self, *, conversation_id: int, task_id: str, assistant_message: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "conversation_id": int(conversation_id),
            "task_id": str(task_id or "").strip(),
            "assistant_message_id": str(assistant_message.get("message_id") or ""),
            "status": self._message_terminal_status(assistant_message),
        }

    def rollback_authority_task_creation(
        self,
        *,
        user_id: int,
        conversation_id: int,
        task_id: str,
        user_message_id: str = "",
        assistant_message_id: str = "",
        preserve_user_message: bool = False,
    ) -> dict[str, Any]:
        lookup_task_id = str(task_id or "").strip()
        lookup_user_message_id = str(user_message_id or "").strip()
        lookup_assistant_message_id = str(assistant_message_id or "").strip()
        expected_user_idempotency_key = f"{conversation_id}:{lookup_task_id}:user"
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            removed_count = 0
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                retained_messages: list[dict[str, Any]] = []
                for message in messages:
                    if not isinstance(message, dict):
                        retained_messages.append(message)
                        continue
                    role = str(message.get("role") or "").strip().lower()
                    message_id = str(message.get("message_id") or "").strip()
                    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
                    if role == "assistant":
                        bound_task_id = str(metadata.get("task_id") or "").strip()
                        if message_id == lookup_assistant_message_id or (lookup_task_id and bound_task_id == lookup_task_id):
                            removed_count += 1
                            continue
                    if role == "user":
                        idempotency_key = str(metadata.get("idempotency_key") or "").strip()
                        trace_id = str(metadata.get("trace_id") or "").strip()
                        if (
                            not preserve_user_message
                            and (
                                message_id == lookup_user_message_id
                                or idempotency_key == expected_user_idempotency_key
                                or (lookup_task_id and trace_id == lookup_task_id)
                            )
                        ):
                            removed_count += 1
                            continue
                    retained_messages.append(message)
                document["messages"] = retained_messages
                meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                if str(meta.get("active_task_id") or "").strip() == lookup_task_id:
                    meta.pop("active_task_id", None)
                meta["updated_at"] = self._now_iso()
                meta["message_count"] = len(retained_messages)
                document["meta"] = meta
                self._persist_task_runtime_document(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            return {
                "success": True,
                "conversation_id": int(conversation_id),
                "task_id": lookup_task_id,
                "removed_count": int(removed_count),
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_TASK_CREATE_ROLLBACK_ERROR"}

    def get_authority_task_binding(
        self,
        *,
        user_id: int,
        conversation_id: int,
        task_id: str,
    ) -> dict[str, Any] | None:
        row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            return None
        try:
            document, _ = self._load_or_bootstrap_document(
                row=row,
                conversation_id=conversation_id,
                user_id=user_id,
                prefer_cached_detail=False,
            )
        except Exception:
            return None
        messages = document.get("messages") if isinstance(document.get("messages"), list) else []
        placeholder = self._find_task_placeholder(messages=messages, task_id=task_id)
        if not isinstance(placeholder, dict):
            return None
        metadata = placeholder.get("metadata") if isinstance(placeholder.get("metadata"), dict) else {}
        return {
            "source_service": str(metadata.get("source_service") or "").strip(),
            "requested_mode": str(metadata.get("requested_mode") or "").strip(),
            "actual_mode": str(metadata.get("actual_mode") or "").strip(),
        }

    def _persist_task_runtime_document(
        self,
        *,
        row: dict[str, Any],
        user_id: int,
        conversation_id: int,
        document: dict[str, Any],
    ) -> None:
        self._persist_document_and_index(
            row=row,
            user_id=user_id,
            conversation_id=conversation_id,
            document=document,
        )
        self._repo.set_message_count(
            conversation_id=conversation_id,
            user_id=user_id,
            message_count=len(document.get("messages") or []),
            touch_updated_at=False,
        )
        row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
        self._refresh_detail_cache(
            row=row,
            user_id=user_id,
            conversation_id=conversation_id,
            document=document,
        )
        self._refresh_primary_list_cache(user_id=user_id)

    def start_authority_task_assistant(
        self,
        *,
        user_id: int,
        conversation_id: int,
        task_id: str,
        trace_id: str,
        source_service: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        status: str,
        last_seq: int = 0,
    ) -> dict[str, Any]:
        live_status = self._normalize_terminal_status(status, default="queued")
        if live_status not in self._live_task_status_set:
            return {"success": False, "error": "invalid_task_status", "code": "VALIDATION_ERROR"}
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                existing = self._find_task_placeholder(messages=messages, task_id=task_id)
                now_iso = self._now_iso()
                if isinstance(existing, dict):
                    assistant_message = dict(existing)
                else:
                    assistant_message = {
                        "message_id": self._next_message_id(messages),
                        "role": "assistant",
                        "content": "",
                        "created_at": now_iso,
                        "status": live_status,
                        "metadata": {
                            "task_id": str(task_id or "").strip(),
                            "task_status": live_status,
                            "trace_id": str(trace_id or "").strip(),
                            "source_service": str(source_service or "").strip(),
                            "route": str(route or "").strip(),
                            "requested_mode": str(requested_mode or "").strip(),
                            "actual_mode": str(actual_mode or "").strip(),
                            "last_seq": max(0, int(last_seq)),
                            "steps": [],
                        },
                    }
                    messages.append(assistant_message)
                metadata = assistant_message.get("metadata") if isinstance(assistant_message.get("metadata"), dict) else {}
                current_status = self._message_terminal_status(assistant_message)
                current_last_seq = max(0, self._safe_int(metadata.get("last_seq"), default=0))
                if current_status in {"completed", "failed", "canceled", "expired"}:
                    document["messages"] = messages
                    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                    if current_status in self._live_task_status_set:
                        meta["active_task_id"] = str(task_id or "").strip()
                    elif str(meta.get("active_task_id") or "").strip() == str(task_id or "").strip():
                        meta.pop("active_task_id", None)
                    meta["updated_at"] = now_iso
                    meta["message_count"] = len(messages)
                    document["meta"] = meta
                    self._persist_task_runtime_document(
                        row=row,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        document=document,
                    )
                    return {
                        "success": True,
                        "conversation_id": int(conversation_id),
                        "task_id": str(task_id or "").strip(),
                        "assistant_message_id": str(assistant_message.get("message_id") or ""),
                        "status": current_status,
                    }
                metadata["task_id"] = str(task_id or "").strip()
                metadata["trace_id"] = str(trace_id or "").strip()
                metadata["source_service"] = str(source_service or "").strip()
                metadata["route"] = str(route or "").strip()
                metadata["requested_mode"] = str(requested_mode or "").strip()
                metadata["actual_mode"] = str(actual_mode or "").strip()
                metadata["task_status"] = current_status if current_status in self._live_task_status_set and self._terminal_status_rank(current_status) >= self._terminal_status_rank(live_status) else live_status
                metadata["last_seq"] = max(current_last_seq, max(0, int(last_seq)))
                assistant_message["status"] = str(metadata.get("task_status") or live_status)
                assistant_message["metadata"] = metadata
                self._replace_message(
                    messages=messages,
                    message_id=str(assistant_message.get("message_id") or ""),
                    next_message=assistant_message,
                )
                document["messages"] = messages
                meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                meta["active_task_id"] = str(task_id or "").strip()
                meta["updated_at"] = now_iso
                meta["message_count"] = len(messages)
                document["meta"] = meta
                self._persist_task_runtime_document(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            return {
                "success": True,
                "conversation_id": int(conversation_id),
                "task_id": str(task_id or "").strip(),
                "assistant_message_id": str(assistant_message.get("message_id") or ""),
                "status": str(assistant_message.get("status") or live_status),
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_TASK_START_ERROR"}

    def progress_authority_task_assistant(
        self,
        *,
        user_id: int,
        conversation_id: int,
        task_id: str,
        status: str,
        content_delta: str,
        steps: list[dict[str, Any]] | None,
        last_seq: int,
    ) -> dict[str, Any]:
        live_status = self._normalize_terminal_status(status, default="running")
        if live_status not in self._live_task_status_set:
            return {"success": False, "error": "invalid_task_status", "code": "VALIDATION_ERROR"}
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                assistant_message = self._find_task_placeholder(messages=messages, task_id=task_id)
                if not isinstance(assistant_message, dict):
                    return {"success": False, "error": "task_placeholder_not_found", "code": "NOT_FOUND"}
                current_status = self._message_terminal_status(assistant_message)
                if current_status in {"completed", "failed", "canceled", "expired"}:
                    return self._task_mutation_response(
                        conversation_id=conversation_id,
                        task_id=task_id,
                        assistant_message=assistant_message,
                    )
                updated = dict(assistant_message)
                metadata = updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {}
                current_last_seq = max(0, self._safe_int(metadata.get("last_seq"), default=0))
                if int(last_seq) <= current_last_seq:
                    return self._task_mutation_response(
                        conversation_id=conversation_id,
                        task_id=task_id,
                        assistant_message=assistant_message,
                    )
                delta = str(content_delta or "")
                if delta:
                    updated["content"] = f"{str(updated.get('content') or '')}{delta}"
                metadata["task_id"] = str(task_id or "").strip()
                metadata["task_status"] = live_status
                metadata["last_seq"] = max(0, int(last_seq))
                if steps is not None:
                    metadata["steps"] = list(steps)
                updated["status"] = live_status
                updated["metadata"] = metadata
                self._replace_message(
                    messages=messages,
                    message_id=str(updated.get("message_id") or ""),
                    next_message=updated,
                )
                document["messages"] = messages
                meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                meta["active_task_id"] = str(task_id or "").strip()
                meta["updated_at"] = self._now_iso()
                meta["message_count"] = len(messages)
                document["meta"] = meta
                self._persist_task_runtime_document(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            return self._task_mutation_response(
                conversation_id=conversation_id,
                task_id=task_id,
                assistant_message=updated,
            )
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_TASK_PROGRESS_ERROR"}

    def terminal_authority_task_assistant(
        self,
        *,
        user_id: int,
        conversation_id: int,
        task_id: str,
        terminal_status: str,
        last_seq: int,
        answer_text: str = "",
        steps: list[dict[str, Any]] | None = None,
        failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_status = self._normalize_terminal_status(terminal_status, default="completed")
        if normalized_status not in {"completed", "failed", "canceled", "expired"}:
            return {"success": False, "error": "invalid_terminal_status", "code": "VALIDATION_ERROR"}
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                assistant_message = self._find_task_placeholder(messages=messages, task_id=task_id)
                if not isinstance(assistant_message, dict):
                    return {"success": False, "error": "task_placeholder_not_found", "code": "NOT_FOUND"}
                current_status = self._message_terminal_status(assistant_message)
                if current_status in {"completed", "failed", "canceled", "expired"}:
                    return self._task_mutation_response(
                        conversation_id=conversation_id,
                        task_id=task_id,
                        assistant_message=assistant_message,
                    )
                updated = dict(assistant_message)
                metadata = updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {}
                if str(answer_text or "").strip():
                    updated["content"] = str(answer_text or "").strip()
                metadata["task_id"] = str(task_id or "").strip()
                metadata["task_status"] = normalized_status
                metadata["terminal_status"] = normalized_status
                metadata["last_seq"] = max(0, int(last_seq))
                if steps is not None:
                    metadata["steps"] = list(steps)
                failure_payload = dict(failure or {})
                if failure_payload:
                    metadata["failure"] = failure_payload
                else:
                    metadata.pop("failure", None)
                terminal_failure_metadata = self._terminal_failure_metadata(
                    terminal_status=normalized_status,
                    terminal_event={"failure": failure_payload},
                )
                if terminal_failure_metadata:
                    metadata.update(terminal_failure_metadata)
                else:
                    for field_name in ("failure_stage", "failure_code", "failure_message", "retriable"):
                        metadata.pop(field_name, None)
                updated["status"] = normalized_status
                updated["metadata"] = metadata
                self._replace_message(
                    messages=messages,
                    message_id=str(updated.get("message_id") or ""),
                    next_message=updated,
                )
                document["messages"] = messages
                meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                if str(meta.get("active_task_id") or "").strip() == str(task_id or "").strip():
                    meta.pop("active_task_id", None)
                meta["updated_at"] = self._now_iso()
                meta["message_count"] = len(messages)
                document["meta"] = meta
                self._persist_task_runtime_document(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            return self._task_mutation_response(
                conversation_id=conversation_id,
                task_id=task_id,
                assistant_message=updated,
            )
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_TASK_TERMINAL_ERROR"}

    def accept_authority_assistant_async(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        source_service: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        idempotency_key: str,
        final_event: dict[str, Any],
    ) -> dict[str, Any]:
        idempotency_text = str(idempotency_key or "").strip()
        source_service_text = str(source_service or "").strip()
        answer_text = str((final_event or {}).get("answer_text") or "").strip()
        if not idempotency_text:
            return {"success": False, "error": "idempotency_key_required", "code": "VALIDATION_ERROR"}
        if not answer_text:
            return {"success": False, "error": "empty_answer_text", "code": "VALIDATION_ERROR"}
        if source_service_text not in {"fastQA", "highThinkingQA", "patentQA"}:
            return {"success": False, "error": "invalid_source_service", "code": "VALIDATION_ERROR"}
        try:
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
                if not row:
                    return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                existing = self._find_message_by_idempotency_key(
                    messages=messages,
                    role="assistant",
                    idempotency_key=idempotency_text,
                )
                if isinstance(existing, dict):
                    existing_status = self._message_terminal_status(existing)
                    if self._terminal_status_rank("done") <= self._terminal_status_rank(existing_status):
                        return {
                            "success": True,
                            "accepted": True,
                            "conversation_id": int(conversation_id),
                            "event_id": f"assistant-async:{conversation_id}:{trace_id}",
                            "trace_id": str(trace_id or "").strip(),
                            "idempotency_key": idempotency_text,
                            "status": "accepted",
                            "deduped": True,
                        }
                if isinstance(existing, dict):
                    existing = None
                if existing is None:
                    queued = self._repo.enqueue_authority_assistant_task(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        trace_id=trace_id,
                        source_service=source_service_text,
                        route=route,
                        requested_mode=requested_mode,
                        actual_mode=actual_mode,
                        idempotency_key=idempotency_text,
                        final_event=dict(final_event or {}),
                    )
            return {
                "success": True,
                "accepted": True,
                "conversation_id": int(conversation_id),
                "event_id": f"assistant-async:{conversation_id}:{trace_id}",
                "trace_id": str(trace_id or "").strip(),
                "idempotency_key": idempotency_text,
                "status": "accepted",
                "deduped": bool(queued.get("deduped")),
                "task_id": int(queued.get("task_id") or 0),
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_ASSISTANT_ACCEPT_ERROR"}

    def accept_authority_assistant_terminal_async(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        source_service: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        idempotency_key: str,
        terminal_event: dict[str, Any],
    ) -> dict[str, Any]:
        idempotency_text = str(idempotency_key or "").strip()
        source_service_text = str(source_service or "").strip()
        if not idempotency_text:
            return {"success": False, "error": "idempotency_key_required", "code": "VALIDATION_ERROR"}
        if source_service_text not in {"fastQA", "highThinkingQA", "patentQA"}:
            return {"success": False, "error": "invalid_source_service", "code": "VALIDATION_ERROR"}
        try:
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
                if not row:
                    return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                existing = self._find_message_by_idempotency_key(
                    messages=messages,
                    role="assistant",
                    idempotency_key=idempotency_text,
                )
                if isinstance(existing, dict):
                    existing_status = self._message_terminal_status(existing)
                    incoming_status = self._normalize_terminal_status((terminal_event or {}).get("terminal_status"), default="done")
                    if self._terminal_status_rank(incoming_status) <= self._terminal_status_rank(existing_status):
                        return {
                            "success": True,
                            "accepted": True,
                            "conversation_id": int(conversation_id),
                            "event_id": f"assistant-terminal-async:{conversation_id}:{trace_id}",
                            "trace_id": str(trace_id or "").strip(),
                            "idempotency_key": idempotency_text,
                            "status": "accepted",
                            "deduped": True,
                        }
                if isinstance(existing, dict):
                    existing = None
                if existing is None:
                    queued = self._repo.enqueue_authority_assistant_terminal_task(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        trace_id=trace_id,
                        source_service=source_service_text,
                        route=route,
                        requested_mode=requested_mode,
                        actual_mode=actual_mode,
                        idempotency_key=idempotency_text,
                        terminal_event=dict(terminal_event or {}),
                    )
            return {
                "success": True,
                "accepted": True,
                "conversation_id": int(conversation_id),
                "event_id": f"assistant-terminal-async:{conversation_id}:{trace_id}",
                "trace_id": str(trace_id or "").strip(),
                "idempotency_key": idempotency_text,
                "status": "accepted",
                "deduped": bool(queued.get("deduped")),
                "task_id": int(queued.get("task_id") or 0),
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_ASSISTANT_TERMINAL_ACCEPT_ERROR"}

    def materialize_authority_assistant_task(self, *, task: dict[str, Any]) -> dict[str, Any]:
        refreshed_task = task
        task_id = self._safe_int(task.get("id"), default=0)
        if task_id > 0:
            latest = self._repo.get_authority_assistant_task(task_id=task_id)
            if isinstance(latest, dict):
                refreshed_task = latest
        metadata = refreshed_task.get("metadata") if isinstance(refreshed_task.get("metadata"), dict) else {}
        conversation_id = self._safe_int(refreshed_task.get("conversation_id"), default=0)
        user_id = self._safe_int(refreshed_task.get("user_id"), default=0)
        idempotency_text = str(metadata.get("idempotency_key") or "").strip()
        if conversation_id <= 0 or user_id <= 0 or not idempotency_text:
            return {"success": False, "error": "invalid_task_payload", "code": "VALIDATION_ERROR"}
        terminal_event = metadata.get("terminal_event") if isinstance(metadata.get("terminal_event"), dict) else {}
        final_event = metadata.get("final_event") if isinstance(metadata.get("final_event"), dict) else {}
        is_terminal = metadata.get("authority_assistant_terminal_async") is True or bool(terminal_event)
        event_payload = terminal_event if is_terminal else final_event
        terminal_status = self._normalize_terminal_status(
            event_payload.get("terminal_status") if is_terminal else "done",
            default="done",
        )
        answer_text = str(event_payload.get("answer_text") or refreshed_task.get("content") or "").strip()
        if self._is_completed_status(terminal_status) and not answer_text:
            return {"success": False, "error": "empty_answer_text", "code": "VALIDATION_ERROR"}
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                document, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                messages = document.get("messages") if isinstance(document.get("messages"), list) else []
                existing = self._find_message_by_idempotency_key(
                    messages=messages,
                    role="assistant",
                    idempotency_key=idempotency_text,
                )
                now_iso = self._now_iso()
                reference_objects = list(event_payload.get("reference_objects") or [])
                references = reference_objects or list(event_payload.get("references") or [])
                assistant_metadata = {
                    "trace_id": str(metadata.get("trace_id") or "").strip(),
                    "source_service": str(metadata.get("source_service") or "").strip(),
                    "route": str(metadata.get("route") or "").strip(),
                    "requested_mode": str(metadata.get("requested_mode") or "").strip(),
                    "actual_mode": str(metadata.get("actual_mode") or "").strip(),
                    "idempotency_key": idempotency_text,
                    "used_files": list(event_payload.get("used_files") or []),
                    "references": references,
                    "reference_objects": reference_objects or list(references),
                    "reference_links": list(event_payload.get("reference_links") or []),
                    "pdf_links": list(event_payload.get("pdf_links") or []),
                    "doi_locations": dict(event_payload.get("doi_locations") or {}),
                    "steps": list(event_payload.get("steps") or []),
                    "timings": dict(event_payload.get("timings") or {}),
                    "done_seen": bool(event_payload.get("done_seen")) if is_terminal else True,
                    "terminal_status": terminal_status,
                }
                assistant_metadata.update(self._terminal_failure_metadata(terminal_status=terminal_status, terminal_event=event_payload))
                built_payload = {
                    "message_id": self._next_message_id(messages),
                    "role": "assistant",
                    "content": answer_text,
                    "created_at": now_iso,
                    "status": terminal_status,
                    "metadata": assistant_metadata,
                    "references": references,
                    "reference_objects": reference_objects or list(references),
                    "reference_links": list(event_payload.get("reference_links") or []),
                    "pdf_links": list(event_payload.get("pdf_links") or []),
                    "doi_locations": dict(event_payload.get("doi_locations") or {}),
                    "steps": list(event_payload.get("steps") or []),
                    "done_seen": bool(event_payload.get("done_seen")) if is_terminal else True,
                    **({"failure_stage": assistant_metadata.get("failure_stage")} if str(assistant_metadata.get("failure_stage") or "").strip() else {}),
                    **({"failure_code": assistant_metadata.get("failure_code")} if str(assistant_metadata.get("failure_code") or "").strip() else {}),
                    **({"failure_message": assistant_metadata.get("failure_message")} if str(assistant_metadata.get("failure_message") or "").strip() else {}),
                    **({"retriable": bool(assistant_metadata.get("retriable"))} if "retriable" in assistant_metadata else {}),
                }
                if isinstance(existing, dict):
                    existing_status = self._message_terminal_status(existing)
                    if self._terminal_status_rank(terminal_status) <= self._terminal_status_rank(existing_status):
                        message_payload = existing
                    else:
                        built_payload["message_id"] = str(existing.get("message_id") or built_payload.get("message_id") or "")
                        built_payload["created_at"] = existing.get("created_at") or built_payload.get("created_at")
                        for index, item in enumerate(messages):
                            if not isinstance(item, dict):
                                continue
                            if str(item.get("message_id") or "") == str(existing.get("message_id") or ""):
                                messages[index] = built_payload
                                break
                        message_payload = built_payload
                else:
                    messages.append(built_payload)
                    message_payload = built_payload
                document["messages"] = messages
                meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
                meta["title"] = str(row.get("title") or meta.get("title") or "New Conversation")
                meta["updated_at"] = now_iso
                meta["message_count"] = len(messages)
                meta["last_message_at"] = now_iso
                document["meta"] = meta
                self._persist_document_and_index(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
                self._repo.set_message_count(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    message_count=len(messages),
                    touch_updated_at=False,
                )
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                self._refresh_detail_cache(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    document=document,
                )
            self._refresh_primary_list_cache(user_id=user_id)
            return {
                "success": True,
                "conversation_id": int(conversation_id),
                "message_id": str(message_payload.get("message_id") or ""),
                "trace_id": str(metadata.get("trace_id") or "").strip(),
                "idempotency_key": idempotency_text,
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "AUTHORITY_ASSISTANT_MATERIALIZE_ERROR"}

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
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                doc, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []
                now_iso = self._now_iso()
                message_status = "done"
                if role_text == "assistant" and isinstance(metadata, dict):
                    message_status = self._normalize_terminal_status(metadata.get("terminal_status"), default="done")
                message_payload: dict[str, Any] = {
                    "message_id": self._next_message_id(messages),
                    "role": role_text,
                    "content": content_text,
                    "created_at": now_iso,
                    "status": message_status,
                    "metadata": metadata or {},
                }
                if role_text == "assistant" and isinstance(metadata, dict):
                    if metadata.get("query_mode"):
                        message_payload["query_mode"] = metadata.get("query_mode")
                    if isinstance(metadata.get("references"), list):
                        message_payload["references"] = metadata.get("references")
                    if isinstance(metadata.get("reference_objects"), list):
                        message_payload["reference_objects"] = metadata.get("reference_objects")
                    if isinstance(metadata.get("reference_links"), list):
                        message_payload["reference_links"] = metadata.get("reference_links")
                    if isinstance(metadata.get("pdf_links"), list):
                        message_payload["pdf_links"] = metadata.get("pdf_links")
                    if isinstance(metadata.get("doi_locations"), dict):
                        message_payload["doi_locations"] = metadata.get("doi_locations")
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
                self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
                self._repo.set_message_count(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    message_count=len(messages),
                    touch_updated_at=False,
                )
                row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
                numeric_id = self._message_numeric_id(message_payload, default_value=len(messages))
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
            self._refresh_primary_list_cache(user_id=user_id)
            self._refresh_detail_cache(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            return {"success": True, "data": {"message_id": int(numeric_id), "conversation_id": int(conversation_id)}}
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "MESSAGE_ADD_ERROR"}

    def _delete_conversation_json_artifacts(self, *, row: dict[str, Any], user_id: int, conversation_id: int) -> None:
        local_path = self._json_store.conversation_local_path(user_id=user_id, conversation_id=conversation_id)
        try:
            if local_path.exists():
                local_path.unlink()
        except Exception:
            pass

        lock_path = local_path.with_suffix(".lock")
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception:
            pass

        storage_ref = str(row.get("chat_json_storage_ref") or "").strip()
        parsed = storage_service.parse_storage_ref(storage_ref)
        if parsed and parsed.get("scheme") == "minio" and parsed.get("object_name"):
            try:
                backend = get_storage_backend(project_root=str(self._workspace_root))
                backend.delete_object(
                    object_name=str(parsed.get("object_name") or ""),
                    bucket=str(parsed.get("bucket") or "") or None,
                )
            except Exception:
                pass

    def delete_conversation(self, *, user_id: int, conversation_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                if not files and self._should_use_legacy_conversation_fallback():
                    files = self._normalize_json_files(
                        self._repo.list_uploaded_files(conversation_id=conversation_id, user_id=user_id)
                    )
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    self._cleanup_uploaded_file_resources(file_row=item)
            deleted = self._repo.delete_conversation(conversation_id=conversation_id, user_id=user_id)
            if int(deleted or 0) <= 0:
                return {"success": False, "error": "conversation_delete_failed", "code": "CONVERSATION_DELETE_ERROR"}
            self._delete_conversation_json_artifacts(row=row, user_id=user_id, conversation_id=conversation_id)
            self._refresh_primary_list_cache(user_id=user_id)
            self._invalidate_detail_cache(user_id=user_id, conversation_id=conversation_id)
            return {"success": True, "message": "deleted"}
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
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
            file_id = 0
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
            if int(file_id or 0) <= 0:
                return {"success": False, "error": "file_record_insert_failed", "code": "FILE_RECORD_ERROR"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                current_max_file_no = 0
                existing_index = -1
                for idx, item in enumerate(files):
                    current_max_file_no = max(current_max_file_no, self._safe_int(item.get("file_no"), default=0))
                    if self._safe_int(item.get("file_id"), default=0) == int(file_id):
                        existing_index = idx
                now_iso = self._now_iso()
                payload = {
                    "file_no": current_max_file_no + 1,
                    "file_id": int(file_id),
                    "file_type": file_type_text,
                    "file_name": file_name.strip(),
                    "local_path": str(local_path or ""),
                    "storage_ref": str(storage_ref or ""),
                    "content_type": str(content_type or ""),
                    "size_bytes": self._safe_int(size_bytes, default=0),
                    "uploaded_at": now_iso,
                    "file_status": "active",
                    "parse_status": "uploaded",
                    "index_status": "pending",
                    "processing_stage": "uploaded",
                    "status_updated_at": now_iso,
                    "last_error": "",
                    "file_meta": {},
                    "deleted_at": None,
                    "deleted_by": None,
                }
                if existing_index >= 0:
                    payload["file_no"] = self._safe_int(files[existing_index].get("file_no"), default=payload["file_no"])
                    files[existing_index] = {**files[existing_index], **payload}
                else:
                    files.append(payload)
                files.sort(key=lambda item: (self._safe_int(item.get("file_no"), 0), self._safe_int(item.get("file_id"), 0)))
                doc["files"] = files
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["updated_at"] = self._now_iso()
                doc["meta"] = meta
                self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id) or row
            self._refresh_primary_list_cache(user_id=user_id)
            self._refresh_detail_cache(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            return {
                "success": True,
                "data": {
                    "file_id": int(file_id),
                    "conversation_id": int(conversation_id),
                    "file_type": file_type_text,
                    "file_name": file_name.strip(),
                },
            }
        except DatabaseUnavailableError as exc:
            if int(file_id or 0) > 0:
                self._rollback_uploaded_file_insert(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    file_id=int(file_id),
                )
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            if int(file_id or 0) > 0:
                self._rollback_uploaded_file_insert(
                    row=row,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    file_id=int(file_id),
                )
            return {"success": False, "error": str(exc), "code": "FILE_RECORD_ERROR"}

    def list_uploaded_files(self, *, user_id: int, conversation_id: int, include_deleted: bool = False) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            cached_data = self._get_cached_detail_data(user_id=user_id, conversation_id=conversation_id, row=row)
            if cached_data is not None:
                cached_files = cached_data.get("uploaded_files_all" if include_deleted else "uploaded_files")
                if isinstance(cached_files, list):
                    return {"success": True, "data": {"files": cached_files}}

            detail_result = self.get_conversation_detail(user_id=user_id, conversation_id=conversation_id)
            if detail_result.get("success"):
                detail_data = detail_result.get("data") if isinstance(detail_result.get("data"), dict) else {}
                detail_files = detail_data.get("uploaded_files_all" if include_deleted else "uploaded_files")
                if isinstance(detail_files, list):
                    return {"success": True, "data": {"files": detail_files}}
            elif str(detail_result.get("code") or "").strip().upper() == "NOT_FOUND":
                return detail_result

            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                if bootstrapped:
                    self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
                self._reconcile_deleted_file_cleanup(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            raw_files = doc.get("files") if isinstance(doc.get("files"), list) else []
            files = self._prepare_response_files(
                files=raw_files,
                conversation_id=conversation_id,
                user_id=user_id,
                only_active=not bool(include_deleted),
            )
            if not raw_files and self._should_use_legacy_conversation_fallback():
                legacy_files = self._repo.list_uploaded_files(conversation_id=conversation_id, user_id=user_id)
                files = self._prepare_response_files(
                    files=self._normalize_json_files(legacy_files),
                    conversation_id=conversation_id,
                    user_id=user_id,
                    only_active=not bool(include_deleted),
                )
            return {"success": True, "data": {"files": files}}
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_LIST_ERROR"}

    def get_uploaded_file(self, *, user_id: int, conversation_id: int, file_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            cached_data = self._get_cached_detail_data(user_id=user_id, conversation_id=conversation_id, row=row)
            if cached_data is not None:
                cached_files = cached_data.get("uploaded_files_all")
                if isinstance(cached_files, list):
                    for item in cached_files:
                        if not isinstance(item, dict):
                            continue
                        if self._safe_int(item.get("id"), default=0) != int(file_id):
                            continue
                        if str(item.get("file_status") or "").strip().lower() != "active":
                            return {"success": False, "error": "file_deleted", "code": "NOT_FOUND"}
                        return {"success": True, "data": item}

            detail_result = self.get_conversation_detail(user_id=user_id, conversation_id=conversation_id)
            if detail_result.get("success"):
                detail_data = detail_result.get("data") if isinstance(detail_result.get("data"), dict) else {}
                detail_files = detail_data.get("uploaded_files_all")
                if isinstance(detail_files, list):
                    for item in detail_files:
                        if not isinstance(item, dict):
                            continue
                        if self._safe_int(item.get("id"), default=0) != int(file_id):
                            continue
                        if str(item.get("file_status") or "").strip().lower() != "active":
                            return {"success": False, "error": "file_deleted", "code": "NOT_FOUND"}
                        return {"success": True, "data": item}
            elif str(detail_result.get("code") or "").strip().upper() == "NOT_FOUND":
                return detail_result

            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prefer_cached_detail=False,
                )
                if bootstrapped:
                    self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
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
            if not self._should_use_legacy_conversation_fallback():
                return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
            file_row = self._repo.get_uploaded_file(conversation_id=conversation_id, user_id=user_id, file_id=file_id)
            if not file_row:
                return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
            legacy_item = self._prepare_response_files(
                files=self._normalize_json_files([file_row]),
                conversation_id=conversation_id,
                user_id=user_id,
                only_active=True,
            )
            if not legacy_item:
                return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
            return {"success": True, "data": legacy_item[0]}
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_FETCH_ERROR"}

    def remove_uploaded_file(self, *, user_id: int, conversation_id: int, file_id: int) -> dict[str, Any]:
        try:
            row = self._repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
            if not row:
                return {"success": False, "error": "conversation_not_found", "code": "NOT_FOUND"}
            removed_snapshot: dict[str, Any] | None = None
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, bootstrapped = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                if bootstrapped:
                    self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                target_idx = -1
                for idx, item in enumerate(files):
                    if self._safe_int(item.get("file_id"), default=0) == int(file_id):
                        target_idx = idx
                        break
                if target_idx < 0:
                    return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
                now_iso = self._now_iso()
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
                current_meta = current.get("file_meta") if isinstance(current.get("file_meta"), dict) else {}
                files[target_idx] = {
                    **current,
                    "file_status": "deleted",
                    "deleted_at": now_iso,
                    "deleted_by": int(user_id),
                    "status_updated_at": now_iso,
                    "file_meta": {**current_meta, "cleanup_pending": True, "cleanup_error": ""},
                }
                removed_snapshot = dict(files[target_idx])
                doc["files"] = files
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["updated_at"] = now_iso
                doc["meta"] = meta
                self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            cleanup_result = self._cleanup_uploaded_file_resources(file_row=removed_snapshot or {})
            cleanup_pending = bool(cleanup_result.get("errors"))
            cleanup_error = "; ".join(str(item) for item in (cleanup_result.get("errors") or []) if str(item).strip())[:2000]
            with self._json_store.conversation_lock(user_id=user_id, conversation_id=conversation_id):
                doc, _ = self._load_or_bootstrap_document(
                    row=row,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                for idx, item in enumerate(files):
                    if not isinstance(item, dict):
                        continue
                    if self._safe_int(item.get("file_id"), default=0) != int(file_id):
                        continue
                    current_meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
                    files[idx] = {
                        **item,
                        "status_updated_at": self._now_iso(),
                        "file_meta": self._build_cleanup_meta_patch(
                            current_meta=current_meta,
                            cleanup_result=cleanup_result,
                        ),
                    }
                    break
                doc["files"] = files
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["updated_at"] = self._now_iso()
                doc["meta"] = meta
                self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            self._refresh_primary_list_cache(user_id=user_id)
            self._refresh_detail_cache(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            return {
                "success": True,
                "data": {
                    "conversation_id": int(conversation_id),
                    "file_id": int(file_id),
                    "file_status": "deleted",
                    "already_deleted": False,
                    "cleanup_pending": cleanup_pending,
                    "cleanup_error": cleanup_error,
                },
            }
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_DELETE_ERROR"}

    def update_uploaded_file_processing_state(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_id: int,
        parse_status: str | None = None,
        index_status: str | None = None,
        processing_stage: str | None = None,
        last_error: str | None = None,
        file_meta_patch: dict[str, Any] | None = None,
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
                if bootstrapped:
                    self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
                files = doc.get("files") if isinstance(doc.get("files"), list) else []
                target_idx = -1
                for idx, item in enumerate(files):
                    if self._safe_int(item.get("file_id"), default=0) == int(file_id):
                        target_idx = idx
                        break
                if target_idx < 0:
                    return {"success": False, "error": "file_not_found", "code": "NOT_FOUND"}
                current = files[target_idx] if isinstance(files[target_idx], dict) else {}
                current_parse = self._normalize_parse_status(
                    parse_status if parse_status is not None else current.get("parse_status"),
                    default="uploaded",
                )
                current_index = self._normalize_index_status(
                    index_status if index_status is not None else current.get("index_status"),
                    default="pending",
                )
                stage_candidate = str(processing_stage or "").strip().lower()
                if stage_candidate not in self._processing_stage_set:
                    stage_candidate = self._derive_processing_stage(
                        parse_status=current_parse,
                        index_status=current_index,
                        fallback=current.get("processing_stage"),
                    )
                current_meta = current.get("file_meta") if isinstance(current.get("file_meta"), dict) else {}
                if isinstance(file_meta_patch, dict):
                    for key, value in file_meta_patch.items():
                        if value is None:
                            current_meta.pop(key, None)
                        else:
                            current_meta[str(key)] = value
                files[target_idx] = {
                    **current,
                    "parse_status": current_parse,
                    "index_status": current_index,
                    "processing_stage": stage_candidate,
                    "last_error": str(last_error or ""),
                    "status_updated_at": self._now_iso(),
                    "file_meta": current_meta,
                }
                doc["files"] = files
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                meta["updated_at"] = self._now_iso()
                doc["meta"] = meta
                self._persist_document_and_index(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            self._refresh_detail_cache(row=row, user_id=user_id, conversation_id=conversation_id, document=doc)
            item = self.get_uploaded_file(user_id=user_id, conversation_id=conversation_id, file_id=file_id)
            return item if item.get("success") else {"success": True, "data": {"file_id": file_id}}
        except DatabaseUnavailableError as exc:
            return {"success": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "code": "FILE_STATE_UPDATE_ERROR"}

    def resolve_uploaded_file_download(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_id: int,
    ) -> tuple[dict[str, Any], int, dict[str, Any] | None]:
        result = self.get_uploaded_file(user_id=user_id, conversation_id=conversation_id, file_id=file_id)
        status_code = self.status_code_for(result, ok_status=200)
        if not result.get("success"):
            return result, status_code, None
        use_proxy = str(os.getenv("MINIO_USE_PROXY", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
        try:
            expires_seconds = max(1, int(str(os.getenv("MINIO_DOWNLOAD_EXPIRES", "3600") or "3600").strip()))
        except Exception:
            expires_seconds = 3600
        download = storage_service.resolve_download(
            file_row=result.get("data") or {},
            project_root=str(self._workspace_root),
            use_proxy=use_proxy,
            expires_seconds=expires_seconds,
        )
        if download is None:
            return ({"success": False, "error": "file_unavailable", "code": "FILE_UNAVAILABLE"}, 404, None)
        return result, 200, download

    @staticmethod
    def _conversation_id_from_payload(payload: dict[str, Any] | None) -> int | None:
        raw = (payload or {}).get("conversation_id")
        try:
            conversation_id = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
        return conversation_id if conversation_id and conversation_id > 0 else None

    def persist_user_request(self, *, payload: dict[str, Any], context: Any, runtime: Any) -> None:
        _ = runtime
        if context is None:
            return
        conversation_id = self._conversation_id_from_payload(payload)
        if conversation_id is None:
            return
        question = str((payload or {}).get("question") or "").strip()
        if not question:
            return
        self.add_message(
            user_id=int(context.user_id),
            conversation_id=conversation_id,
            role="user",
            content=question,
            metadata={"source": "ask_stream"},
        )

    def persist_assistant_summary(
        self,
        *,
        summary: dict[str, Any],
        payload: dict[str, Any],
        context: Any,
        runtime: Any,
    ) -> None:
        _ = runtime
        if context is None or not bool((summary or {}).get("done_seen")):
            return
        conversation_id = self._conversation_id_from_payload(payload)
        if conversation_id is None:
            return
        content = str((summary or {}).get("assistant_content") or "").strip()
        if not content:
            return
        reference_objects = (summary or {}).get("reference_objects") or []
        references = reference_objects or ((summary or {}).get("references") or [])
        metadata = {
            "source": "ask_stream",
            "query_mode": str((summary or {}).get("query_mode") or ""),
            "references": references,
            "reference_objects": reference_objects or list(references),
            "reference_links": (summary or {}).get("reference_links") or [],
            "pdf_links": (summary or {}).get("pdf_links") or [],
            "doi_locations": (summary or {}).get("doi_locations") or {},
            "steps": (summary or {}).get("steps") or [],
            "route": str((summary or {}).get("route") or ""),
            "used_files": (summary or {}).get("used_files") or [],
            "timings": (summary or {}).get("timings") or {},
            "trace_id": str((summary or {}).get("trace_id") or ""),
            "file_selection": (summary or {}).get("file_selection") or {},
            "done_seen": True,
        }
        self.add_message(
            user_id=int(context.user_id),
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            metadata=metadata,
        )


conversation_service = ConversationService()


def set_conversation_service(service: ConversationService) -> None:
    global conversation_service
    conversation_service = service
