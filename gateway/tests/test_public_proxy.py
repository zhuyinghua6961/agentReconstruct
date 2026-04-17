import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.integrations.redis.service import RedisService
from app.main import app
from app.routers.public_proxy import router as public_proxy_router
from app.services import execution_queue_status as queue_status_module
from app.services.execution_event_relay import ExecutionEventRelayStore
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


@pytest.fixture(autouse=True)
def _fresh_public_proxy_task_state():
    previous_queue = app.state.execution_queue_status_store
    previous_relay = app.state.execution_event_relay_store
    previous_slot_leases = app.state.execution_slot_lease_store
    app.state.execution_queue_status_store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
    )
    app.state.execution_event_relay_store = ExecutionEventRelayStore(
        redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
    )
    app.state.execution_slot_lease_store = ExecutionSlotLeaseStore(
        redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
    )
    try:
        yield
    finally:
        app.state.execution_queue_status_store = previous_queue
        app.state.execution_event_relay_store = previous_relay
        app.state.execution_slot_lease_store = previous_slot_leases
        app.state.proxy_service.set_transport(None)


class _TransportGuard:
    def __init__(self, handler):
        self._transport = httpx.MockTransport(handler)

    def __enter__(self):
        app.state.proxy_service.set_transport(self._transport)
        app.state.conversation_persistence_service.set_transport(self._transport)
        app.state.quota_proxy_service.set_transport(self._transport)
        return self

    def __exit__(self, exc_type, exc, tb):
        app.state.proxy_service.set_transport(None)
        app.state.conversation_persistence_service.set_transport(None)
        app.state.quota_proxy_service.set_transport(None)
        return False


class _ChunkedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"chunk-1"
        yield b"chunk-2"

    async def aclose(self):
        return None


def test_public_proxy_forwards_json_to_public_backend():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/conversations"
        assert request.url.query == b"page=2"
        assert request.headers["authorization"] == "Bearer demo"
        assert request.headers["x-trace-id"]
        return httpx.Response(200, json={"success": True, "source": "public"})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get("/api/conversations?page=2", headers={"Authorization": "Bearer demo"})

    assert response.status_code == 200
    assert response.json()["source"] == "public"
    assert response.headers["x-gateway-backend"] == "public"


@pytest.mark.parametrize("path", ["/api/conversations", "/api/v1/conversations"])
def test_public_proxy_enriches_conversation_list_with_live_active_task(path: str):
    app.state.execution_queue_status_store.put_request(
        {
            "request_id": "task_list_live",
            "status": "queued",
            "conversation_id": 12,
            "assistant_message_id": "msg_list_live",
            "requested_mode": "thinking",
            "actual_mode": "thinking",
            "route": "thinking_qa",
            "queue_tier": "low",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:00+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
            "expires_at": "2026-04-06T10:15:00+00:00",
            "cancel_allowed": True,
            "user_id": 42,
        },
        ttl_seconds=900,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == path
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "conversations": [
                        {
                            "conversation_id": 12,
                            "user_id": 42,
                            "title": "queued",
                            "message_count": 2,
                            "created_at": "2026-04-06T09:00:00+00:00",
                            "updated_at": "2026-04-06T09:30:00+00:00",
                        },
                        {
                            "conversation_id": 13,
                            "user_id": 42,
                            "title": "idle",
                            "message_count": 1,
                            "created_at": "2026-04-06T09:00:00+00:00",
                            "updated_at": "2026-04-06T09:30:00+00:00",
                        },
                    ],
                    "total_count": 2,
                    "page": 1,
                    "page_size": 20,
                },
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get(path, headers={"Authorization": "Bearer demo"})

    assert response.status_code == 200
    conversations = response.json()["data"]["conversations"]
    assert conversations[0]["active_task"]["task_id"] == "task_list_live"
    assert conversations[0]["active_task"]["status"] == "queued"
    assert conversations[1]["active_task"] is None


