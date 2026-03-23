from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


class AuthorityAssistantInboxWorker:
    def __init__(self, *, repository: Any, conversation_service: Any, logger_: logging.Logger | None = None) -> None:
        self._repository = repository
        self._conversation_service = conversation_service
        self._logger = logger_ or logger

    def run_once(self, *, limit: int = 100) -> dict[str, int]:
        tasks = list(self._repository.claim_pending_authority_assistant_tasks(limit=max(1, int(limit))))
        result = {
            "claimed": len(tasks),
            "done": 0,
            "retry": 0,
            "dead": 0,
            "skipped": 0,
        }
        for task in tasks:
            task_id = int(task.get("id") or 0)
            if task_id <= 0:
                result["skipped"] += 1
                continue
            try:
                materialized = self._conversation_service.materialize_authority_assistant_task(task=task)
            except Exception as exc:
                self._repository.mark_authority_assistant_task_failed(task_id=task_id, last_error=str(exc))
                self._logger.warning("assistant inbox materialization failed: %s", exc, exc_info=True)
                result["retry"] += 1
                continue
            if not materialized.get("success"):
                self._repository.mark_authority_assistant_task_failed(
                    task_id=task_id,
                    last_error=str(materialized.get("error") or "assistant_materialize_failed"),
                )
                result["retry"] += 1
                continue
            self._repository.mark_authority_assistant_task_done(
                task_id=task_id,
                materialized_message_id=str(materialized.get("message_id") or ""),
                note="ok",
            )
            result["done"] += 1
        return result
