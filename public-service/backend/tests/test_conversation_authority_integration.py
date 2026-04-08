from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
HIGH_THINKING_ROOT = REPO_ROOT / "highThinkingQA"
if str(HIGH_THINKING_ROOT) not in sys.path:
    sys.path.insert(0, str(HIGH_THINKING_ROOT))

from app.core.deps import AuthContext
from app.integrations.redis import RedisService
from app.integrations.storage.local import LocalStorageBackend
from app.main import app
from app.modules.auth.deps import require_auth_context
from app.modules.conversation.assistant_inbox import AuthorityAssistantInboxWorker
from app.modules.conversation.internal_api import _INTERNAL_TOKEN_ENV
from app.modules.conversation.json_store import ConversationJsonStore
from app.modules.conversation.service import ConversationService, conversation_service, set_conversation_service
from fastQA.app.services.conversation_authority_client import ConversationAuthorityClient
from highThinkingQA.server.services.conversation_authority_client import ConversationAuthorityClient as HighThinkingConversationAuthorityClient
from test_conversation_module import _FakeRedis, _MemoryConversationRepo, _OutboxRecorder


def _transport_via_test_client(client: TestClient) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        response = client.request(
            method=request.method,
            url=request.url.path + (f"?{request.url.query.decode('utf-8')}" if request.url.query else ""),
            headers=dict(request.headers),
            content=request.content,
        )
        body = response.content
        headers = dict(response.headers)
        return httpx.Response(response.status_code, content=body, headers=headers)

    return httpx.MockTransport(handler)


def test_fastqa_authority_client_closed_loop_materializes_assistant_turn(monkeypatch):
    monkeypatch.setenv(_INTERNAL_TOKEN_ENV, "authority-test-token")

    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir, TestClient(app) as client:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )
        original_service = conversation_service
        set_conversation_service(service)
        client.app.state.runtime.conversation_service = service
        client.app.state.runtime.conversation_repository = repo
        client.app.state.runtime.redis_service = redis_service
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="user7")

        try:
            created = service.create_conversation(user_id=7, title="authority integration")
            conversation_id = int(created["data"]["conversation_id"])

            authority_client = ConversationAuthorityClient(
                base_url="http://public-service",
                service_token="authority-test-token",
                transport=_transport_via_test_client(client),
            )

            user_written = authority_client.write_user_turn(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-user",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
                content="why?",
                selected_file_ids=[],
                last_turn_route_hint=None,
            )
            assert user_written["conversation_id"] == conversation_id

            snapshot_after_user = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-user",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
            )
            assert [item["role"] for item in snapshot_after_user["recent_turns"]] == ["user"]

            accepted = authority_client.accept_assistant_turn_async(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
                answer_text="because",
                steps=[{"step": "stage1"}],
                references=[{"doi": "10.1/a"}],
                reference_objects=[{"doi": "10.1/a", "section_name": "Results", "chunk_index": 3}],
                reference_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
                pdf_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
                doi_locations={"10.1/a": [{"section": "Results", "chunk_index": 3}]},
                used_files=[{"file_id": 9}],
                timings={"latency_ms": 123},
            )
            assert accepted["accepted"] is True

            snapshot_before_worker = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
            )
            assert [item["role"] for item in snapshot_before_worker["recent_turns"]] == ["user"]

            worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
            summary = worker.run_once(limit=10)
            assert summary["done"] == 1

            snapshot_after_worker = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
            )
            assert [item["role"] for item in snapshot_after_worker["recent_turns"]] == ["user", "assistant"]
            assert snapshot_after_worker["conversation_state"] == {
                "last_turn_route": "kb_qa",
                "last_focus_file_ids": [9],
                "last_assistant_trace_id": "trace-it-assistant",
            }
            detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
            assert detail["success"] is True
            assistant_message = detail["data"]["messages"][-1]
            assert assistant_message["metadata"]["reference_objects"] == [{"doi": "10.1/a", "section_name": "Results", "chunk_index": 3}]
            assert assistant_message["metadata"]["reference_links"] == [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}]
            assert assistant_message["metadata"]["pdf_links"] == [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}]
            assert assistant_message["metadata"]["doi_locations"] == {"10.1/a": [{"section": "Results", "chunk_index": 3}]}
        finally:
            set_conversation_service(original_service)
            client.app.dependency_overrides.clear()


