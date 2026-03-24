from __future__ import annotations

from tempfile import TemporaryDirectory

from app.integrations.redis import RedisService
from app.integrations.storage.local import LocalStorageBackend
from app.modules.conversation.json_store import ConversationJsonStore
from app.modules.conversation.service import ConversationService
from app.modules.conversation.assistant_inbox import AuthorityAssistantInboxWorker, AuthorityAssistantInboxConfig
from test_conversation_module import _FakeRedis, _MemoryConversationRepo, _OutboxRecorder



def test_accept_authority_assistant_async_is_hidden_until_worker_materializes():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Assistant Inbox")
        conversation_id = int(created["data"]["conversation_id"])
        accepted = service.accept_authority_assistant_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-a1",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-a1:assistant",
            final_event={
                "done_seen": True,
                "answer_text": "final answer",
                "steps": [{"step": "stage1"}],
                "references": [{"doi": "10.1/a"}],
                "used_files": [{"file_id": 8}],
                "timings": {"latency_ms": 321},
            },
        )

        assert accepted["success"] is True
        assert accepted["accepted"] is True
        assert accepted["status"] == "accepted"

        before = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)
        assert before["success"] is True
        assert before["data"]["recent_turns"] == []

        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        summary = worker.run_once(limit=10)

        assert summary["claimed"] == 1
        assert summary["done"] == 1
        after = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)
        assert [item["role"] for item in after["data"]["recent_turns"]] == ["assistant"]
        assert after["data"]["recent_turns"][0]["content"] == "final answer"
        assert after["data"]["conversation_state"] == {
            "last_turn_route": "kb_qa",
            "last_focus_file_ids": [8],
            "last_assistant_trace_id": "trace-a1",
        }



def test_accept_authority_assistant_async_dedupes_and_materializes_once():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Assistant Inbox Dedupe")
        conversation_id = int(created["data"]["conversation_id"])
        payload = {
            "user_id": 7,
            "conversation_id": conversation_id,
            "trace_id": "trace-a2",
            "source_service": "fastQA",
            "route": "hybrid_qa",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "idempotency_key": f"{conversation_id}:trace-a2:assistant",
            "final_event": {
                "done_seen": True,
                "answer_text": "answer once",
                "steps": [],
                "references": [],
                "used_files": [{"file_id": 5}, {"file_id": 9}],
                "timings": {},
            },
        }

        first = service.accept_authority_assistant_async(**payload)
        second = service.accept_authority_assistant_async(**payload)

        assert first["success"] is True
        assert second["success"] is True
        assert second["deduped"] is True

        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        first_run = worker.run_once(limit=10)
        second_run = worker.run_once(limit=10)

        assert first_run["done"] == 1
        assert second_run["done"] == 0

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assert [item["content"] for item in detail["data"]["messages"]] == ["answer once"]
        assert detail["data"]["message_count"] == 1


class _RetryRepo:
    def __init__(self, *, tasks=None, reclaimed=0):
        self.tasks = list(tasks or [])
        self.reclaimed = int(reclaimed)
        self.retry_calls: list[dict] = []
        self.dead_calls: list[dict] = []
        self.failed_calls: list[dict] = []
        self.done_calls: list[dict] = []

    def reclaim_stuck_authority_assistant_tasks(self, *, timeout_seconds: int) -> int:
        self.last_reclaim_timeout = int(timeout_seconds)
        return self.reclaimed

    def claim_pending_authority_assistant_tasks(self, *, limit: int):
        return list(self.tasks[:limit])

    def mark_authority_assistant_task_retry(self, *, task_id: int, last_error: str, next_retry_at):
        self.retry_calls.append({
            "task_id": task_id,
            "last_error": last_error,
            "next_retry_at": next_retry_at,
        })
        return 1

    def mark_authority_assistant_task_dead(self, *, task_id: int, last_error: str):
        self.dead_calls.append({
            "task_id": task_id,
            "last_error": last_error,
        })
        return 1

    def mark_authority_assistant_task_failed(self, *, task_id: int, last_error: str):
        self.failed_calls.append({
            "task_id": task_id,
            "last_error": last_error,
        })
        return 1

    def mark_authority_assistant_task_done(self, *, task_id: int, materialized_message_id: str, note: str = "ok"):
        self.done_calls.append({
            "task_id": task_id,
            "materialized_message_id": materialized_message_id,
            "note": note,
        })
        return 1


def test_authority_assistant_inbox_worker_retries_failed_materialization_before_dead():
    repo = _RetryRepo(
        tasks=[
            {
                "id": 11,
                "conversation_id": 7,
                "user_id": 5,
                "metadata": {
                    "assistant_async_state": "pending",
                    "attempt_count": 0,
                },
            }
        ]
    )

    class _FailingService:
        def materialize_authority_assistant_task(self, *, task):
            raise RuntimeError("boom")

    worker = AuthorityAssistantInboxWorker(
        repository=repo,
        conversation_service=_FailingService(),
        config=AuthorityAssistantInboxConfig(max_attempts=3, retry_base_seconds=1, retry_max_seconds=4, processing_timeout_seconds=30),
    )

    summary = worker.run_once(limit=10)

    assert summary["reclaimed"] == 0
    assert summary["claimed"] == 1
    assert summary["retry"] == 1
    assert summary["dead"] == 0
    assert repo.retry_calls[0]["task_id"] == 11
    assert repo.retry_calls[0]["last_error"] == "boom"


def test_authority_assistant_inbox_worker_marks_dead_after_max_attempts():
    repo = _RetryRepo(
        tasks=[
            {
                "id": 12,
                "conversation_id": 7,
                "user_id": 5,
                "metadata": {
                    "assistant_async_state": "failed",
                    "attempt_count": 2,
                },
            }
        ]
    )

    class _FailingService:
        def materialize_authority_assistant_task(self, *, task):
            return {"success": False, "error": "still-bad"}

    worker = AuthorityAssistantInboxWorker(
        repository=repo,
        conversation_service=_FailingService(),
        config=AuthorityAssistantInboxConfig(max_attempts=3, retry_base_seconds=1, retry_max_seconds=4, processing_timeout_seconds=30),
    )

    summary = worker.run_once(limit=10)

    assert summary["claimed"] == 1
    assert summary["retry"] == 0
    assert summary["dead"] == 1
    assert repo.dead_calls == [{"task_id": 12, "last_error": "still-bad"}]


def test_authority_assistant_inbox_worker_reclaims_stuck_processing_before_claiming():
    repo = _RetryRepo(tasks=[], reclaimed=2)

    class _Service:
        def materialize_authority_assistant_task(self, *, task):
            return {"success": True, "message_id": "m-1"}

    worker = AuthorityAssistantInboxWorker(
        repository=repo,
        conversation_service=_Service(),
        config=AuthorityAssistantInboxConfig(max_attempts=3, retry_base_seconds=1, retry_max_seconds=4, processing_timeout_seconds=45),
    )

    summary = worker.run_once(limit=10)

    assert summary == {
        "claimed": 0,
        "done": 0,
        "retry": 0,
        "dead": 0,
        "skipped": 0,
        "reclaimed": 2,
    }
    assert repo.last_reclaim_timeout == 45
