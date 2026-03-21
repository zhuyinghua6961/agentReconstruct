from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, local
from typing import Any, Iterator

from app.core.config import get_settings
from app.integrations.redis import RedisLeaseLostError, RedisLockHandle, RedisLockManager, RedisRenewingLock, RedisService
from app.integrations.storage.base import StorageBackend
from app.integrations.storage.factory import get_storage_backend


class ConversationJsonStore:
    def __init__(
        self,
        *,
        project_root: str | None = None,
        logger: Any = None,
        storage_backend: StorageBackend | None = None,
        redis_service: RedisService | None = None,
    ) -> None:
        settings = get_settings()
        self._project_root = Path(project_root or settings.data_root).resolve()
        self._logger = logger
        self._storage_backend = storage_backend
        self._redis_service = redis_service
        self._redis_lock_manager = RedisLockManager(getattr(redis_service, "client", None))
        self._locks_guard = Lock()
        self._locks: dict[str, Lock] = {}
        self._thread_state = local()
        self._lock_ttl_seconds = self._env_int("CONVERSATION_LOCK_TTL_SECONDS", 30, minimum=5, maximum=300)
        self._lock_wait_seconds = self._env_int("CONVERSATION_LOCK_WAIT_SECONDS", 10, minimum=1, maximum=120)
        self._lock_retry_interval_ms = self._env_int("CONVERSATION_LOCK_RETRY_INTERVAL_MS", 100, minimum=10, maximum=1000)

        raw_base = str(os.getenv("CHAT_JSON_BASE_DIR", "data/conversations") or "data/conversations").strip()
        base_path = Path(raw_base).expanduser()
        if not base_path.is_absolute():
            base_path = (self._project_root / raw_base).resolve()
        else:
            base_path = base_path.resolve()
        self._base_dir = base_path
        self._object_prefix = os.getenv("CHAT_JSON_STORAGE_PREFIX", "conversations").strip("/") or "conversations"

    @staticmethod
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

    def _conversation_lock_key(self, *, user_id: int, conversation_id: int) -> str:
        if self._redis_service is None:
            return f"conversation:{int(user_id)}:{int(conversation_id)}"
        return self._redis_service.key_factory.lock("conversation", int(user_id), int(conversation_id))

    def _acquire_distributed_lock(self, *, user_id: int, conversation_id: int) -> RedisLockHandle | None:
        if not self._redis_lock_manager.available:
            return None
        deadline = time.monotonic() + float(self._lock_wait_seconds)
        key = self._conversation_lock_key(user_id=user_id, conversation_id=conversation_id)
        while True:
            handle = self._redis_lock_manager.acquire(key, ttl_seconds=self._lock_ttl_seconds)
            if handle is not None:
                return handle
            if time.monotonic() >= deadline:
                raise TimeoutError(f"conversation_lock_timeout:{user_id}:{conversation_id}")
            time.sleep(self._lock_retry_interval_ms / 1000.0)

    def conversation_local_path(self, *, user_id: int, conversation_id: int) -> Path:
        return self._base_dir / str(int(user_id)) / f"{int(conversation_id)}.json"

    def conversation_object_name(self, *, user_id: int, conversation_id: int) -> str:
        return f"{self._object_prefix}/{int(user_id)}/{int(conversation_id)}.json"

    def assert_lock_healthy(self) -> None:
        lease = getattr(self._thread_state, "conversation_lease", None)
        if isinstance(lease, RedisRenewingLock):
            lease.ensure_healthy()

    @contextmanager
    def conversation_lock(self, *, user_id: int, conversation_id: int) -> Iterator[None]:
        key = f"{int(user_id)}:{int(conversation_id)}"
        distributed_handle = self._acquire_distributed_lock(user_id=user_id, conversation_id=conversation_id)
        distributed_lease = RedisRenewingLock(
            lock_manager=self._redis_lock_manager,
            handle=distributed_handle,
            logger=self._logger,
            label="conversation_lock",
        ).start()
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = Lock()
                self._locks[key] = lock
        lock.acquire()
        lock_file = self.conversation_local_path(user_id=user_id, conversation_id=conversation_id).with_suffix(".lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = lock_file.open("a+", encoding="utf-8")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            self._thread_state.conversation_lease = distributed_lease if distributed_handle is not None else None
            yield
        finally:
            self._thread_state.conversation_lease = None
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            finally:
                fd.close()
            lock.release()
            if distributed_handle is not None and not distributed_lease.release():
                if self._logger is not None:
                    self._logger.warning(
                        "conversation redis lock release failed: user=%s conversation=%s",
                        user_id,
                        conversation_id,
                    )

    def document_exists(self, *, user_id: int, conversation_id: int) -> bool:
        return self.conversation_local_path(user_id=user_id, conversation_id=conversation_id).exists()

    def build_default_document(
        self,
        *,
        conversation_id: int,
        user_id: int,
        title: str,
        created_at: str,
        updated_at: str,
        message_count: int = 0,
        messages: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "meta": {
                "schema_version": "chatlog.v1",
                "conversation_id": int(conversation_id),
                "user_id": int(user_id),
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
                "message_count": max(0, int(message_count)),
                "last_message_at": updated_at if int(message_count) > 0 else None,
            },
            "messages": list(messages or []),
            "files": list(files or []),
            "runtime": {
                "last_request_id": "",
                "last_latency_ms": 0,
                "last_error": "",
            },
        }

    def load_document(self, *, user_id: int, conversation_id: int) -> dict[str, Any] | None:
        local_path = self.conversation_local_path(user_id=user_id, conversation_id=conversation_id)
        local_doc = self._load_document_from_local(local_path)
        if local_doc is not None:
            return local_doc
        remote_synced, remote_doc = self._sync_local_from_remote_if_needed(
            user_id=user_id,
            conversation_id=conversation_id,
            local_path=local_path,
        )
        if remote_synced:
            doc = self._load_document_from_local(local_path)
            if doc is not None:
                return doc
            if remote_doc is not None:
                return remote_doc
        return None

    def write_document(
        self,
        *,
        user_id: int,
        conversation_id: int,
        document: dict[str, Any],
        storage_ref_hint: str | None = None,
    ) -> dict[str, Any]:
        self.assert_lock_healthy()
        local_path = self.conversation_local_path(user_id=user_id, conversation_id=conversation_id)
        self._atomic_write_json(local_path=local_path, document=document)
        self.assert_lock_healthy()
        content_hash, size_bytes = self._compute_file_hash_and_size(local_path)

        storage_ref = storage_ref_hint or ""
        sync_status = "local_only"
        try:
            backend = self._get_storage_backend()
            storage_ref = backend.upload_file(
                local_path=str(local_path),
                object_name=self.conversation_object_name(user_id=user_id, conversation_id=conversation_id),
                content_type="application/json",
            )
            sync_status = "ok"
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(f"conversation json mirror failed: {exc}")
            if not storage_ref:
                sync_status = "sync_failed"
        self.assert_lock_healthy()

        return {
            "local_path": str(local_path),
            "storage_ref": storage_ref or None,
            "content_hash": content_hash,
            "size_bytes": size_bytes,
            "sync_status": sync_status,
        }

    def _get_storage_backend(self) -> StorageBackend:
        if self._storage_backend is not None:
            return self._storage_backend
        return get_storage_backend(project_root=str(self._project_root))

    def _load_document_from_local(self, local_path: Path) -> dict[str, Any] | None:
        if not local_path.exists() or not local_path.is_file():
            return None
        try:
            with local_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(f"conversation json load failed: {local_path} ({exc})")
        return None

    def _download_remote_copy(self, *, user_id: int, conversation_id: int, local_path: Path) -> bool:
        try:
            backend = self._get_storage_backend()
            local_path.parent.mkdir(parents=True, exist_ok=True)
            return bool(
                backend.download_file(
                    object_name=self.conversation_object_name(user_id=user_id, conversation_id=conversation_id),
                    local_path=str(local_path),
                )
            )
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(f"conversation json remote restore failed: {exc}")
            return False

    def _sync_local_from_remote_if_needed(
        self,
        *,
        user_id: int,
        conversation_id: int,
        local_path: Path,
    ) -> tuple[bool, dict[str, Any] | None]:
        remote_tmp_path = local_path.with_suffix(local_path.suffix + ".remote.tmp")
        try:
            downloaded = self._download_remote_copy(
                user_id=user_id,
                conversation_id=conversation_id,
                local_path=remote_tmp_path,
            )
            if not downloaded:
                return False, None

            remote_doc = self._load_document_from_local(remote_tmp_path)
            if remote_doc is None:
                if self._logger is not None:
                    self._logger.warning(
                        f"conversation json remote content invalid, keep local fallback: {remote_tmp_path}"
                    )
                return False, None

            local_path.parent.mkdir(parents=True, exist_ok=True)
            if not local_path.exists():
                os.replace(remote_tmp_path, local_path)
                return True, remote_doc

            local_hash, _ = self._compute_file_hash_and_size(local_path)
            remote_hash, _ = self._compute_file_hash_and_size(remote_tmp_path)
            if local_hash != remote_hash:
                if self._logger is not None:
                    self._logger.warning(
                        "conversation json local/remote mismatch detected; prefer remote copy "
                        f"(conversation={conversation_id}, user={user_id})"
                    )
                os.replace(remote_tmp_path, local_path)
            else:
                try:
                    remote_tmp_path.unlink()
                except Exception:
                    pass
            return True, remote_doc
        finally:
            if remote_tmp_path.exists():
                try:
                    remote_tmp_path.unlink()
                except Exception:
                    pass

    def _atomic_write_json(self, *, local_path: Path, document: dict[str, Any]) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(document, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, local_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _compute_file_hash_and_size(self, local_path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        with local_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), int(local_path.stat().st_size)