def test_fastqa_authority_client_allows_rerouted_thinking_request_mode(monkeypatch):
    monkeypatch.setenv(_INTERNAL_TOKEN_ENV, "authority-test-token")

    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir, TestClient(app) as client:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )
        original_service = conversation_service
        set_conversation_service(service)
        client.app.state.runtime.conversation_service = service
        client.app.state.runtime.conversation_repository = repo
        client.app.state.runtime.redis_service = redis_service
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="user7")

        try:
            created = service.create_conversation(user_id=7, title="authority integration rerouted thinking")
            conversation_id = int(created["data"]["conversation_id"])

            authority_client = ConversationAuthorityClient(
                base_url="http://public-service",
                service_token="authority-test-token",
                transport=_transport_via_test_client(client),
            )

            user_written = authority_client.write_user_turn(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-user-rerouted",
                route="pdf_qa",
                requested_mode="thinking",
                actual_mode="fast",
                content="summarize this paper",
                selected_file_ids=[9],
                last_turn_route_hint="pdf_qa",
            )
            assert user_written["conversation_id"] == conversation_id

            snapshot_after_user = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-user-rerouted",
                route="pdf_qa",
                requested_mode="thinking",
                actual_mode="fast",
            )
            assert [item["role"] for item in snapshot_after_user["recent_turns"]] == ["user"]

            accepted = authority_client.accept_assistant_turn_async(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant-rerouted",
                route="pdf_qa",
                requested_mode="thinking",
                actual_mode="fast",
                answer_text="paper summary",
                steps=[{"step": "pdf"}],
                references=[],
                reference_objects=[],
                reference_links=[],
                pdf_links=[],
                doi_locations={},
                used_files=[{"file_id": 9}],
                timings={"latency_ms": 123},
            )
            assert accepted["accepted"] is True

            worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
            summary = worker.run_once(limit=10)
            assert summary["done"] == 1

            snapshot_after_worker = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant-rerouted",
                route="pdf_qa",
                requested_mode="thinking",
                actual_mode="fast",
            )
            assert [item["role"] for item in snapshot_after_worker["recent_turns"]] == ["user", "assistant"]
            assert snapshot_after_worker["conversation_state"] == {
                "last_turn_route": "pdf_qa",
                "last_focus_file_ids": [9],
                "last_assistant_trace_id": "trace-it-assistant-rerouted",
            }
        finally:
            set_conversation_service(original_service)
            client.app.dependency_overrides.clear()


