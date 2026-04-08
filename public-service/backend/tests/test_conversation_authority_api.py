from __future__ import annotations

from contextlib import contextmanager
import logging
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.main import app
from app.integrations.redis import RedisService
from app.integrations.storage.local import LocalStorageBackend
from app.modules.auth.deps import require_auth_context
from app.modules.conversation.internal_api import _require_gateway_internal_caller, require_internal_authority
from app.modules.conversation.json_store import ConversationJsonStore
from app.modules.conversation.service import ConversationService, conversation_service, set_conversation_service
from test_conversation_module import _FakeRedis, _MemoryConversationRepo, _OutboxRecorder


INTERNAL_TOKEN_ENV = "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN"
INTERNAL_TOKEN = "authority-test-token"


def _route_for(path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _internal_headers(service_name: str = "fastQA") -> dict[str, str]:
    return {
        "X-Internal-Service-Name": service_name,
        "X-Internal-Service-Token": INTERNAL_TOKEN,
    }


def _snapshot_query(**overrides) -> dict[str, str | int]:
    query: dict[str, str | int] = {
        "user_id": 7,
        "trace_id": "trc_fast_001",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
    }
    query.update(overrides)
    return query


def _user_write_body(**overrides):
    payload = {
        "conversation_id": 12,
        "user_id": 7,
        "trace_id": "trc_fast_001",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "idempotency_key": "12:trc_fast_001:user",
        "message": {
            "role": "user",
            "content": "hello authority",
        },
        "context_hints": {
            "selected_file_ids": [1, 2],
            "last_turn_route_hint": "kb_qa",
        },
    }
    payload.update(overrides)
    return payload


def _assistant_body(**overrides):
    payload = {
        "conversation_id": 12,
        "user_id": 7,
        "trace_id": "trc_fast_001",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "idempotency_key": "12:trc_fast_001:assistant",
        "final_event": {
            "done_seen": True,
            "answer_text": "final answer",
            "steps": [],
            "references": [],
            "used_files": [],
            "timings": {"latency_ms": 321},
        },
    }
    payload.update(overrides)
    return payload


def _assistant_terminal_body(**overrides):
    payload = {
        "conversation_id": 12,
        "user_id": 7,
        "trace_id": "trc_fast_001",
        "source_service": "fastQA",
        "route": "kb_qa",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "idempotency_key": "12:trc_fast_001:assistant",
        "terminal_event": {
            "terminal_status": "failed",
            "done_seen": False,
            "answer_text": "",
            "steps": [],
            "references": [],
            "reference_objects": [],
            "reference_links": [],
            "pdf_links": [],
            "doi_locations": {},
            "used_files": [],
            "timings": {"latency_ms": 321},
            "failure": {
                "stage": "llm_stream",
                "message": "timeout",
                "code": "LLM_TIMEOUT",
                "retriable": True,
            },
        },
    }
    payload.update(overrides)
    return payload


@contextmanager
def _authority_harness(client: TestClient):
    repo = _MemoryConversationRepo()
    outbox = _OutboxRecorder()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    original_service = conversation_service
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
        set_conversation_service(service)
        client.app.state.runtime.conversation_service = service
        client.app.state.runtime.conversation_repository = repo
        client.app.state.runtime.redis_service = redis_service
        try:
            yield service
        finally:
            set_conversation_service(original_service)


def test_internal_authority_routes_registered_and_isolated_from_browser_auth():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/internal/conversations/{conversation_id}/messages/user" in paths
    assert "/internal/conversations/{conversation_id}/context-snapshot" in paths
    assert "/internal/conversations/{conversation_id}/messages/assistant-async" in paths
    assert "/internal/conversations/{conversation_id}/messages/assistant-terminal-async" in paths
    assert "/internal/conversations/{conversation_id}/tasks/{task_id}/create-turn" in paths
    assert "/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-start" in paths
    assert "/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-progress" in paths
    assert "/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-terminal" in paths

    user_write_route = _route_for("/internal/conversations/{conversation_id}/messages/user", "POST")
    snapshot_route = _route_for("/internal/conversations/{conversation_id}/context-snapshot", "GET")
    assistant_route = _route_for("/internal/conversations/{conversation_id}/messages/assistant-async", "POST")
    assistant_terminal_route = _route_for("/internal/conversations/{conversation_id}/messages/assistant-terminal-async", "POST")
    task_create_turn_route = _route_for("/internal/conversations/{conversation_id}/tasks/{task_id}/create-turn", "POST")
    task_start_route = _route_for("/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-start", "POST")
    task_progress_route = _route_for("/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-progress", "POST")
    task_terminal_route = _route_for("/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-terminal", "POST")

    assert require_auth_context not in {dep.call for dep in user_write_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in snapshot_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in assistant_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in assistant_terminal_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in task_create_turn_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in task_start_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in task_progress_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in task_terminal_route.dependant.dependencies}

    assert require_internal_authority in {dep.call for dep in user_write_route.dependant.dependencies}
    assert require_internal_authority in {dep.call for dep in snapshot_route.dependant.dependencies}
    assert require_internal_authority in {dep.call for dep in assistant_route.dependant.dependencies}
    assert require_internal_authority in {dep.call for dep in assistant_terminal_route.dependant.dependencies}
    assert _require_gateway_internal_caller in {dep.call for dep in task_create_turn_route.dependant.dependencies}
    assert _require_gateway_internal_caller in {dep.call for dep in task_start_route.dependant.dependencies}
    assert _require_gateway_internal_caller in {dep.call for dep in task_progress_route.dependant.dependencies}
    assert _require_gateway_internal_caller in {dep.call for dep in task_terminal_route.dependant.dependencies}


def test_internal_context_snapshot_requires_trusted_headers(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.get(
            "/internal/conversations/12/context-snapshot",
            params=_snapshot_query(),
        )

    assert response.status_code == 401
    assert response.json()["code"] == "INTERNAL_AUTH_MISSING"


def test_internal_task_progress_rejects_wrong_source_service(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="task progress wrong caller")
        conversation_id = int(created["data"]["conversation_id"])
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_progress_001",
            trace_id="task-progress-trace",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_progress_001/assistant-progress",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "task_id": "task_progress_001",
                "status": "running",
                "content_delta": "blocked",
                "steps": [],
                "last_seq": 1,
            },
            headers=_internal_headers("highThinkingQA"),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "INTERNAL_SOURCE_SERVICE_FORBIDDEN"


def test_internal_task_progress_logs_task_id(monkeypatch, caplog):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="task progress logs")
        conversation_id = int(created["data"]["conversation_id"])
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_progress_log_001",
            trace_id="task-progress-log-trace",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        with caplog.at_level(logging.INFO, logger="app.modules.conversation.internal_api"):
            response = client.post(
                f"/internal/conversations/{conversation_id}/tasks/task_progress_log_001/assistant-progress",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "task_id": "task_progress_log_001",
                    "status": "running",
                    "content_delta": "delta",
                    "steps": [],
                    "last_seq": 1,
                },
                headers=_internal_headers("gateway"),
            )

    assert response.status_code == 200
    assert any("task_id=task_progress_log_001" in record.getMessage() for record in caplog.records)


