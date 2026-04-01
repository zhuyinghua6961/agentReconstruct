from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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


def test_accept_authority_assistant_terminal_async_materializes_failed_turn():
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

        created = service.create_conversation(user_id=7, title="Assistant Terminal Inbox")
        conversation_id = int(created["data"]["conversation_id"])
        accepted = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-a1",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-terminal-a1:assistant",
            terminal_event={
                "terminal_status": "failed",
                "done_seen": False,
                "answer_text": "",
                "failure": {
                    "stage": "llm_stream",
                    "message": "timeout",
                    "code": "LLM_TIMEOUT",
                    "retriable": True,
                },
            },
        )

        assert accepted["success"] is True
        assert accepted["accepted"] is True
        assert accepted["status"] == "accepted"

        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        summary = worker.run_once(limit=10)

        assert summary["claimed"] == 1
        assert summary["done"] == 1
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assert detail["success"] is True
        assistant = detail["data"]["messages"][-1]
        assert assistant["status"] == "failed"
        assert assistant["done_seen"] is False
        assert assistant["metadata"]["terminal_status"] == "failed"
        assert assistant["metadata"]["failure_stage"] == "llm_stream"
        assert assistant["metadata"]["failure_code"] == "LLM_TIMEOUT"
        assert assistant["metadata"]["failure_message"] == "timeout"
        assert assistant["metadata"]["retriable"] is True


def test_accept_authority_assistant_terminal_async_materializes_canceled_turn_with_minimal_failure_message():
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

        created = service.create_conversation(user_id=7, title="Assistant Terminal Cancel")
        conversation_id = int(created["data"]["conversation_id"])
        accepted = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-cancel-a1",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-terminal-cancel-a1:assistant",
            terminal_event={
                "terminal_status": "canceled",
                "done_seen": False,
                "answer_text": "",
            },
        )

        assert accepted["success"] is True
        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        summary = worker.run_once(limit=10)

        assert summary["claimed"] == 1
        assert summary["done"] == 1
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assistant = detail["data"]["messages"][-1]
        assert assistant["status"] == "canceled"
        assert assistant["done_seen"] is False
        assert assistant["metadata"]["terminal_status"] == "canceled"
        assert assistant["metadata"]["failure_stage"] == "unknown"
        assert assistant["metadata"]["failure_message"] == "已取消"
        assert assistant["metadata"]["retriable"] is False


def test_terminal_then_legacy_accept_with_same_key_dedupes_across_endpoints():
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

        created = service.create_conversation(user_id=7, title="Assistant Terminal Dedupe")
        conversation_id = int(created["data"]["conversation_id"])
        key = f"{conversation_id}:trace-terminal-dedupe:assistant"
        terminal_result = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-dedupe",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "failed",
                "done_seen": False,
                "answer_text": "",
                "failure": {
                    "stage": "llm_stream",
                    "message": "timeout",
                    "code": "LLM_TIMEOUT",
                    "retriable": True,
                },
            },
        )
        legacy_result = service.accept_authority_assistant_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-dedupe",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            final_event={
                "done_seen": True,
                "answer_text": "should not enqueue",
                "steps": [],
                "references": [],
                "used_files": [],
                "timings": {},
            },
        )

        assert terminal_result["success"] is True
        assert legacy_result["success"] is True
        assert legacy_result["deduped"] is False

        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        summary = worker.run_once(limit=10)

        assert summary["claimed"] == 1
        assert summary["done"] == 1
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assistant = detail["data"]["messages"][-1]
        assert assistant["status"] == "done"
        assert assistant["content"] == "should not enqueue"


def test_legacy_then_terminal_accept_with_same_key_dedupes_across_endpoints():
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

        created = service.create_conversation(user_id=7, title="Assistant Legacy Dedupe")
        conversation_id = int(created["data"]["conversation_id"])
        key = f"{conversation_id}:trace-legacy-dedupe:assistant"
        legacy_result = service.accept_authority_assistant_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-legacy-dedupe",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            final_event={
                "done_seen": True,
                "answer_text": "final answer",
                "steps": [],
                "references": [],
                "used_files": [],
                "timings": {},
            },
        )
        terminal_result = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-legacy-dedupe",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "failed",
                "done_seen": False,
                "answer_text": "",
                "failure": {
                    "stage": "llm_stream",
                    "message": "timeout",
                    "code": "LLM_TIMEOUT",
                    "retriable": True,
                },
            },
        )

        assert legacy_result["success"] is True
        assert terminal_result["success"] is True
        assert terminal_result["deduped"] is True

        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        summary = worker.run_once(limit=10)

        assert summary["claimed"] == 1
        assert summary["done"] == 1