def test_highthinking_authority_client_closed_loop_materializes_assistant_turn(monkeypatch):
    monkeypatch.setenv(_INTERNAL_TOKEN_ENV, "authority-test-token")

    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir, TestClient(app) as client:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )
        original_service = conversation_service
        set_conversation_service(service)
        client.app.state.runtime.conversation_service = service
        client.app.state.runtime.conversation_repository = repo
        client.app.state.runtime.redis_service = redis_service
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="user7")

        try:
            created = service.create_conversation(user_id=7, title="authority integration thinking")
            conversation_id = int(created["data"]["conversation_id"])

            authority_client = HighThinkingConversationAuthorityClient(
                base_url="http://public-service",
                service_token="authority-test-token",
                transport=_transport_via_test_client(client),
            )

            user_written = authority_client.write_user_turn(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-user-thinking",
                route="thinking_qa",
                requested_mode="thinking",
                actual_mode="thinking",
                content="why?",
            )
            assert user_written["conversation_id"] == conversation_id

            snapshot_after_user = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-user-thinking",
                route="thinking_qa",
                requested_mode="thinking",
                actual_mode="thinking",
            )
            assert [item["role"] for item in snapshot_after_user["recent_turns"]] == ["user"]

            accepted = authority_client.accept_assistant_turn_async(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant-thinking",
                route="thinking_qa",
                requested_mode="thinking",
                actual_mode="thinking",
                answer_text="because",
                steps=[{"step": "stage1"}],
                references=[{"doi": "10.1/a"}],
                reference_objects=[{"doi": "10.1/a", "section_name": "Discussion", "chunk_index": 2}],
                reference_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
                pdf_links=[{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}],
                doi_locations={"10.1/a": [{"section": "Discussion", "chunk_index": 2}]},
                used_files=[{"file_id": 9}],
                timings={"latency_ms": 123},
            )
            assert accepted["accepted"] is True

            snapshot_before_worker = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant-thinking",
                route="thinking_qa",
                requested_mode="thinking",
                actual_mode="thinking",
            )
            assert [item["role"] for item in snapshot_before_worker["recent_turns"]] == ["user"]

            worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
            summary = worker.run_once(limit=10)
            assert summary["done"] == 1

            snapshot_after_worker = authority_client.read_context_snapshot(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-assistant-thinking",
                route="thinking_qa",
                requested_mode="thinking",
                actual_mode="thinking",
            )
            assert [item["role"] for item in snapshot_after_worker["recent_turns"]] == ["user", "assistant"]
            assert snapshot_after_worker["conversation_state"] == {
                "last_turn_route": "thinking_qa",
                "last_focus_file_ids": [9],
                "last_assistant_trace_id": "trace-it-assistant-thinking",
            }
            detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
            assert detail["success"] is True
            assistant_message = detail["data"]["messages"][-1]
            assert assistant_message["metadata"]["reference_objects"] == [{"doi": "10.1/a", "section_name": "Discussion", "chunk_index": 2}]
            assert assistant_message["metadata"]["reference_links"] == [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}]
            assert assistant_message["metadata"]["pdf_links"] == [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1%2Fa"}]
            assert assistant_message["metadata"]["doi_locations"] == {"10.1/a": [{"section": "Discussion", "chunk_index": 2}]}
        finally:
            set_conversation_service(original_service)
            client.app.dependency_overrides.clear()


def test_gateway_owned_patent_task_runtime_keeps_single_user_and_assistant_turn(monkeypatch):
    monkeypatch.setenv(_INTERNAL_TOKEN_ENV, "authority-test-token")

    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir, TestClient(app) as client:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )
        original_service = conversation_service
        set_conversation_service(service)
        client.app.state.runtime.conversation_service = service
        client.app.state.runtime.conversation_repository = repo
        client.app.state.runtime.redis_service = redis_service
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="user7")

        try:
            created = service.create_conversation(user_id=7, title="gateway owned patent task runtime")
            conversation_id = int(created["data"]["conversation_id"])

            user_response = client.post(
                f"/internal/conversations/{conversation_id}/messages/user",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "trace_id": "trace-it-user-patent-task",
                    "source_service": "patentQA",
                    "route": "kb_qa",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "idempotency_key": f"{conversation_id}:trace-it-user-patent-task:user",
                    "message": {
                        "role": "user",
                        "content": "给我总结一下这个专利",
                    },
                    "context_hints": {},
                },
                headers={
                    "X-Internal-Service-Name": "gateway",
                    "X-Internal-Service-Token": "authority-test-token",
                },
            )
            assert user_response.status_code == 201
            assert user_response.json()["success"] is True

            start_response = client.post(
                f"/internal/conversations/{conversation_id}/tasks/task_patent_001/assistant-start",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "trace_id": "task-patent-trace",
                    "source_service": "patentQA",
                    "route": "kb_qa",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "task_id": "task_patent_001",
                    "status": "queued",
                    "last_seq": 0,
                },
                headers={
                    "X-Internal-Service-Name": "gateway",
                    "X-Internal-Service-Token": "authority-test-token",
                },
            )
            progress_response = client.post(
                f"/internal/conversations/{conversation_id}/tasks/task_patent_001/assistant-progress",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "task_id": "task_patent_001",
                    "status": "running",
                    "content_delta": "专利内容摘要",
                    "steps": [{"step": "retrieve", "status": "success"}],
                    "last_seq": 1,
                },
                headers={
                    "X-Internal-Service-Name": "gateway",
                    "X-Internal-Service-Token": "authority-test-token",
                },
            )
            terminal_response = client.post(
                f"/internal/conversations/{conversation_id}/tasks/task_patent_001/assistant-terminal",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "task_id": "task_patent_001",
                    "terminal_status": "completed",
                    "last_seq": 2,
                    "answer_text": "专利内容摘要",
                    "steps": [{"step": "retrieve", "status": "success"}],
                    "failure": {},
                },
                headers={
                    "X-Internal-Service-Name": "gateway",
                    "X-Internal-Service-Token": "authority-test-token",
                },
            )

            assert start_response.status_code == 200
            assert progress_response.status_code == 200
            assert terminal_response.status_code == 200

            snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)
            assert snapshot["success"] is True
            assert [turn["role"] for turn in snapshot["data"]["recent_turns"]] == ["user", "assistant"]
            assert snapshot["data"]["recent_turns"][-1]["status"] == "done"
            assert snapshot["data"]["recent_turns"][-1]["terminal_status"] == "done"

            detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
            assert detail["success"] is True
            assert [message["role"] for message in detail["data"]["messages"]] == ["user", "assistant"]
            assert len(detail["data"]["messages"]) == 2
            assistant_message = detail["data"]["messages"][-1]
            assert assistant_message["status"] == "completed"
            assert assistant_message["content"] == "专利内容摘要"
            assert assistant_message["metadata"]["task_id"] == "task_patent_001"
            assert assistant_message["metadata"]["terminal_status"] == "completed"
            assert assistant_message["metadata"]["requested_mode"] == "patent"
            assert assistant_message["metadata"]["actual_mode"] == "patent"
        finally:
            set_conversation_service(original_service)
            client.app.dependency_overrides.clear()