def test_internal_task_progress_skips_info_log_for_midstream_running_updates(monkeypatch, caplog):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="task progress midstream")
        conversation_id = int(created["data"]["conversation_id"])
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_progress_log_050",
            trace_id="task-progress-log-trace-050",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        with caplog.at_level(logging.INFO, logger="app.modules.conversation.internal_api"):
            response = client.post(
                f"/internal/conversations/{conversation_id}/tasks/task_progress_log_050/assistant-progress",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "task_id": "task_progress_log_050",
                    "status": "running",
                    "content_delta": "delta",
                    "steps": [],
                    "last_seq": 37,
                },
                headers=_internal_headers("gateway"),
            )

    assert response.status_code == 200
    assert not any("authority task progress task_id=task_progress_log_050" in record.getMessage() for record in caplog.records)


def test_internal_task_terminal_logs_task_id(monkeypatch, caplog):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="task terminal logs")
        conversation_id = int(created["data"]["conversation_id"])
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_terminal_log_001",
            trace_id="task-terminal-log-trace",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        with caplog.at_level(logging.INFO, logger="app.modules.conversation.internal_api"):
            response = client.post(
                f"/internal/conversations/{conversation_id}/tasks/task_terminal_log_001/assistant-terminal",
                json={
                    "conversation_id": conversation_id,
                    "user_id": 7,
                    "task_id": "task_terminal_log_001",
                    "terminal_status": "completed",
                    "last_seq": 2,
                    "answer_text": "done",
                    "steps": [],
                    "failure": {},
                },
                headers=_internal_headers("gateway"),
            )

    assert response.status_code == 200
    assert any("task_id=task_terminal_log_001" in record.getMessage() for record in caplog.records)


