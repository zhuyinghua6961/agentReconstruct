from __future__ import annotations

from contextlib import contextmanager
from tempfile import TemporaryDirectory

from app.integrations.redis import RedisService
from app.integrations.storage.local import LocalStorageBackend
from app.modules.conversation.json_store import ConversationJsonStore
from app.modules.conversation.service import ConversationService
from test_conversation_module import _FakeRedis, _MemoryConversationRepo, _OutboxRecorder


@contextmanager
def _task_runtime_harness():
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
        yield service


def _create_conversation_with_user_turn(service: ConversationService) -> tuple[int, str]:
    created = service.create_conversation(user_id=7, title="task runtime")
    conversation_id = int(created["data"]["conversation_id"])
    user_result = service.add_authority_user_message(
        user_id=7,
        conversation_id=conversation_id,
        trace_id="task-trace-user",
        source_service="fastQA",
        route="kb_qa",
        requested_mode="fast",
        actual_mode="fast",
        idempotency_key=f"{conversation_id}:task-trace-user:user",
        content="hello task runtime",
        context_hints={},
    )
    return conversation_id, str(user_result["message_id"])


def test_task_assistant_start_creates_placeholder_and_binds_active_task_id():
    with _task_runtime_harness() as service:
        conversation_id, user_message_id = _create_conversation_with_user_turn(service)

        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_001",
            trace_id="task-trace-001",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert started["success"] is True
    assert started["assistant_message_id"].startswith("m_")
    assert started["assistant_message_id"] != user_message_id
    assert detail["success"] is True
    assert len(detail["data"]["messages"]) == 2
    assistant = detail["data"]["messages"][1]
    assert assistant["message_id"] == started["assistant_message_id"]
    assert assistant["status"] == "queued"
    assert assistant["metadata"]["task_id"] == "task_001"
    assert assistant["metadata"]["task_status"] == "queued"
    assert document["meta"]["active_task_id"] == "task_001"


def test_create_authority_task_turn_atomically_persists_user_turn_and_placeholder():
    with _task_runtime_harness() as service:
        created = service.create_conversation(user_id=7, title="task atomic create")
        conversation_id = int(created["data"]["conversation_id"])

        created_turn = service.create_authority_task_turn(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_atomic_001",
            trace_id="task-atomic-001",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            content="atomic hello",
            context_hints={"selected_file_ids": [9], "last_turn_route_hint": "kb_qa"},
            status="queued",
            last_seq=0,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert created_turn["success"] is True
    assert created_turn["status"] == "queued"
    assert created_turn["user_message_id"].startswith("m_")
    assert created_turn["assistant_message_id"].startswith("m_")
    assert created_turn["user_message_id"] != created_turn["assistant_message_id"]
    assert len(detail["data"]["messages"]) == 2
    assert detail["data"]["messages"][0]["role"] == "user"
    assert detail["data"]["messages"][0]["content"] == "atomic hello"
    assert detail["data"]["messages"][1]["role"] == "assistant"
    assert detail["data"]["messages"][1]["status"] == "queued"
    assert detail["data"]["messages"][1]["metadata"]["task_id"] == "task_atomic_001"
    assert document["meta"]["active_task_id"] == "task_atomic_001"


def test_create_authority_task_turn_is_idempotent_for_same_task_id():
    with _task_runtime_harness() as service:
        created = service.create_conversation(user_id=7, title="task atomic dedupe")
        conversation_id = int(created["data"]["conversation_id"])

        first = service.create_authority_task_turn(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_atomic_002",
            trace_id="task-atomic-002",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            content="atomic dedupe",
            context_hints={"selected_file_ids": [], "last_turn_route_hint": "kb_qa"},
            status="queued",
            last_seq=0,
        )
        second = service.create_authority_task_turn(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_atomic_002",
            trace_id="task-atomic-002",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            content="atomic dedupe",
            context_hints={"selected_file_ids": [], "last_turn_route_hint": "kb_qa"},
            status="queued",
            last_seq=0,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

    assert first["success"] is True
    assert second["success"] is True
    assert second["deduped"] is True
    assert first["user_message_id"] == second["user_message_id"]
    assert first["assistant_message_id"] == second["assistant_message_id"]
    assert len(detail["data"]["messages"]) == 2


def test_task_assistant_start_is_idempotent_for_same_task_id():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)

        first = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_002",
            trace_id="task-trace-002",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        second = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_002",
            trace_id="task-trace-002",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

    assert first["assistant_message_id"] == second["assistant_message_id"]
    assert len(detail["data"]["messages"]) == 2


def test_task_assistant_progress_updates_bound_placeholder_without_creating_new_message():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_003",
            trace_id="task-trace-003",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )

        progressed = service.progress_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_003",
            status="running",
            content_delta="partial answer",
            steps=[{"title": "retrieve"}],
            last_seq=5,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

    assert progressed["success"] is True
    assert progressed["assistant_message_id"] == started["assistant_message_id"]
    assert len(detail["data"]["messages"]) == 2
    assistant = detail["data"]["messages"][1]
    assert assistant["message_id"] == started["assistant_message_id"]
    assert assistant["status"] == "running"
    assert assistant["content"] == "partial answer"
    assert assistant["metadata"]["last_seq"] == 5
    assert assistant["metadata"]["steps"] == [{"title": "retrieve"}]