def test_internal_terminal_authority_route_materializes_failed_turn(monkeypatch):
    monkeypatch.setenv(_INTERNAL_TOKEN_ENV, "authority-test-token")

    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    with TemporaryDirectory() as tempdir, TestClient(app) as client:
        storage_backend = LocalStorageBackend(root_dir=tempdir)
        json_store = ConversationJsonStore(project_root=tempdir, storage_backend=storage_backend)
        service = ConversationService(
            repo=repo,
            json_store=json_store,
            outbox_repo=outbox,
            workspace_root=tempdir,
            redis_service=redis_service,
        )
        original_service = conversation_service
        set_conversation_service(service)
        client.app.state.runtime.conversation_service = service
        client.app.state.runtime.conversation_repository = repo
        client.app.state.runtime.redis_service = redis_service
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(user_id=7, role="user", username="user7")

        try:
            created = service.create_conversation(user_id=7, title="authority terminal integration")
            conversation_id = int(created["data"]["conversation_id"])

            user_written = service.add_authority_user_message(
                user_id=7,
                conversation_id=conversation_id,
                trace_id="trace-it-user-terminal",
                source_service="fastQA",
                route="kb_qa",
                requested_mode="fast",
                actual_mode="fast",
                idempotency_key=f"{conversation_id}:trace-it-user-terminal:user",
                content="why failed?",
                context_hints={},
            )
            assert user_written["success"] is True

            response = client.post(
                f"/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "trace_id": "trace-it-assistant-terminal",
                    "source_service": "fastQA",
                    "route": "kb_qa",
                    "requested_mode": "fast",
                    "actual_mode": "fast",
                    "idempotency_key": f"{conversation_id}:trace-it-assistant-terminal:assistant",
                    "terminal_event": {
                        "terminal_status": "failed",
                        "done_seen": False,
                        "answer_text": "partial answer",
                        "failure": {
                            "stage": "llm_stream",
                            "message": "timeout",
                            "code": "LLM_TIMEOUT",
                            "retriable": True,
                        },
                    },
                },
                headers={
                    "X-Internal-Service-Name": "fastQA",
                    "X-Internal-Service-Token": "authority-test-token",
                },
            )
            assert response.status_code == 202

            worker = AuthorityAssistantInboxWorker(repository=repo, conversation_service=service)
            summary = worker.run_once(limit=10)
            assert summary["done"] == 1

            snapshot = service.get_conversation_context_snapshot(user_id=7, conversation_id=conversation_id)
            assert snapshot["success"] is True
            assert snapshot["data"]["recent_turns"][-1]["status"] == "failed"
            assert snapshot["data"]["recent_turns"][-1]["terminal_status"] == "failed"

            detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
            assert detail["success"] is True
            assistant_message = detail["data"]["messages"][-1]
            assert assistant_message["status"] == "failed"
            assert assistant_message["metadata"]["terminal_status"] == "failed"
            assert assistant_message["metadata"]["failure_message"] == "timeout"
        finally:
            set_conversation_service(original_service)
            client.app.dependency_overrides.clear()