def test_internal_task_terminal_rejects_wrong_source_service(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="task terminal wrong caller")
        conversation_id = int(created["data"]["conversation_id"])
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_terminal_001",
            trace_id="task-terminal-trace",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="queued",
        )
        response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_terminal_001/assistant-terminal",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "task_id": "task_terminal_001",
                "terminal_status": "failed",
                "last_seq": 1,
                "answer_text": "",
                "steps": [],
                "failure": {"message": "blocked"},
            },
            headers=_internal_headers("highThinkingQA"),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "INTERNAL_SOURCE_SERVICE_FORBIDDEN"


def test_internal_task_routes_allow_gateway_caller_for_gateway_owned_task_runtime(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="gateway owned task runtime")
        conversation_id = int(created["data"]["conversation_id"])

        start_response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_gateway_001/assistant-start",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "trace_id": "task-gateway-trace",
                "source_service": "fastQA",
                "route": "kb_qa",
                "requested_mode": "fast",
                "actual_mode": "fast",
                "task_id": "task_gateway_001",
                "status": "queued",
                "last_seq": 0,
            },
            headers=_internal_headers("gateway"),
        )

        progress_response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_gateway_001/assistant-progress",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "task_id": "task_gateway_001",
                "status": "running",
                "content_delta": "hello",
                "steps": [],
                "last_seq": 1,
            },
            headers=_internal_headers("gateway"),
        )

        terminal_response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_gateway_001/assistant-terminal",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "task_id": "task_gateway_001",
                "terminal_status": "completed",
                "last_seq": 2,
                "answer_text": "hello",
                "steps": [],
                "failure": {},
            },
            headers=_internal_headers("gateway"),
        )

    assert start_response.status_code == 200
    assert progress_response.status_code == 200
    assert terminal_response.status_code == 200


def test_internal_user_write_allows_gateway_caller_for_gateway_owned_task_runtime(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="gateway owned user write")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/user",
            json=_user_write_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:user",
            ),
            headers=_internal_headers("gateway"),
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["conversation_id"] == conversation_id


def test_internal_gateway_task_runtime_normalizes_patent_citations_before_user_visible_storage(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="gateway owned patent citation normalization")
        conversation_id = int(created["data"]["conversation_id"])

        start_response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_patent_norm_001/assistant-start",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "trace_id": "task-patent-norm-trace",
                "source_service": "patentQA",
                "route": "kb_qa",
                "requested_mode": "patent",
                "actual_mode": "patent",
                "task_id": "task_patent_norm_001",
                "status": "queued",
                "last_seq": 0,
            },
            headers=_internal_headers("gateway"),
        )
        progress_response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_patent_norm_001/assistant-progress",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "task_id": "task_patent_norm_001",
                "status": "running",
                "content_delta": "结论来自专利 (patent_id=CN115132975B)。",
                "steps": [],
                "last_seq": 1,
            },
            headers=_internal_headers("gateway"),
        )
        terminal_response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_patent_norm_001/assistant-terminal",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "task_id": "task_patent_norm_001",
                "terminal_status": "completed",
                "last_seq": 2,
                "answer_text": "结论来自专利 (patent_id=CN115132975B)。",
                "steps": [],
                "failure": {},
            },
            headers=_internal_headers("gateway"),
        )

        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

    assert start_response.status_code == 200
    assert progress_response.status_code == 200
    assert terminal_response.status_code == 200
    assert detail["success"] is True
    assistant_message = detail["data"]["messages"][-1]
    assert "patent_id=" not in str(assistant_message.get("content") or "")
    assert "CN115132975B" in str(assistant_message.get("content") or "")


def test_internal_task_create_turn_allows_gateway_caller_and_materializes_both_messages(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="gateway owned task create turn")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/tasks/task_gateway_create_001/create-turn",
            json={
                "conversation_id": conversation_id,
                "user_id": 7,
                "trace_id": "task-gateway-create-trace",
                "source_service": "fastQA",
                "route": "kb_qa",
                "requested_mode": "fast",
                "actual_mode": "fast",
                "task_id": "task_gateway_create_001",
                "message": {
                    "role": "user",
                    "content": "atomic gateway hello",
                },
                "context_hints": {
                    "selected_file_ids": [3],
                    "last_turn_route_hint": "kb_qa",
                },
                "status": "queued",
                "last_seq": 0,
            },
            headers=_internal_headers("gateway"),
        )
        detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["conversation_id"] == conversation_id
    assert payload["task_id"] == "task_gateway_create_001"
    assert payload["status"] == "queued"
    assert payload["user_message_id"]
    assert payload["assistant_message_id"]
    assert [message["role"] for message in detail["data"]["messages"]] == ["user", "assistant"]
    assert detail["data"]["messages"][0]["content"] == "atomic gateway hello"
    assert detail["data"]["messages"][1]["metadata"]["task_id"] == "task_gateway_create_001"