def test_task_assistant_terminal_clears_active_task_and_finalizes_same_placeholder():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_004",
            trace_id="task-trace-004",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        service.progress_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_004",
            status="running",
            content_delta="partial answer",
            steps=[{"title": "retrieve"}],
            last_seq=6,
        )

        terminal = service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_004",
            terminal_status="expired",
            last_seq=7,
            timings={"stage1": 1000},
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert terminal["success"] is True
    assert terminal["assistant_message_id"] == started["assistant_message_id"]
    assert len(detail["data"]["messages"]) == 2
    assistant = detail["data"]["messages"][1]
    assert assistant["message_id"] == started["assistant_message_id"]
    assert assistant["status"] == "expired"
    assert assistant["metadata"]["terminal_status"] == "expired"
    assert assistant["metadata"]["failure_stage"] == "unknown"
    assert assistant["metadata"]["failure_message"] == "已过期"
    assert assistant["metadata"]["retriable"] is False
    assert assistant["metadata"]["last_seq"] == 7
    assert assistant["metadata"]["timings"] == {"stage1": 1000}
    assert document["meta"].get("active_task_id") in {None, ""}


def test_task_assistant_late_progress_does_not_resurrect_terminal_placeholder():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_005",
            trace_id="task-trace-005",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_005",
            terminal_status="expired",
            last_seq=7,
        )

        late_progress = service.progress_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_005",
            status="running",
            content_delta="should-be-ignored",
            steps=[{"title": "late"}],
            last_seq=8,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert late_progress["success"] is True
    assistant = detail["data"]["messages"][1]
    assert assistant["status"] == "expired"
    assert assistant["content"] == ""
    assert document["meta"].get("active_task_id") in {None, ""}


def test_task_assistant_conflicting_second_terminal_does_not_override_first_terminal():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_006",
            trace_id="task-trace-006",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        first_terminal = service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_006",
            terminal_status="expired",
            last_seq=7,
            timings={"stage1": 1000},
        )

        second_terminal = service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_006",
            terminal_status="failed",
            last_seq=8,
            failure={"message": "should-be-ignored"},
            timings={"stage2": 2000},
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

    assert first_terminal["success"] is True
    assert second_terminal["success"] is True
    assistant = detail["data"]["messages"][1]
    assert assistant["status"] == "expired"
    assert assistant["metadata"]["terminal_status"] == "expired"
    assert assistant["metadata"]["timings"] == {"stage1": 1000}


