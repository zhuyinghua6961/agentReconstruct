from __future__ import annotations

from contextlib import contextmanager
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.main import app
from app.integrations.redis import RedisService
from app.integrations.storage.local import LocalStorageBackend
from app.modules.auth.deps import require_auth_context
from app.modules.conversation.internal_api import require_internal_authority
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

    user_write_route = _route_for("/internal/conversations/{conversation_id}/messages/user", "POST")
    snapshot_route = _route_for("/internal/conversations/{conversation_id}/context-snapshot", "GET")
    assistant_route = _route_for("/internal/conversations/{conversation_id}/messages/assistant-async", "POST")

    assert require_auth_context not in {dep.call for dep in user_write_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in snapshot_route.dependant.dependencies}
    assert require_auth_context not in {dep.call for dep in assistant_route.dependant.dependencies}

    assert require_internal_authority in {dep.call for dep in user_write_route.dependant.dependencies}
    assert require_internal_authority in {dep.call for dep in snapshot_route.dependant.dependencies}
    assert require_internal_authority in {dep.call for dep in assistant_route.dependant.dependencies}


def test_internal_context_snapshot_requires_trusted_headers(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.get(
            "/internal/conversations/12/context-snapshot",
            params=_snapshot_query(),
        )

    assert response.status_code == 401
    assert response.json()["code"] == "INTERNAL_AUTH_MISSING"


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