def test_internal_context_snapshot_rejects_invalid_source_service_policy(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.get(
            "/internal/conversations/12/context-snapshot",
            params=_snapshot_query(actual_mode="thinking"),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "INTERNAL_SOURCE_SERVICE_FORBIDDEN"


def test_internal_context_snapshot_allows_fastqa_rerouted_thinking_request(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority rerouted snapshot")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id, requested_mode="thinking", actual_mode="fast"),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation_id


def test_internal_context_snapshot_allows_expired_recent_turns(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority expired snapshot")
        conversation_id = int(created["data"]["conversation_id"])
        service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="expired-snapshot-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:expired-snapshot-user:user",
            content="hello expired snapshot",
            context_hints={},
        )
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_expired_snapshot",
            trace_id="task-expired-snapshot",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="running",
            last_seq=2,
        )
        service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_expired_snapshot",
            terminal_status="expired",
            last_seq=3,
        )

        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation_id
    assert payload["recent_turns"][-1]["status"] == "expired"
    assert payload["recent_turns"][-1]["terminal_status"] == "expired"
    assert payload["user_id"] == 7


def test_internal_context_snapshot_normalizes_completed_recent_turns_to_done(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority completed snapshot")
        conversation_id = int(created["data"]["conversation_id"])
        service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="completed-snapshot-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:completed-snapshot-user:user",
            content="hello completed snapshot",
            context_hints={},
        )
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_completed_snapshot",
            trace_id="task-completed-snapshot",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="running",
            last_seq=2,
        )
        service.terminal_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_completed_snapshot",
            terminal_status="completed",
            last_seq=3,
            answer_text="done answer",
        )

        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation_id
    assert payload["recent_turns"][-1]["status"] == "done"
    assert payload["recent_turns"][-1]["terminal_status"] == "done"
    assert payload["recent_turns"][-1]["content"] == "done answer"


def test_internal_context_snapshot_allows_running_recent_turns(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority running snapshot")
        conversation_id = int(created["data"]["conversation_id"])
        service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="running-snapshot-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:running-snapshot-user:user",
            content="hello running snapshot",
            context_hints={},
        )
        service.start_authority_task_assistant(
            user_id=7,
            conversation_id=conversation_id,
            task_id="task_running_snapshot",
            trace_id="task-running-snapshot",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            status="running",
            last_seq=2,
        )

        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation_id
    assert payload["recent_turns"][-1]["status"] == "running"
    assert payload["recent_turns"][-1]["terminal_status"] == "running"
    assert payload["user_id"] == 7