def test_task_assistant_repeated_start_after_terminal_does_not_reopen_placeholder():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_007",
            trace_id="task-trace-007",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_007",
            terminal_status="expired",
            last_seq=7,
        )

        repeated_start = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_007",
            trace_id="task-trace-007",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
            last_seq=8,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert repeated_start["success"] is True
    assert repeated_start["assistant_message_id"] == started["assistant_message_id"]
    assistant = detail["data"]["messages"][1]
    assert assistant["status"] == "expired"
    assert assistant["metadata"]["terminal_status"] == "expired"
    assert document["meta"].get("active_task_id") in {None, ""}


def test_task_assistant_repeated_start_does_not_roll_back_live_placeholder_state():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_008",
            trace_id="task-trace-008",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
            last_seq=0,
        )
        service.progress_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_008",
            status="running",
            content_delta="partial answer",
            steps=[{"title": "retrieve"}],
            last_seq=5,
        )

        repeated_start = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_008",
            trace_id="task-trace-008",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
            last_seq=0,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert repeated_start["success"] is True
    assert repeated_start["assistant_message_id"] == started["assistant_message_id"]
    assert repeated_start["status"] == "running"
    assistant = detail["data"]["messages"][1]
    assert assistant["status"] == "running"
    assert assistant["metadata"]["task_status"] == "running"
    assert assistant["metadata"]["last_seq"] == 5
    assert document["meta"]["active_task_id"] == "task_008"


def test_task_assistant_repeated_start_rebinds_active_task_id_without_regressing_live_state():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_008_rebind",
            trace_id="task-trace-008-rebind",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
            last_seq=0,
        )
        service.progress_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_008_rebind",
            status="running",
            content_delta="partial answer",
            steps=[{"title": "retrieve"}],
            last_seq=5,
        )
        with service._json_store.conversation_lock(user_id=7, conversation_id=conversation_id):
            document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)
            document["meta"]["active_task_id"] = ""
            service._json_store.write_document(user_id=7, conversation_id=conversation_id, document=document)

        repeated_start = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_008_rebind",
            trace_id="task-trace-008-rebind",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
            last_seq=0,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert repeated_start["success"] is True
    assert repeated_start["assistant_message_id"] == started["assistant_message_id"]
    assistant = detail["data"]["messages"][1]
    assert assistant["status"] == "running"
    assert assistant["metadata"]["task_status"] == "running"
    assert assistant["metadata"]["last_seq"] == 5
    assert document["meta"]["active_task_id"] == "task_008_rebind"


def test_task_assistant_terminal_persists_flat_failure_fields():
    with _task_runtime_harness() as service:
        conversation_id, _ = _create_conversation_with_user_turn(service)
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_failure_fields",
            trace_id="task-trace-failure-fields",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="running",
            last_seq=2,
        )

        terminal = service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_failure_fields",
            terminal_status="failed",
            last_seq=3,
            failure={
                "stage": "llm_stream",
                "code": "LLM_TIMEOUT",
                "message": "timeout",
                "retriable": True,
            },
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

    assert terminal["success"] is True
    assistant = detail["data"]["messages"][1]
    assert assistant["status"] == "failed"
    assert assistant["metadata"]["failure"]["stage"] == "llm_stream"
    assert assistant["metadata"]["failure_stage"] == "llm_stream"
    assert assistant["metadata"]["failure_code"] == "LLM_TIMEOUT"
    assert assistant["metadata"]["failure_message"] == "timeout"
    assert assistant["metadata"]["retriable"] is True