@pytest.mark.parametrize("path", ["/api/conversations/12", "/api/v1/conversations/12"])
def test_public_proxy_enriches_conversation_detail_with_live_active_task(path: str):
    app.state.execution_queue_status_store.put_request(
        {
            "request_id": "task_detail_live",
            "status": "running",
            "conversation_id": 12,
            "assistant_message_id": "msg_detail_live",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:01:00+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
            "admitted_at": "2026-04-06T10:00:10+00:00",
            "started_at": "2026-04-06T10:00:20+00:00",
            "expires_at": "2026-04-06T10:15:00+00:00",
            "cancel_allowed": True,
            "user_id": 42,
        },
        ttl_seconds=900,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == path
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "conversation_id": 12,
                    "user_id": 42,
                    "title": "detail",
                    "message_count": 2,
                    "created_at": "2026-04-06T09:00:00+00:00",
                    "updated_at": "2026-04-06T09:30:00+00:00",
                    "messages": [],
                    "uploaded_files": [],
                    "uploaded_files_all": [],
                    "pdf_files": [],
                    "excel_files": [],
                },
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get(path, headers={"Authorization": "Bearer demo"})

    assert response.status_code == 200
    active_task = response.json()["data"]["active_task"]
    assert active_task["task_id"] == "task_detail_live"
    assert active_task["status"] == "running"
    assert active_task["assistant_message_id"] == "msg_detail_live"