def test_internal_user_write_allows_fastqa_rerouted_thinking_request(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority rerouted user write")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/user",
            json=_user_write_body(
                conversation_id=conversation_id,
                requested_mode="thinking",
                actual_mode="fast",
                idempotency_key=f"{conversation_id}:trc_fast_001:user",
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["conversation_id"] == conversation_id


def test_internal_assistant_async_allows_fastqa_rerouted_thinking_request(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority rerouted assistant write")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-async",
            json=_assistant_body(
                conversation_id=conversation_id,
                requested_mode="thinking",
                actual_mode="fast",
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["trace_id"] == "trc_fast_001"


def test_internal_assistant_terminal_async_allows_patentqa_patent_request(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority patent terminal write")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                conversation_id=conversation_id,
                source_service="patentQA",
                requested_mode="patent",
                actual_mode="patent",
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
            ),
            headers=_internal_headers("patentQA"),
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["trace_id"] == "trc_fast_001"


def test_internal_context_snapshot_read_does_not_require_idempotency_key(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority snapshot")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation_id
    assert payload["user_id"] == 7
    assert payload["snapshot_version"] == 1
    assert payload["summary"] == {
        "short_summary": "",
        "memory_facts": [],
        "open_threads": [],
    }
    assert payload["recent_turns"] == []
    assert payload["conversation_state"] == {
        "last_turn_route": None,
        "last_focus_file_ids": [],
        "last_assistant_trace_id": None,
    }


def test_internal_context_snapshot_contract_filters_non_final_messages(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority snapshot contract")
        conversation_id = int(created["data"]["conversation_id"])
        written = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trc_fast_user_001",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trc_fast_user_001:user",
            content="What changed in the experiment?",
            context_hints={"selected_file_ids": [3], "last_turn_route_hint": "kb_qa"},
        )
        assert written["success"] is True
        added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="The temperature increased by 5C.",
            metadata={
                "trace_id": "trc_fast_assistant_001",
                "route": "kb_qa",
                "used_files": [{"file_id": 3}],
                "steps": [{"stage": "retrieve"}],
                "timings": {"latency_ms": 42},
                "debug": {"planner": "legacy"},
            },
        )
        assert added["success"] is True

        with service._json_store.conversation_lock(user_id=7, conversation_id=conversation_id):
            document = service._json_store.load_document(user_id=7, conversation_id=conversation_id)
            messages = document.get("messages") if isinstance(document.get("messages"), list) else []
            messages.append(
                {
                    "message_id": "m_999999",
                    "role": "system",
                    "content": "trace: internal retrieval context",
                    "created_at": written["created_at"],
                    "metadata": {
                        "trace_id": "trc_internal_ignored",
                        "steps": [{"stage": "debug"}],
                        "timings": {"latency_ms": 1},
                    },
                }
            )
            document["messages"] = messages
            service._json_store.write_document(user_id=7, conversation_id=conversation_id, document=document)

        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id, trace_id="trc_fast_read_001"),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["role"] for item in payload["recent_turns"]] == ["user", "assistant"]
    assert [item["content"] for item in payload["recent_turns"]] == [
        "What changed in the experiment?",
        "The temperature increased by 5C.",
    ]
    assert payload["conversation_state"] == {
        "last_turn_route": "kb_qa",
        "last_focus_file_ids": [3],
        "last_assistant_trace_id": "trc_fast_assistant_001",
    }
    assert sorted(payload["summary"].keys()) == ["memory_facts", "open_threads", "short_summary"]


def test_internal_context_snapshot_generates_minimal_summary(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority snapshot summary")
        conversation_id = int(created["data"]["conversation_id"])
        written = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trc_fast_user_002",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trc_fast_user_002:user",
            content="Summarize the last finding.",
            context_hints={},
        )
        assert written["success"] is True
        added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="The last finding is that the catalyst stayed stable for 48 hours.",
            metadata={
                "trace_id": "trc_fast_assistant_002",
                "route": "kb_qa",
                "steps": [{"stage": "answer"}],
                "timings": {"latency_ms": 84},
            },
        )
        assert added["success"] is True

        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id, trace_id="trc_fast_read_002"),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {
        "short_summary": (
            "主题：Summarize the last finding.；最新结论："
            "The last finding is that the catalyst stayed stable for 48 hours."
        ),
        "memory_facts": [
            "The last finding is that the catalyst stayed stable for 48 hours.",
        ],
        "open_threads": [],
    }


