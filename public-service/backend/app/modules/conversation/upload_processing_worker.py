from __future__ import annotations

import csv
import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from app.core.db import Database
from app.core.db_locks import MySQLNamedLockLease
from app.integrations.redis import RedisLockManager, RedisRenewingLock, RedisService
from app.modules.storage.service import storage_service


_SKIP_LEASE = object()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = int(default)
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _env_int_alias(names: tuple[str, ...], default: int, *, minimum: int, maximum: int) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            value = int(str(raw).strip())
        except Exception:
            continue
        if value < minimum:
            return minimum
        if value > maximum:
            return maximum
        return value
    return _env_int("", default, minimum=minimum, maximum=maximum)


@dataclass(frozen=True)
class UploadProcessingConfig:
    enabled: bool = True
    max_workers: int = 2
    pdf_max_pages: int = 20

    @classmethod
    def from_env(cls) -> "UploadProcessingConfig":
        return cls(
            enabled=True,
            max_workers=_env_int_alias(
                ("UPLOAD_PROCESSING_WORKER_MAX_WORKERS", "UPLOAD_FILE_PROCESSING_MAX_WORKERS"),
                2,
                minimum=1,
                maximum=8,
            ),
            pdf_max_pages=_env_int_alias(
                ("UPLOAD_PROCESSING_MAX_PDF_PAGES", "UPLOAD_FILE_PROCESSING_MAX_PDF_PAGES"),
                20,
                minimum=1,
                maximum=200,
            ),
        )


