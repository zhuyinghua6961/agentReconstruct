"""Conversation JSON outbox worker."""

from __future__ import annotations

import hashlib
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from server.repositories.conversation_outbox_repository import ConversationOutboxRepository
from server.repositories.conversation_repository import ConversationRepository
from server.storage.base import StorageBackend
from server.storage.storage_factory import get_storage_backend


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = int(default)
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


@dataclass(frozen=True)
class ChatJsonOutboxConfig:
    batch_size: int = 100
    poll_interval_ms: int = 1000
    max_attempts: int = 20
    retry_base_seconds: int = 2
    retry_max_seconds: int = 300
    processing_timeout_seconds: int = 120
    retry_jitter_ratio: float = 0.2

    @classmethod
    def from_env(cls) -> "ChatJsonOutboxConfig":
        return cls(
            batch_size=_env_int("OUTBOX_WORKER_BATCH_SIZE", 100, minimum=1, maximum=1000),
            poll_interval_ms=_env_int("OUTBOX_WORKER_POLL_INTERVAL_MS", 1000, minimum=50, maximum=60000),
            max_attempts=_env_int("OUTBOX_MAX_ATTEMPTS", 20, minimum=1, maximum=1000),
            retry_base_seconds=_env_int("OUTBOX_RETRY_BASE_SECONDS", 2, minimum=1, maximum=3600),
            retry_max_seconds=_env_int("OUTBOX_RETRY_MAX_SECONDS", 300, minimum=1, maximum=86400),
            processing_timeout_seconds=_env_int(
                "OUTBOX_PROCESSING_TIMEOUT_SECONDS",
                120,
                minimum=1,
                maximum=86400,
            ),
            retry_jitter_ratio=0.2,
        )