def test_internal_context_snapshot_preserves_terminal_status_fields(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority snapshot terminal status")
        conversation_id = int(created["data"]["conversation_id"])
        user_written = service.add_authority_user_message(
            user_id=7,
            conversation_id=conversation_id,
            trace_id="trace-snapshot-user",
            source_service="fastQA",
            route="kb_qa",
            requested_mode="fast",
            actual_mode="fast",
            idempotency_key=f"{conversation_id}:trace-snapshot-user:user",
            content="why failed?",
            context_hints={},
        )
        assert user_written["success"] is True
        assistant_added = service.add_message(
            user_id=7,
            conversation_id=conversation_id,
            role="assistant",
            content="partial answer",
            metadata={
                "trace_id": "trace-snapshot-assistant",
                "route": "kb_qa",
                "terminal_status": "failed",
                "failure_stage": "llm_stream",
                "failure_message": "timeout",
                "retriable": True,
                "done_seen": False,
            },
        )
        assert assistant_added["success"] is True

        response = client.get(
            f"/internal/conversations/{conversation_id}/context-snapshot",
            params=_snapshot_query(conversation_id=conversation_id, trace_id="trace-snapshot-user"),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recent_turns"][-1]["status"] == "failed"
    assert payload["recent_turns"][-1]["terminal_status"] == "failed"
    assert payload["recent_turns"][-1]["failure_message"] == "timeout"


def test_internal_user_write_rejects_missing_idempotency_key(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    body = _user_write_body()
    body.pop("idempotency_key")

    with TestClient(app) as client:
        response = client.post(
            "/internal/conversations/12/messages/user",
            json=body,
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 422
    errors = response.json()["details"]["errors"]
    assert any(error["loc"][-1] == "idempotency_key" for error in errors)


def test_internal_user_write_rejects_invalid_idempotency_key(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.post(
            "/internal/conversations/12/messages/user",
            json=_user_write_body(idempotency_key="bad-key"),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"


def test_internal_user_write_accepts_valid_contract(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority user write")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/user",
            json=_user_write_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:user",
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["conversation_id"] == conversation_id
    assert payload["trace_id"] == "trc_fast_001"
    assert payload["idempotency_key"] == f"{conversation_id}:trc_fast_001:user"
    assert payload["deduped"] is False
    assert payload["message_id"]


def test_internal_assistant_async_rejects_non_final_event(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.post(
            "/internal/conversations/12/messages/assistant-async",
            json=_assistant_body(final_event={"done_seen": False, "answer_text": "partial"}),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 422
    errors = response.json()["details"]["errors"]
    assert any(error["loc"][-1] == "final_event" for error in errors)


def test_internal_assistant_async_accepts_valid_contract(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority assistant accept")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-async",
            json=_assistant_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload == {
        "accepted": True,
        "event_id": f"assistant-async:{conversation_id}:trc_fast_001",
        "trace_id": "trc_fast_001",
        "idempotency_key": f"{conversation_id}:trc_fast_001:assistant",
        "status": "accepted",
    }


def test_internal_assistant_terminal_async_accepts_failed_contract(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority assistant terminal failed")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["trace_id"] == "trc_fast_001"
    assert payload["idempotency_key"] == f"{conversation_id}:trc_fast_001:assistant"


def test_internal_assistant_terminal_async_accepts_partial_failed_contract(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority assistant terminal partial failed")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
                terminal_event={
                    "terminal_status": "failed",
                    "done_seen": False,
                    "answer_text": "partial answer",
                    "failure": {
                        "stage": "citation_validation",
                        "message": "validation timeout",
                        "code": "VALIDATION_TIMEOUT",
                        "retriable": True,
                    },
                },
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_internal_assistant_terminal_async_accepts_done_contract(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority assistant terminal done")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
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
                    "timings": {"latency_ms": 321},
                },
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_internal_assistant_terminal_async_rejects_done_contract_with_failure(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority assistant terminal invalid done")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
                terminal_event={
                    "terminal_status": "done",
                    "done_seen": True,
                    "answer_text": "final answer",
                    "failure": {
                        "stage": "citation_validation",
                        "message": "should not coexist",
                        "code": "CONTRADICTORY_TERMINAL",
                        "retriable": False,
                    },
                }
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 422
    errors = response.json()["details"]["errors"]
    assert any(error["loc"][-1] == "terminal_event" for error in errors)


def test_internal_assistant_terminal_async_accepts_canceled_without_failure(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client, _authority_harness(client) as service:
        created = service.create_conversation(user_id=7, title="authority assistant terminal canceled")
        conversation_id = int(created["data"]["conversation_id"])
        response = client.post(
            f"/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                conversation_id=conversation_id,
                idempotency_key=f"{conversation_id}:trc_fast_001:assistant",
                terminal_event={
                    "terminal_status": "canceled",
                    "done_seen": False,
                    "answer_text": "",
                    "steps": [],
                    "references": [],
                    "reference_objects": [],
                    "reference_links": [],
                    "pdf_links": [],
                    "doi_locations": {},
                    "used_files": [],
                    "timings": {"latency_ms": 321},
                },
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_internal_assistant_terminal_async_rejects_canceled_failure_without_message(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.post(
            "/internal/conversations/12/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                terminal_event={
                    "terminal_status": "canceled",
                    "done_seen": False,
                    "answer_text": "",
                    "failure": {
                        "stage": "user_stop",
                        "message": "",
                        "retriable": False,
                    },
                },
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 422
    errors = response.json()["details"]["errors"]
    assert any(error["loc"][-1] == "terminal_event" for error in errors)


def test_internal_assistant_terminal_async_rejects_canceled_failure_without_explicit_false_retriable(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.post(
            "/internal/conversations/12/messages/assistant-terminal-async",
            json=_assistant_terminal_body(
                terminal_event={
                    "terminal_status": "canceled",
                    "done_seen": False,
                    "answer_text": "",
                    "failure": {
                        "stage": "user_stop",
                        "message": "user canceled",
                    },
                },
            ),
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 422
    errors = response.json()["details"]["errors"]
    assert any(error["loc"][-1] == "terminal_event" for error in errors)