def test_task_create_rollback_removes_bound_user_turn_placeholder_and_active_task():
    with _task_runtime_harness() as service:
        created = service.create_conversation(user_id=7, title="task rollback")
        conversation_id = int(created["data"]["conversation_id"])
        user_result = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="task_rollback_001",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:task_rollback_001:user",
            content="rollback me",
            context_hints={},
        )
        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_rollback_001",
            trace_id="task_rollback_001",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )

        rolled_back = service.rollback_authority_task_creation(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_rollback_001",
            user_message_id=str(user_result["message_id"]),
            assistant_message_id=str(started["assistant_message_id"]),
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert rolled_back["success"] is True
    assert rolled_back["removed_count"] == 2
    assert detail["success"] is True
    assert detail["data"]["messages"] == []
    assert document["meta"].get("active_task_id") in {None, ""}


def test_task_create_rollback_is_idempotent_when_state_already_cleared():
    with _task_runtime_harness() as service:
        created = service.create_conversation(user_id=7, title="task rollback idempotent")
        conversation_id = int(created["data"]["conversation_id"])

        first = service.rollback_authority_task_creation(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_rollback_002",
        )
        second = service.rollback_authority_task_creation(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_rollback_002",
        )

    assert first["success"] is True
    assert first["removed_count"] == 0
    assert second["success"] is True
    assert second["removed_count"] == 0


def test_task_create_rollback_can_preserve_user_turn_while_clearing_placeholder_and_active_task():
    with _task_runtime_harness() as service:
        created = service.create_conversation(user_id=7, title="task rollback preserve user")
        conversation_id = int(created["data"]["conversation_id"])
        user_result = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="task_rollback_keep_user_001",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:task_rollback_keep_user_001:user",
            content="keep me",
            context_hints={},
        )
        started = service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_rollback_keep_user_001",
            trace_id="task_rollback_keep_user_001",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="running",
        )

        rolled_back = service.rollback_authority_task_creation(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_rollback_keep_user_001",
            user_message_id=str(user_result["message_id"]),
            assistant_message_id=str(started["assistant_message_id"]),
            preserve_user_message=True,
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert rolled_back["success"] is True
    assert rolled_back["removed_count"] == 1
    assert detail["success"] is True
    assert len(detail["data"]["messages"]) == 1
    assert detail["data"]["messages"][0]["role"] == "user"
    assert detail["data"]["messages"][0]["message_id"] == str(user_result["message_id"])
    assert document["meta"].get("active_task_id") in {None, ""}


def test_atomic_create_plus_task_lifecycle_keeps_single_user_and_assistant_turn():
    with _task_runtime_harness() as service:
        created = service.create_conversation(user_id=7, title="task atomic lifecycle")
        conversation_id = int(created["data"]["conversation_id"])

        created_turn = service.create_authority_task_turn(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_atomic_lifecycle_001",
            trace_id="task-atomic-lifecycle-001",
            source_service="patentQA",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            content="请总结这个专利",
            context_hints={"selected_file_ids": [], "last_turn_route_hint": "kb_qa"},
            status="queued",
            last_seq=0,
        )
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_atomic_lifecycle_001",
            trace_id="task-atomic-lifecycle-001",
            source_service="patentQA",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            status="queued",
            last_seq=0,
        )
        service.progress_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_atomic_lifecycle_001",
            status="running",
            content_delta="专利摘要输出中",
            steps=[{"step": "retrieve", "status": "success"}],
            last_seq=1,
        )
        service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_atomic_lifecycle_001",
            terminal_status="completed",
            answer_text="专利摘要输出中",
            steps=[{"step": "retrieve", "status": "success"}],
            last_seq=2,
            failure={},
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
        document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)

    assert created_turn["success"] is True
    assert len(detail["data"]["messages"]) == 2
    assert [message["role"] for message in detail["data"]["messages"]] == ["user", "assistant"]
    assistant = detail["data"]["messages"][1]
    assert assistant["message_id"] == created_turn["assistant_message_id"]
    assert assistant["status"] == "completed"
    assert assistant["metadata"]["task_id"] == "task_atomic_lifecycle_001"
    assert assistant["metadata"]["requested_mode"] == "patent"
    assert assistant["metadata"]["actual_mode"] == "patent"
    assert document["meta"].get("active_task_id") in {None, ""}
