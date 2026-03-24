from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = int(default)
    if value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


@dataclass(frozen=True)
class AuthorityAssistantInboxConfig:
    batch_size: int = 100
    max_attempts: int = 10
    retry_base_seconds: int = 2
    retry_max_seconds: int = 300
    processing_timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> "AuthorityAssistantInboxConfig":
        return cls(
            batch_size=_env_int("AUTHORITY_ASSISTANT_INBOX_BATCH_SIZE", 100, minimum=1, maximum=1000),
            max_attempts=_env_int("AUTHORITY_ASSISTANT_INBOX_MAX_ATTEMPTS", 10, minimum=1, maximum=1000),
            retry_base_seconds=_env_int("AUTHORITY_ASSISTANT_INBOX_RETRY_BASE_SECONDS", 2, minimum=1, maximum=3600),
            retry_max_seconds=_env_int("AUTHORITY_ASSISTANT_INBOX_RETRY_MAX_SECONDS", 300, minimum=1, maximum=86400),
            processing_timeout_seconds=_env_int("AUTHORITY_ASSISTANT_INBOX_PROCESSING_TIMEOUT_SECONDS", 120, minimum=1, maximum=86400),
        )


class AuthorityAssistantInboxWorker:
    def __init__(
        self,
        *,
        repository: Any,
        conversation_service: Any,
        logger_: logging.Logger | None = None,
        config: AuthorityAssistantInboxConfig | None = None,
    ) -> None:
        self._repository = repository
        self._conversation_service = conversation_service
        self._logger = logger_ or logger
        self._config = config or AuthorityAssistantInboxConfig.from_env()

    def run_once(self, *, limit: int | None = None) -> dict[str, int]:
        batch_size = max(1, int(limit or self._config.batch_size))
        reclaimed = 0
        reclaim = getattr(self._repository, "reclaim_stuck_authority_assistant_tasks", None)
        if callable(reclaim):
            reclaimed = int(reclaim(timeout_seconds=self._config.processing_timeout_seconds) or 0)
        tasks = list(self._repository.claim_pending_authority_assistant_tasks(limit=batch_size))
        result = {
            "claimed": len(tasks),
            "done": 0,
            "retry": 0,
            "dead": 0,
            "skipped": 0,
            "reclaimed": reclaimed,
        }
        for task in tasks:
            task_id = int(task.get("id") or 0)
            if task_id <= 0:
                result["skipped"] += 1
                continue
            try:
                materialized = self._conversation_service.materialize_authority_assistant_task(task=task)
            except Exception as exc:
                outcome = self._retry_or_dead(task=task, error=str(exc))
                self._logger.warning("assistant inbox materialization failed: %s", exc, exc_info=True)
                result[outcome] += 1
                continue
            if not materialized.get("success"):
                outcome = self._retry_or_dead(
                    task=task,
                    error=str(materialized.get("error") or "assistant_materialize_failed"),
                )
                result[outcome] += 1
                continue
            self._repository.mark_authority_assistant_task_done(
                task_id=task_id,
                materialized_message_id=str(materialized.get("message_id") or ""),
                note="ok",
            )
            result["done"] += 1
        return result

    def _retry_or_dead(self, *, task: dict[str, Any], error: str) -> str:
        task_id = int(task.get("id") or 0)
        attempt_count = self._task_attempt_count(task)
        next_attempt = attempt_count + 1
        if next_attempt >= self._config.max_attempts:
            self._repository.mark_authority_assistant_task_dead(task_id=task_id, last_error=error)
            return "dead"
        backoff_seconds = self._compute_backoff_seconds(next_attempt)
        retry_at = datetime.now() + timedelta(seconds=backoff_seconds)
        self._repository.mark_authority_assistant_task_retry(
            task_id=task_id,
            last_error=error,
            next_retry_at=retry_at,
        )
        return "retry"

    def _task_attempt_count(self, task: dict[str, Any]) -> int:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        try:
            return max(0, int(metadata.get("attempt_count") or 0))
        except Exception:
            return 0

    def _compute_backoff_seconds(self, attempt_no: int) -> int:
        base = max(1, int(self._config.retry_base_seconds))
        maximum = max(base, int(self._config.retry_max_seconds))
        return min(maximum, base * (2 ** max(0, int(attempt_no) - 1)))