def test_cross_endpoint_accepts_share_one_placeholder_under_concurrent_calls():
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend, redis_service=redis_service)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )

        created = service.create_conversation(user_id=7, title="Assistant Concurrent Dedupe")
        conversation_id = int(created["data"]["conversation_id"])
        key = f"{conversation_id}:trace-concurrent-dedupe:assistant"

        def _accept_legacy():
            return service.accept_authority_assistant_async(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-concurrent-dedupe",
                source_service="fastQA",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
                idempotency_key=key,
                final_event={
                    "done_seen": True,
                    "answer_text": "final answer",
                    "steps": [],
                    "references": [],
                    "used_files": [],
                    "timings": {},
                },
            )

        def _accept_terminal():
            return service.accept_authority_assistant_terminal_async(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-concurrent-dedupe",
                source_service="fastQA",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
                idempotency_key=key,
                terminal_event={
                    "terminal_status": "failed",
                    "done_seen": False,
                    "answer_text": "",
                    "failure": {
                        "stage": "llm_stream",
                        "message": "timeout",
                        "code": "LLM_TIMEOUT",
                        "retriable": True,
                    },
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            legacy_future = executor.submit(_accept_legacy)
            terminal_future = executor.submit(_accept_terminal)
            legacy_result = legacy_future.result()
            terminal_result = terminal_future.result()

        assert legacy_result["success"] is True
        assert terminal_result["success"] is True
        assert len(repo.assistant_tasks) == 1


def test_failed_terminal_turn_can_upgrade_to_done_with_same_idempotency_key():
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

        created = service.create_conversation(user_id=7, title="Assistant Terminal Upgrade")
        conversation_id = int(created["data"]["conversation_id"])
        key = f"{conversation_id}:trace-terminal-upgrade:assistant"

        failed_accept = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-upgrade",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "failed",
                "done_seen": False,
                "answer_text": "partial",
                "failure": {
                    "stage": "llm_stream",
                    "message": "timeout",
                    "code": "LLM_TIMEOUT",
                    "retriable": True,
                },
            },
        )
        assert failed_accept["success"] is True

        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        first_run = worker.run_once(limit=10)
        assert first_run["done"] == 1

        done_accept = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-upgrade",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "done",
                "done_seen": True,
                "answer_text": "final answer",
                "steps": [],
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "pdf_links": [],
                "doi_locations": {},
                "used_files": [],
                "timings": {},
            },
        )
        assert done_accept["success"] is True

        second_run = worker.run_once(limit=10)
        assert second_run["done"] == 1

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        messages = detail["data"]["messages"]
        assert len(messages) == 1
        assistant = messages[0]
        assert assistant["status"] == "done"
        assert assistant["done_seen"] is True
        assert assistant["content"] == "final answer"


def test_failed_terminal_placeholder_can_upgrade_to_done_before_worker_runs():
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

        created = service.create_conversation(user_id=7, title="Assistant Placeholder Upgrade")
        conversation_id = int(created["data"]["conversation_id"])
        key = f"{conversation_id}:trace-terminal-preupgrade:assistant"

        first_accept = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-preupgrade",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "failed",
                "done_seen": False,
                "answer_text": "partial",
                "failure": {
                    "stage": "llm_stream",
                    "message": "timeout",
                    "code": "LLM_TIMEOUT",
                    "retriable": True,
                },
            },
        )
        second_accept = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-preupgrade",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "done",
                "done_seen": True,
                "answer_text": "final answer",
                "steps": [],
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "pdf_links": [],
                "doi_locations": {},
                "used_files": [],
                "timings": {},
            },
        )

        assert first_accept["success"] is True
        assert second_accept["success"] is True
        assert second_accept["deduped"] is False

        worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
        summary = worker.run_once(limit=10)
        assert summary["done"] == 1

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assistant = detail["data"]["messages"][-1]
        assert assistant["status"] == "done"
        assert assistant["content"] == "final answer"


def test_claimed_failed_placeholder_reloads_latest_done_payload_before_materialize():
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

        created = service.create_conversation(user_id=7, title="Assistant Claimed Upgrade")
        conversation_id = int(created["data"]["conversation_id"])
        key = f"{conversation_id}:trace-terminal-claimed-upgrade:assistant"

        failed_accept = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-claimed-upgrade",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "failed",
                "done_seen": False,
                "answer_text": "partial",
                "failure": {
                    "stage": "llm_stream",
                    "message": "timeout",
                    "code": "LLM_TIMEOUT",
                    "retriable": True,
                },
            },
        )
        assert failed_accept["success"] is True

        claimed = repo.claim_pending_authority_assistant_tasks(limit=10)
        assert len(claimed) == 1

        done_accept = service.accept_authority_assistant_terminal_async(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-terminal-claimed-upgrade",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=key,
            terminal_event={
                "terminal_status": "done",
                "done_seen": True,
                "answer_text": "final answer",
                "steps": [],
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "pdf_links": [],
                "doi_locations": {},
                "used_files": [],
                "timings": {},
            },
        )
        assert done_accept["success"] is True

        materialized = service.materialize_authority_assistant_task(task=claimed[0])
        assert materialized["success"] is True
        repo.mark_authority_assistant_task_done(task_id=claimed[0]["id"], materialized_message_id=materialized["message_id"])

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        assistant = detail["data"]["messages"][-1]
        assert assistant["status"] == "done"
        assert assistant["content"] == "final answer"


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