class ChatJsonOutboxWorker:
    """Retry worker for conversation JSON mirror failures."""

    def __init__(
        self,
        *,
        outbox_repo: ConversationOutboxRepository | None = None,
        conversation_repo: ConversationRepository | None = None,
        storage_backend: StorageBackend | None = None,
        config: ChatJsonOutboxConfig | None = None,
        logger: logging.Logger | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        self._outbox_repo = outbox_repo or ConversationOutboxRepository()
        self._conversation_repo = conversation_repo or ConversationRepository()
        self._storage_backend = storage_backend
        self._config = config or ChatJsonOutboxConfig.from_env()
        self._logger = logger or logging.getLogger(__name__)
        self._sleep_fn = sleep_fn or time.sleep
        self._random_fn = random_fn or random.random

    @property
    def config(self) -> ChatJsonOutboxConfig:
        return self._config

    def run_once(self) -> dict[str, int]:
        reclaimed = self._outbox_repo.reclaim_stuck_processing(timeout_seconds=self._config.processing_timeout_seconds)
        tasks = self._outbox_repo.claim_due_tasks(limit=self._config.batch_size)

        result = {
            "enabled": 1,
            "reclaimed": int(reclaimed),
            "claimed": len(tasks),
            "done": 0,
            "retry": 0,
            "dead": 0,
            "stale": 0,
            "skipped": 0,
        }

        for task in tasks:
            outcome = self._process_task(task)
            if outcome in result:
                result[outcome] += 1
            else:
                result["skipped"] += 1
        return result

    def run_forever(self, *, max_loops: int | None = None) -> dict[str, int]:
        aggregate = {
            "loops": 0,
            "reclaimed": 0,
            "claimed": 0,
            "done": 0,
            "retry": 0,
            "dead": 0,
            "stale": 0,
            "skipped": 0,
        }

        while True:
            summary = self.run_once()
            aggregate["loops"] += 1
            aggregate["reclaimed"] += int(summary.get("reclaimed", 0))
            aggregate["claimed"] += int(summary.get("claimed", 0))
            aggregate["done"] += int(summary.get("done", 0))
            aggregate["retry"] += int(summary.get("retry", 0))
            aggregate["dead"] += int(summary.get("dead", 0))
            aggregate["stale"] += int(summary.get("stale", 0))
            aggregate["skipped"] += int(summary.get("skipped", 0))

            if max_loops is not None and aggregate["loops"] >= max(1, int(max_loops)):
                return aggregate

            self._sleep_fn(max(0.05, self._config.poll_interval_ms / 1000.0))

    def _process_task(self, task: dict[str, Any]) -> str:
        task_id = self._safe_int(task.get("id"), 0)
        if task_id <= 0:
            return "skipped"

        conversation_id = self._safe_int(task.get("conversation_id"), 0)
        user_id = self._safe_int(task.get("user_id"), 0)
        task_version = self._safe_int(task.get("json_version"), 0)
        local_path = str(task.get("local_path") or "").strip()
        object_name = str(task.get("object_name") or "").strip()
        expected_hash = str(task.get("content_hash") or "").strip().lower()

        if conversation_id <= 0 or user_id <= 0 or task_version <= 0 or not local_path or not object_name:
            self._outbox_repo.mark_dead(task_id=task_id, last_error="invalid_task_payload")
            return "dead"

        row = self._conversation_repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
        if not row:
            self._outbox_repo.mark_done(task_id=task_id, note="conversation_not_found")
            return "stale"

        current_version = self._safe_int((row or {}).get("chat_json_version"), 0)
        if current_version > task_version:
            self._outbox_repo.mark_done(task_id=task_id, note=f"stale_version:{task_version}<{current_version}")
            return "stale"

        file_path = Path(local_path)
        if not file_path.exists() or not file_path.is_file():
            return self._retry_or_dead(task, error="local_file_missing")

        if expected_hash:
            actual_hash = self._compute_file_hash(file_path)
            if actual_hash != expected_hash:
                self._logger.warning(
                    "outbox content hash mismatch (conversation=%s, version=%s, expected=%s, actual=%s)",
                    conversation_id,
                    task_version,
                    expected_hash,
                    actual_hash,
                )

        try:
            storage_backend = self._get_storage_backend()
            storage_ref = storage_backend.upload_file(
                local_path=str(file_path),
                object_name=object_name,
                content_type="application/json",
            )
            affected = self._conversation_repo.mark_chat_json_sync_ok(
                conversation_id=conversation_id,
                user_id=user_id,
                expected_version=task_version,
                storage_ref=storage_ref,
                updated_at=datetime.now(),
            )
            if int(affected) <= 0:
                latest = self._conversation_repo.get_conversation(conversation_id=conversation_id, user_id=user_id)
                latest_version = self._safe_int((latest or {}).get("chat_json_version"), 0)
                if latest and latest_version > task_version:
                    self._outbox_repo.mark_done(task_id=task_id, note=f"stale_after_upload:{task_version}<{latest_version}")
                    return "stale"
                return self._retry_or_dead(task, error="sync_index_update_failed")
            self._outbox_repo.mark_done(task_id=task_id, note="ok")
            return "done"
        except Exception as exc:  # pragma: no cover - runtime env specific
            return self._retry_or_dead(task, error=f"upload_failed:{exc}")

    def _retry_or_dead(self, task: dict[str, Any], *, error: str) -> str:
        task_id = self._safe_int(task.get("id"), 0)
        attempts = self._safe_int(task.get("attempt_count"), 0)
        next_attempt = attempts + 1

        if next_attempt >= self._config.max_attempts:
            self._outbox_repo.mark_dead(task_id=task_id, last_error=error)
            return "dead"

        backoff_seconds = self._compute_backoff_seconds(next_attempt)
        retry_at = datetime.now() + timedelta(seconds=backoff_seconds)
        self._outbox_repo.mark_retry(task_id=task_id, next_retry_at=retry_at, last_error=error)
        return "retry"

    def _compute_backoff_seconds(self, attempt_no: int) -> float:
        base = max(1, int(self._config.retry_base_seconds))
        maximum = max(base, int(self._config.retry_max_seconds))
        raw = min(maximum, base * (2 ** max(0, int(attempt_no) - 1)))
        jitter = max(0.0, min(1.0, float(self._config.retry_jitter_ratio)))
        if jitter <= 0:
            return float(raw)
        delta = (self._random_fn() * 2.0 - 1.0) * jitter
        return max(0.1, raw * (1.0 + delta))

    def _compute_file_hash(self, local_path: Path) -> str:
        digest = hashlib.sha256()
        with local_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _get_storage_backend(self) -> StorageBackend:
        if self._storage_backend is not None:
            return self._storage_backend
        self._storage_backend = get_storage_backend()
        return self._storage_backend

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)


__all__ = ["ChatJsonOutboxConfig", "ChatJsonOutboxWorker"]