class UploadProcessingWorker:
    def __init__(
        self,
        *,
        conversation_service: Any,
        extract_pdf_text_fn: Callable[..., str] | None,
        redis_service: RedisService | None = None,
        config: UploadProcessingConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._conversation_service = conversation_service
        self._extract_pdf_text_fn = extract_pdf_text_fn
        self._redis_service = redis_service
        self._redis_lock_manager = RedisLockManager(getattr(redis_service, "client", None))
        self._config = config or UploadProcessingConfig.from_env()
        self._logger = logger or logging.getLogger(__name__)
        self._workspace_root = Path(getattr(conversation_service, "_workspace_root", Path.cwd())).resolve()
        self._executor: ThreadPoolExecutor | None = None
        self._active_guard = Lock()
        self._active_keys: set[tuple[int, int, int]] = set()
        self._lock_ttl_seconds = _env_int("UPLOAD_PROCESSING_LOCK_TTL_SECONDS", 120, minimum=5, maximum=3600)

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    def submit(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_id: int,
        file_type: str,
        local_path: str | None,
    ) -> bool:
        if not self.enabled:
            return False
        key = (int(user_id), int(conversation_id), int(file_id))
        with self._active_guard:
            if key in self._active_keys:
                return False
            self._active_keys.add(key)
        future = self._ensure_executor().submit(
            self._run_task,
            user_id=int(user_id),
            conversation_id=int(conversation_id),
            file_id=int(file_id),
            file_type=str(file_type or "").strip().lower(),
            local_path=str(local_path or "").strip(),
        )
        future.add_done_callback(lambda fut, active_key=key: self._on_task_done(active_key, fut))
        return True

    def shutdown(self, *, wait: bool = False) -> None:
        if self._executor is None:
            return
        self._executor.shutdown(wait=wait, cancel_futures=True)
        self._executor = None

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._config.max_workers,
                thread_name_prefix="upload-processing",
            )
        return self._executor

    def _on_task_done(self, key: tuple[int, int, int], fut: Future) -> None:
        with self._active_guard:
            self._active_keys.discard(key)
        try:
            fut.result()
        except Exception as exc:
            self._logger.warning("upload processing task failed: key=%s, error=%s", key, exc)

    def _set_state(
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
    ) -> bool:
        result: dict[str, Any] = {"success": False}
        for attempt in range(1, 3):
            result = self._conversation_service.update_uploaded_file_processing_state(
                user_id=user_id,
                conversation_id=conversation_id,
                file_id=file_id,
                parse_status=parse_status,
                index_status=index_status,
                processing_stage=processing_stage,
                last_error=last_error,
                file_meta_patch=file_meta_patch,
            )
            if result.get("success"):
                break
            if str(result.get("code") or "").upper() != "NOT_FOUND" or attempt >= 2:
                break
            time.sleep(0.15)
        if not result.get("success"):
            self._logger.warning(
                "upload processing state update failed: conversation=%s, file=%s, result=%s",
                conversation_id,
                file_id,
                result,
            )
            return False
        return True

    def _build_processing_file_row(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_id: int,
        file_type: str,
        local_path: str,
    ) -> dict[str, Any]:
        suffix = ".pdf" if file_type == "pdf" else ".xlsx" if file_type == "excel" else ""
        fallback = {
            "id": int(file_id),
            "conversation_id": int(conversation_id),
            "user_id": int(user_id),
            "file_name": f"upload-{int(file_id)}{suffix}",
            "local_path": str(local_path or "").strip(),
            "storage_ref": "",
        }
        getter = getattr(self._conversation_service, "get_uploaded_file", None)
        if not callable(getter):
            return fallback
        try:
            result = getter(user_id=user_id, conversation_id=conversation_id, file_id=file_id)
        except Exception as exc:
            self._logger.warning(
                "upload processing metadata lookup failed: conversation=%s file=%s error=%s",
                conversation_id,
                file_id,
                exc,
            )
            return fallback
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not result.get("success") or not isinstance(data, dict):
            return fallback
        merged = dict(fallback)
        merged.update(data)
        if not str(merged.get("local_path") or "").strip() and fallback["local_path"]:
            merged["local_path"] = fallback["local_path"]
        if not str(merged.get("file_name") or "").strip():
            merged["file_name"] = fallback["file_name"]
        return merged

    def _resolve_processing_file(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_id: int,
        file_type: str,
        local_path: str,
    ) -> tuple[Path, bool]:
        file_row = self._build_processing_file_row(
            user_id=user_id,
            conversation_id=conversation_id,
            file_id=file_id,
            file_type=file_type,
            local_path=local_path,
        )
        preferred_local_path = Path(str(file_row.get("local_path") or "").strip()) if str(file_row.get("local_path") or "").strip() else None
        if preferred_local_path is not None and preferred_local_path.exists() and preferred_local_path.is_file():
            return preferred_local_path, False

        download = storage_service.resolve_download(
            file_row=file_row,
            project_root=str(self._workspace_root),
            use_proxy=True,
            expires_seconds=3600,
        )
        if download is None:
            raise FileNotFoundError(f"uploaded file not found: {local_path}")
        target = Path(str(download.get("target") or "").strip())
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"uploaded file materialization failed: {target}")
        return target, str(download.get("mode") or "") == "proxy_file"

    def _run_task(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_id: int,
        file_type: str,
        local_path: str,
    ) -> None:
        try:
            with self._processing_lease(user_id=user_id, conversation_id=conversation_id, file_id=file_id) as lease:
                if lease is _SKIP_LEASE:
                    return
                if lease is not None:
                    lease.ensure_healthy()
                state_ready = self._set_state(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    file_id=file_id,
                    parse_status="parsing",
                    processing_stage="parsing",
                    last_error="",
                )
                if not state_ready:
                    return
                if lease is not None:
                    lease.ensure_healthy()
                file_path: Path | None = None
                cleanup_after_parse = False
                try:
                    file_path, cleanup_after_parse = self._resolve_processing_file(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        file_id=file_id,
                        file_type=file_type,
                        local_path=local_path,
                    )

                    parse_meta = {"source_path": str(file_path)}
                    if file_type == "pdf":
                        parse_meta.update(self._parse_pdf(file_path))
                    elif file_type == "excel":
                        parse_meta.update(self._parse_table(file_path))
                    else:
                        raise ValueError(f"unsupported upload file_type={file_type!r}")
                finally:
                    if cleanup_after_parse and file_path is not None:
                        try:
                            if file_path.exists():
                                file_path.unlink()
                        except Exception as exc:
                            self._logger.warning(
                                "upload processing temp file cleanup failed: conversation=%s file=%s path=%s error=%s",
                                conversation_id,
                                file_id,
                                file_path,
                                exc,
                            )

                if lease is not None:
                    lease.ensure_healthy()
                if not self._set_state(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    file_id=file_id,
                    parse_status="parsed",
                    processing_stage="parsed",
                    file_meta_patch=parse_meta,
                ):
                    return
                if lease is not None:
                    lease.ensure_healthy()
                if not self._set_state(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    file_id=file_id,
                    index_status="indexing",
                    processing_stage="indexing",
                ):
                    return
                if lease is not None:
                    lease.ensure_healthy()
                self._set_state(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    file_id=file_id,
                    index_status="ready",
                    processing_stage="ready",
                    last_error="",
                    file_meta_patch={"index_mode": "deferred", "index_note": "runtime_query_indexing"},
                )
        except Exception as exc:
            self._set_state(
                user_id=user_id,
                conversation_id=conversation_id,
                file_id=file_id,
                parse_status="failed",
                index_status="failed",
                processing_stage="failed",
                last_error=str(exc),
                file_meta_patch={"processing_failed": True},
            )

    def _processing_lock_key(self, *, user_id: int, conversation_id: int, file_id: int) -> str:
        if self._redis_service is None:
            return f"upload-processing:{int(user_id)}:{int(conversation_id)}:{int(file_id)}"
        return self._redis_service.key_factory.lock(
            "upload-processing",
            int(user_id),
            int(conversation_id),
            int(file_id),
        )

    def _processing_lease(self, *, user_id: int, conversation_id: int, file_id: int):
        key = self._processing_lock_key(user_id=user_id, conversation_id=conversation_id, file_id=file_id)
        if self._redis_lock_manager.available:
            handle = self._redis_lock_manager.acquire(key, ttl_seconds=self._lock_ttl_seconds)
            if handle is not None:
                lease = RedisRenewingLock(
                    lock_manager=self._redis_lock_manager,
                    handle=handle,
                    logger=self._logger,
                    label="upload_processing_lock",
                ).start()

                class _LeaseContext:
                    def __enter__(self_nonlocal):
                        return lease

                    def __exit__(self_nonlocal, exc_type, exc, tb):
                        lease.release()
                        return False

                return _LeaseContext()
        else:
            database = self._conversation_database()
            if database is not None and not self._allow_unsafe_lock_fallback():
                lease = MySQLNamedLockLease.acquire(
                    database=database,
                    key=key,
                    wait_seconds=max(1, int(self._lock_ttl_seconds)),
                    label="upload_processing_lock",
                )
                if lease is not None:
                    class _LeaseContext:
                        def __enter__(self_nonlocal):
                            return lease

                        def __exit__(self_nonlocal, exc_type, exc, tb):
                            lease.release()
                            return False

                    return _LeaseContext()
            elif self._allow_unsafe_lock_fallback():
                return nullcontext(None)
        self._logger.info(
            "upload processing skipped because another instance holds the file lease: user=%s conversation=%s file=%s",
            user_id,
            conversation_id,
            file_id,
        )
        return nullcontext(_SKIP_LEASE)

    @staticmethod
    def _allow_unsafe_lock_fallback() -> bool:
        return str(os.getenv("APP_ENV", "development") or "development").strip().lower() == "test"

    def _conversation_database(self) -> Database | None:
        repo = getattr(self._conversation_service, "_repo", None)
        database = getattr(repo, "_db", None)
        return database if isinstance(database, Database) else None

    def _parse_pdf(self, file_path: Path) -> dict[str, Any]:
        if self._extract_pdf_text_fn is None:
            raise RuntimeError("pdf extractor unavailable")
        text = self._extract_pdf_text_fn(str(file_path), max_pages=self._config.pdf_max_pages, exclude_references=False)
        if not isinstance(text, str):
            raise RuntimeError("pdf parse output invalid")
        if text.startswith("[错误]"):
            raise RuntimeError(text)
        stripped = text.strip()
        if not stripped:
            raise RuntimeError("pdf parse result is empty")
        return {"parsed_char_count": len(stripped), "parsed_preview": stripped[:300]}

    def _parse_table(self, file_path: Path) -> dict[str, Any]:
        if file_path.suffix.lower() == ".csv":
            return self._parse_csv(file_path)
        return self._parse_excel(file_path)

    def _parse_csv(self, file_path: Path) -> dict[str, Any]:
        row_count = 0
        columns: list[str] = []
        sample_rows: list[list[str]] = []
        with file_path.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
            reader = csv.reader(fh)
            for idx, row in enumerate(reader):
                if idx == 0:
                    columns = [str(item) for item in row]
                    continue
                row_count += 1
                if len(sample_rows) < 3:
                    sample_rows.append([str(item) for item in row[:20]])
        return {
            "table_format": "csv",
            "row_count": row_count,
            "column_count": len(columns),
            "columns": columns[:50],
            "sample_rows": sample_rows,
        }

    def _parse_excel(self, file_path: Path) -> dict[str, Any]:
        try:
            import pandas as pd
        except Exception as exc:
            raise RuntimeError(f"pandas unavailable for excel parsing: {exc}") from exc
        try:
            head_df = pd.read_excel(file_path, nrows=20)
        except Exception as exc:
            raise RuntimeError(f"excel parse failed: {exc}") from exc
        return {
            "table_format": "excel",
            "row_count_estimate": int(len(head_df)),
            "column_count": int(len(head_df.columns)),
            "columns": [str(col) for col in list(head_df.columns)[:50]],
            "sample_rows": head_df.head(3).astype(str).values.tolist(),
        }


__all__ = ["UploadProcessingConfig", "UploadProcessingWorker"]