def test_public_proxy_detail_read_reconciles_expired_task_before_fetching_detail():
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(queue_status_module.time, "time", lambda: 1000.0)
        app.state.execution_queue_status_store.put_request(
            {
                "request_id": "task_expired_detail",
                "status": "queued",
                "conversation_id": 12,
                "assistant_message_id": "msg_expired_detail",
                "requested_mode": "fast",
                "actual_mode": "fast",
                "route": "kb_qa",
                "queue_tier": "high",
                "created_at": "2026-04-06T10:00:00+00:00",
                "updated_at": "2026-04-06T10:00:00+00:00",
                "enqueued_at": "2026-04-06T10:00:00+00:00",
                "expires_at": "2026-04-06T10:15:00+00:00",
                "cancel_allowed": True,
                "user_id": 42,
                "quota_grant_id": "grant-expired-detail",
            },
            ttl_seconds=10,
        )
        monkeypatch.setattr(queue_status_module.time, "time", lambda: 1011.0)
        state = {
            "messages": [
                {"message_id": "m_user", "role": "user", "content": "hello"},
                {
                    "message_id": "msg_expired_detail",
                    "role": "assistant",
                    "content": "",
                    "status": "queued",
                    "metadata": {"task_id": "task_expired_detail", "task_status": "queued"},
                },
            ],
            "active_task_id": "task_expired_detail",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/internal/conversations/12/tasks/task_expired_detail/assistant-terminal":
                state["messages"][1]["status"] = "expired"
                state["messages"][1]["metadata"] = {
                    **state["messages"][1]["metadata"],
                    "task_status": "expired",
                    "terminal_status": "expired",
                }
                state["active_task_id"] = None
                return httpx.Response(200, json={"success": True, "status": "expired"})
            if request.url.path == "/internal/quota/grants/grant-expired-detail/finalize":
                return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-expired-detail", "counted": False, "idempotent": False}})
            if request.url.path == "/api/conversations/12":
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "conversation_id": 12,
                            "user_id": 42,
                            "title": "detail",
                            "message_count": len(state["messages"]),
                            "created_at": "2026-04-06T09:00:00+00:00",
                            "updated_at": "2026-04-06T09:30:00+00:00",
                            "messages": list(state["messages"]),
                            "uploaded_files": [],
                            "uploaded_files_all": [],
                            "pdf_files": [],
                            "excel_files": [],
                        },
                    },
                )
            raise AssertionError(f"unexpected path: {request.url.path}")

        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.get("/api/conversations/12", headers={"Authorization": "Bearer demo"})

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["active_task"] is None
        assert payload["messages"][1]["status"] == "expired"
        stored = app.state.execution_queue_status_store.get_request("task_expired_detail")
        assert stored is not None
        assert stored["status"] == "expired"
        assert stored["terminal_sync_pending"] is False
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize(
    ("path", "task_status", "expected_active_task"),
    [
        ("/api/conversations", "completed", None),
        ("/api/v1/conversations/12", "failed", None),
    ],
)
def test_public_proxy_sets_active_task_null_when_no_live_task(path: str, task_status: str, expected_active_task):
    app.state.execution_queue_status_store.put_request(
        {
            "request_id": "task_not_live",
            "status": task_status,
            "conversation_id": 12,
            "assistant_message_id": "msg_not_live",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "queue_tier": "high",
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:05:00+00:00",
            "enqueued_at": "2026-04-06T10:00:00+00:00",
            "expires_at": "2026-04-06T10:15:00+00:00",
            "cancel_allowed": False,
            "user_id": 42,
        },
        ttl_seconds=900,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if path.endswith("/12"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "conversation_id": 12,
                        "user_id": 42,
                        "title": "detail",
                        "message_count": 0,
                        "created_at": "2026-04-06T09:00:00+00:00",
                        "updated_at": "2026-04-06T09:30:00+00:00",
                        "messages": [],
                        "uploaded_files": [],
                        "uploaded_files_all": [],
                        "pdf_files": [],
                        "excel_files": [],
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "conversations": [
                        {
                            "conversation_id": 12,
                            "user_id": 42,
                            "title": "done",
                            "message_count": 1,
                            "created_at": "2026-04-06T09:00:00+00:00",
                            "updated_at": "2026-04-06T09:30:00+00:00",
                        }
                    ],
                    "total_count": 1,
                    "page": 1,
                    "page_size": 20,
                },
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get(path, headers={"Authorization": "Bearer demo"})

    assert response.status_code == 200
    data = response.json()["data"]
    if path.endswith("/12"):
        assert data["active_task"] is expected_active_task
    else:
        assert data["conversations"][0]["active_task"] is expected_active_task


def test_public_proxy_degrades_to_null_active_task_when_task_expires_during_enrichment(monkeypatch):
    stale_record = {
        "request_id": "task_race_vanished",
        "status": "queued",
        "conversation_id": 12,
        "assistant_message_id": "msg_race_vanished",
        "requested_mode": "fast",
        "actual_mode": "fast",
        "route": "kb_qa",
        "queue_tier": "high",
        "created_at": "2026-04-06T10:00:00+00:00",
        "updated_at": "2026-04-06T10:00:00+00:00",
        "enqueued_at": "2026-04-06T10:00:00+00:00",
        "expires_at": "2026-04-06T10:15:00+00:00",
        "cancel_allowed": True,
        "user_id": 42,
    }
    monkeypatch.setattr(app.state.execution_queue_status_store, "list_requests", lambda status=None: [dict(stale_record)])

    def _missing_summary(self, task_id: str):
        assert task_id == "task_race_vanished"
        raise HTTPException(status_code=404, detail="task_not_found")

    monkeypatch.setattr("app.routers.public_proxy.QATaskService.build_task_summary", _missing_summary)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/conversations"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "conversations": [
                        {
                            "conversation_id": 12,
                            "user_id": 42,
                            "title": "race",
                            "message_count": 1,
                            "created_at": "2026-04-06T09:00:00+00:00",
                            "updated_at": "2026-04-06T09:30:00+00:00",
                        }
                    ],
                    "total_count": 1,
                    "page": 1,
                    "page_size": 20,
                },
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get("/api/conversations", headers={"Authorization": "Bearer demo"})

    assert response.status_code == 200
    assert response.json()["data"]["conversations"][0]["active_task"] is None


def test_public_proxy_accepts_x_request_id_and_forwards_canonical_trace_header():
    def handler(request: httpx.Request) -> httpx.Response:
        header_names = [name.lower() for name, _ in request.headers.raw]
        trace_values = [value for name, value in request.headers.raw if name.lower() == b"x-trace-id"]
        assert trace_values == [b"trace-from-request-id"]
        assert b"x-request-id" not in header_names
        return httpx.Response(200, json={"success": True, "trace_id": trace_values[0].decode("utf-8")})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get(
            "/api/conversations?page=2",
            headers={
                "Authorization": "Bearer demo",
                "X-Request-ID": "trace-from-request-id",
            },
        )

    assert response.status_code == 200
    assert response.json()["trace_id"] == "trace-from-request-id"
    assert response.headers["x-trace-id"] == "trace-from-request-id"


