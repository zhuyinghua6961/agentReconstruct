from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
        finally:
            set_conversation_service(original_service)
            client.app.dependency_overrides.clear()
