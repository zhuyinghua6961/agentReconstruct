from __future__ import annotations

from tempfile import TemporaryDirectory

from app.integrations.redis import RedisService
from app.integrations.storage.local import LocalStorageBackend
from app.modules.conversation.json_store import ConversationJsonStore
from app.modules.conversation.service import ConversationService
from app.modules.conversation.assistant_inbox import AuthorityAssistantInboxWorker
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