@pytest.mark.parametrize(
    ("method", "path", "expected_path", "json_body", "expected_query"),
    [
        (
            "PUT",
            "/api/v1/auth/security-questions",
            "/api/v1/auth/security-questions",
            {"questions": [{"question": "q1", "answer": "a1"}]},
            b"",
        ),
        (
            "PUT",
            "/api/v1/conversations/12/title",
            "/api/v1/conversations/12/title",
            {"title": "updated"},
            b"",
        ),
        (
            "POST",
            "/api/v1/reference_preview",
            "/api/v1/reference_preview",
            {"doi": ["10.1000/test"], "max_items": 5},
            b"",
        ),
        (
            "GET",
            "/api/v1/quota/my",
            "/api/v1/quota/my",
            None,
            b"",
        ),
        (
            "GET",
            "/api/admin/users?page=1&page_size=10",
            "/api/admin/users",
            None,
            b"page=1&page_size=10",
        ),
        (
            "POST",
            "/api/admin/users/batch-delete",
            "/api/admin/users/batch-delete",
            {"user_ids": [1, 2, 3]},
            b"",
        ),
        (
            "POST",
            "/api/admin/users/batch-type",
            "/api/admin/users/batch-type",
            {"user_ids": [1, 2, 3], "user_type": "super"},
            b"",
        ),
        (
            "GET",
            "/api/auth/departments/tree",
            "/api/auth/departments/tree",
            None,
            b"",
        ),
        (
            "PUT",
            "/api/auth/department",
            "/api/auth/department",
            {"primary_department_id": 1, "secondary_department_id": 11},
            b"",
        ),
        (
            "PUT",
            "/api/auth/username",
            "/api/auth/username",
            {"username": "alice-renamed"},
            b"",
        ),
        (
            "GET",
            "/api/admin/departments/tree",
            "/api/admin/departments/tree",
            None,
            b"",
        ),
        (
            "POST",
            "/api/admin/departments/primary",
            "/api/admin/departments/primary",
            {"name": "计算机学院"},
            b"",
        ),
        (
            "PUT",
            "/api/admin/departments/primary/1",
            "/api/admin/departments/primary/1",
            {"name": "信息学院"},
            b"",
        ),
        (
            "PUT",
            "/api/admin/departments/primary/1/status",
            "/api/admin/departments/primary/1/status",
            {"status": "disabled"},
            b"",
        ),
        (
            "POST",
            "/api/admin/departments/secondary",
            "/api/admin/departments/secondary",
            {"primary_department_id": 1, "name": "软件工程系"},
            b"",
        ),
        (
            "PUT",
            "/api/admin/departments/secondary/11",
            "/api/admin/departments/secondary/11",
            {"name": "计算机系"},
            b"",
        ),
        (
            "PUT",
            "/api/admin/departments/secondary/11/status",
            "/api/admin/departments/secondary/11/status",
            {"status": "disabled"},
            b"",
        ),
        (
            "GET",
            "/api/admin/departments/secondary/11/users",
            "/api/admin/departments/secondary/11/users",
            None,
            b"",
        ),
        (
            "POST",
            "/api/admin/departments/batch-import",
            "/api/admin/departments/batch-import",
            None,
            b"",
        ),
        (
            "PUT",
            "/api/admin/users/7/username",
            "/api/admin/users/7/username",
            {"username": "alice-super"},
            b"",
        ),
        (
            "GET",
            "/api/admin/departments/import-template",
            "/api/admin/departments/import-template",
            None,
            b"",
        ),
    ],
)
def test_public_proxy_forwards_extended_route_surface(method, path, expected_path, json_body, expected_query):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == method
        assert request.url.path == expected_path
        assert request.url.query == expected_query
        assert request.headers["authorization"] == "Bearer demo"
        return httpx.Response(200, json={"success": True, "path": request.url.path, "method": request.method})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.request(method, path, json=json_body, headers={"Authorization": "Bearer demo"})

    assert response.status_code == 200
    assert response.json()["path"] == expected_path
    assert response.json()["method"] == method
    assert response.headers["x-gateway-backend"] == "public"


def test_public_proxy_preserves_inline_pdf_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/view_pdf/10.1000/test"
        return httpx.Response(
            200,
            stream=_ChunkedStream(),
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'inline; filename="paper.pdf"',
                "cache-control": "private, max-age=60",
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get("/api/view_pdf/10.1000/test")

    assert response.status_code == 200
    assert response.content == b"chunk-1chunk-2"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["content-type"].startswith("application/pdf")


def test_public_proxy_preserves_query_token_for_v1_view_pdf():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/view_pdf/10.1000/test"
        assert request.url.query == b"token=token-1"
        return httpx.Response(
            200,
            stream=_ChunkedStream(),
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'inline; filename="paper.pdf"',
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get("/api/v1/view_pdf/10.1000/test?token=token-1")

    assert response.status_code == 200
    assert response.content == b"chunk-1chunk-2"
    assert response.headers["content-disposition"].startswith("inline;")


def test_public_proxy_forwards_patent_original_json_requests():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/patent/original/CN123456789A"
        assert request.url.query == b"section=claim&claim_number=1"
        assert request.headers["authorization"] == "Bearer demo"
        return httpx.Response(
            200,
            json={
                "success": True,
                "canonical_patent_id": "CN123456789A",
                "section": "claim",
                "section_label": "权利要求1",
            },
            headers={"etag": '"patent-original:version-1"'},
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get(
            "/api/v1/patent/original/CN123456789A?section=claim&claim_number=1",
            headers={"Authorization": "Bearer demo"},
        )

    assert response.status_code == 200
    assert response.json()["section_label"] == "权利要求1"
    assert response.headers["etag"] == '"patent-original:version-1"'
    assert response.headers["x-gateway-backend"] == "public"


def test_public_proxy_forwards_patent_original_head_requests():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert request.url.path == "/api/patent/original/CN123456789A"
        assert request.url.query == b"section=fulltext"
        return httpx.Response(
            200,
            headers={
                "etag": '"patent-original:version-2"',
                "cache-control": "public, max-age=300",
                "content-type": "application/pdf",
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.head("/api/patent/original/CN123456789A?section=fulltext")

    assert response.status_code == 200
    assert response.text == ""
    assert response.headers["etag"] == '"patent-original:version-2"'
    assert response.headers["x-gateway-backend"] == "public"


def test_public_proxy_streams_patent_fulltext_requests(monkeypatch):
    class _Handle:
        status_code = 200
        headers = {
            "content-type": "application/pdf",
            "etag": '"patent-original:version-3"',
        }

        async def body_iter(self):
            yield b"chunk-1"
            yield b"chunk-2"

    async def _open_request_stream(*, request, target, path=None):
        assert request.url.path == "/api/patent/original/CN123456789A"
        assert request.url.query == "section=fulltext"
        _ = target, path
        return _Handle()

    async def _forward(*, request, target, path=None):
        _ = request, target, path
        raise AssertionError("fulltext patent original route should use streaming proxy")

    monkeypatch.setattr(app.state.proxy_service, "open_request_stream", _open_request_stream)
    monkeypatch.setattr(app.state.proxy_service, "forward", _forward)

    client = TestClient(app)
    response = client.get("/api/patent/original/CN123456789A?section=fulltext")

    assert response.status_code == 200
    assert response.content == b"chunk-1chunk-2"
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["etag"] == '"patent-original:version-3"'


def test_public_proxy_streams_patent_fulltext_requests_when_section_is_omitted(monkeypatch):
    class _Handle:
        status_code = 200
        headers = {
            "content-type": "application/pdf",
            "etag": '"patent-original:version-4"',
        }

        async def body_iter(self):
            yield b"chunk-a"
            yield b"chunk-b"

    async def _open_request_stream(*, request, target, path=None):
        assert request.url.path == "/api/patent/original/CN123456789A"
        assert request.url.query == ""
        _ = target, path
        return _Handle()

    async def _forward(*, request, target, path=None):
        _ = request, target, path
        raise AssertionError("default patent original route should stream because section defaults to fulltext")

    monkeypatch.setattr(app.state.proxy_service, "open_request_stream", _open_request_stream)
    monkeypatch.setattr(app.state.proxy_service, "forward", _forward)

    client = TestClient(app)
    response = client.get("/api/patent/original/CN123456789A")

    assert response.status_code == 200
    assert response.content == b"chunk-achunk-b"
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["etag"] == '"patent-original:version-4"'


def test_public_proxy_streams_conversation_file_download():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/conversations/12/files/34/download"
        assert request.headers["authorization"] == "Bearer demo"
        return httpx.Response(
            200,
            stream=_ChunkedStream(),
            headers={
                "content-type": "application/octet-stream",
                "content-disposition": 'attachment; filename="paper.pdf"',
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get(
            "/api/conversations/12/files/34/download",
            headers={"Authorization": "Bearer demo"},
        )

    assert response.status_code == 200
    assert response.content == b"chunk-1chunk-2"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-gateway-backend"] == "public"


def test_public_proxy_preserves_query_token_for_v1_file_download():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/conversations/12/files/34/download"
        assert request.url.query == b"token=token-1"
        return httpx.Response(
            200,
            stream=_ChunkedStream(),
            headers={
                "content-type": "application/octet-stream",
                "content-disposition": 'attachment; filename="paper.pdf"',
            },
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.get("/api/v1/conversations/12/files/34/download?token=token-1")

    assert response.status_code == 200
    assert response.content == b"chunk-1chunk-2"
    assert response.headers["content-disposition"].startswith("attachment;")


def test_public_proxy_streams_upload_multipart_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/upload_pdf"
        assert request.headers["authorization"] == "Bearer demo"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        body = request.read()
        assert b'name="conversation_id"' in body
        assert b"12" in body
        assert b'name="file"; filename="paper.pdf"' in body
        assert b"mock-pdf-body" in body
        return httpx.Response(200, json={"success": True, "message": "ok"})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/upload_pdf",
            data={"conversation_id": "12"},
            files={"file": ("paper.pdf", b"mock-pdf-body", "application/pdf")},
            headers={"Authorization": "Bearer demo"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.headers["x-gateway-backend"] == "public"


def test_public_proxy_forwards_post_body_and_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/translate"
        assert request.headers["content-type"].startswith("application/json")
        assert request.content == b'{"text":"hello"}'
        return httpx.Response(200, json={"translated": "你好"})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post("/api/translate", json={"text": "hello"})

    assert response.status_code == 200
    assert response.json()["translated"] == "你好"


def test_public_proxy_forwards_translate_document_request():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/translate_document"
        assert request.headers["content-type"].startswith("application/json")
        assert request.content == b'{"document_type":"patent","document_id":"CN123456789A"}'
        return httpx.Response(200, json={"translated_text": "专利译文"})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/v1/translate_document",
            json={"document_type": "patent", "document_id": "CN123456789A"},
        )

    assert response.status_code == 200
    assert response.json()["translated_text"] == "专利译文"


@pytest.mark.parametrize(
    ("path", "expected_upstream_path"),
    [
        ("/api/health", "/health"),
        ("/api/clear_pdf", "/clear_pdf"),
    ],
)
def test_public_proxy_rewrites_legacy_only_public_paths(path, expected_upstream_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_upstream_path
        return httpx.Response(200, json={"path": request.url.path})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.request("POST" if path.endswith("clear_pdf") else "GET", path)

    assert response.status_code == 200
    assert response.json()["path"] == expected_upstream_path


@pytest.mark.parametrize(
    "path",
    [
        "/internal/quota/grants/precheck",
        "/internal/quota/grants/{grant_id}/finalize",
    ],
)
def test_public_proxy_does_not_expose_internal_quota_grant_endpoints(path):
    registered_paths = {route.path for route in public_proxy_router.routes}

    assert path not in registered_paths
