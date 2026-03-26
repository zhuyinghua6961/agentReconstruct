import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.core.trace import TRACE_ID_HEADER
from app.models.files import ConversationFileRow
from app.providers.conversation_files.public_http import PublicHttpConversationFileProvider
from app.services.conversation_files import ConversationFileService


class _TransportGuard:
    def __init__(self, handler):
        self._transport = httpx.MockTransport(handler)

    def __enter__(self):
        app.state.proxy_service.set_transport(self._transport)
        app.state.conversation_persistence_service.set_transport(self._transport)
        return self

    def __exit__(self, exc_type, exc, tb):
        app.state.proxy_service.set_transport(None)
        app.state.conversation_persistence_service.set_transport(None)
        return False


class _ConversationFilesStub:
    def __init__(self, rows):
        self._rows = rows

    async def list_files(self, *, conversation_id, request=None):
        _ = conversation_id, request
        return list(self._rows)


class _FakeConversationPersistenceService:
    def __init__(self) -> None:
        self.user_calls: list[dict] = []
        self.assistant_calls: list[dict] = []
        self.transport = None

    def set_transport(self, transport) -> None:
        self.transport = transport

    async def persist_user_message(self, **kwargs):
        self.user_calls.append(kwargs)

    async def persist_assistant_summary(self, **kwargs):
        self.assistant_calls.append(kwargs)

    def new_stream_summary(self):
        from app.services.conversation_persistence import StreamSummary

        return StreamSummary(
            references=[],
            reference_links=[],
            pdf_links=[],
            doi_locations={},
            used_files=[],
            timings={},
            file_selection={},
            steps=[],
        )

    async def extract_stream(self, *, body_iter, summary):
        async for chunk in body_iter:
            text = chunk.decode("utf-8")
            if '"type":"thinking"' in text:
                summary.steps.append({"step": "thinking_1", "message": "阶段一", "status": "success", "data": {}})
            if '"type":"content"' in text:
                summary.assistant_content += "hello"
            if '"type":"done"' in text:
                summary.done_seen = True
                summary.query_mode = "fast"
                summary.reference_links = [{"doi": "10.1/demo", "pdf_url": "/api/view_pdf/10.1/demo"}]
            yield chunk


def test_mode_ask_routes_plain_question_to_requested_backend():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={
                "question": "磷酸铁锂电压范围是多少？",
                "requested_mode": "thinking",
                "pdf_context": {"selected_ids": [11]},
            },
        )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/thinking/ask")
    assert captured["body"]["actual_mode"] == "thinking"
    assert captured["body"]["route"] == "kb_qa"
    assert response.headers["x-gateway-backend"] == "thinking"


def test_mode_ask_fast_does_not_persist_gateway_messages():
    original_persistence = app.state.conversation_persistence_service
    fake_persistence = _FakeConversationPersistenceService()
    app.state.conversation_persistence_service = fake_persistence
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/fast/ask",
                json={
                    "question": "plain qa",
                    "requested_mode": "fast",
                    "conversation_id": 7,
                },
            )
    finally:
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["url"].endswith("/api/fast/ask")
    assert captured["body"]["actual_mode"] == "fast"
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "fast"


def test_mode_ask_routes_file_question_to_fast_backend():
    original_persistence = app.state.conversation_persistence_service
    fake_persistence = _FakeConversationPersistenceService()
    app.state.conversation_persistence_service = fake_persistence
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "请总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["url"].endswith("/api/fast/ask")
    assert captured["body"]["actual_mode"] == "fast"
    assert captured["body"]["route"] == "pdf_qa"
    assert captured["body"]["source_scope"] == "pdf"
    assert captured["body"]["kb_enabled"] is False
    assert captured["body"]["selected_file_ids"] == [11]
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "fast"

def test_mode_ask_routes_mixed_question_to_fast_backend():
    original = app.state.conversation_file_service
    original_persistence = app.state.conversation_persistence_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="battery-paper.pdf",
            )
        ]
    )
    fake_persistence = _FakeConversationPersistenceService()
    app.state.conversation_persistence_service = fake_persistence
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "请结合知识库总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 101,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["url"].endswith("/api/fast/ask")
    assert captured["body"]["actual_mode"] == "fast"
    assert captured["body"]["route"] == "hybrid_qa"
    assert captured["body"]["source_scope"] == "pdf+kb"
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "fast"

def test_v1_ask_stream_alias_is_removed():
    called = {"upstream": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["upstream"] = True
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"metadata","query_mode":"thinking"}\n\n'
                b'data: {"type":"content","content":"hello"}\n\n'
                b'data: {"type":"done","final_answer":"hello"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/v1/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking"},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 404
    assert body == b'{"detail":"Not Found"}'
    assert called["upstream"] is False


def test_mode_ask_stream_routes_file_question_to_fast_backend():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert str(request.url).endswith("/api/fast/ask_stream")
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"metadata","query_mode":"fast","route":"pdf_qa"}\n\n'
                b'data: {"type":"content","content":"hello"}\n\n'
                b'data: {"type":"done","final_answer":"hello","route":"pdf_qa"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={
                "question": "请总结这篇文献",
                "requested_mode": "thinking",
                "pdf_context": {"selected_ids": [11]},
            },
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert calls == ["/api/fast/ask_stream"]
    assert response.headers["x-gateway-backend"] == "fast"


def test_mode_ask_stream_passthroughs_sse():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/thinking/ask_stream")
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"metadata","query_mode":"thinking"}\n\n'
                b'data: {"type":"content","content":"hello"}\n\n'
                b'data: {"type":"done","final_answer":"hello"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking"},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"type":"content"' in body
    assert response.headers["x-gateway-backend"] == "thinking"


def test_mode_ask_stream_returns_sse_error_when_upstream_connect_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking"},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"code":"UPSTREAM_STREAM_UNAVAILABLE"' in body
    assert b'"backend":"thinking"' in body
    assert response.headers["x-gateway-backend"] == "thinking"


def test_mode_ask_stream_does_not_persist_gateway_messages():
    original = app.state.conversation_persistence_service
    fake_persistence = _FakeConversationPersistenceService()
    app.state.conversation_persistence_service = fake_persistence

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/fast/ask_stream")
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"thinking","content":"\xe9\x98\xb6\xe6\xae\xb5\xe4\xb8\x80"}\n\n'
                b'data: {"type":"content","content":"hello"}\n\n'
                b'data: {"type":"done","final_answer":"hello","reference_links":[{"doi":"10.1/demo","pdf_url":"/api/view_pdf/10.1/demo"}]}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/fast/ask_stream",
                json={"question": "plain qa", "requested_mode": "fast", "conversation_id": 42},
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_persistence_service = original

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []


def test_mode_ask_stream_does_not_persist_file_turn_context_hints():
    original = app.state.conversation_persistence_service
    fake_persistence = _FakeConversationPersistenceService()
    app.state.conversation_persistence_service = fake_persistence

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/fast/ask_stream")
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"metadata","query_mode":"fast","route":"pdf_qa"}\n\n'
                b'data: {"type":"content","content":"hello"}\n\n'
                b'data: {"type":"done","final_answer":"hello","route":"pdf_qa"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/thinking/ask_stream",
                json={
                    "question": "请总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                _ = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_persistence_service = original

    assert response.status_code == 200
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []


def test_mode_ask_thinking_skips_public_message_persistence():
    original_persistence = app.state.conversation_persistence_service
    fake_persistence = _FakeConversationPersistenceService()
    app.state.conversation_persistence_service = fake_persistence
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/thinking/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "plain qa",
                    "requested_mode": "thinking",
                    "conversation_id": 42,
                },
            )
    finally:
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert calls == ["/api/thinking/ask"]
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "thinking"

def test_mode_ask_stream_thinking_skips_public_message_persistence():
    original_persistence = app.state.conversation_persistence_service
    fake_persistence = _FakeConversationPersistenceService()
    app.state.conversation_persistence_service = fake_persistence
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/thinking/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"thinking"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"hello"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/thinking/ask_stream",
                json={
                    "question": "plain qa",
                    "requested_mode": "thinking",
                    "conversation_id": 42,
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert calls == ["/api/thinking/ask_stream"]
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "thinking"

def test_mode_ask_uses_conversation_file_metadata_for_table_route():
    original = app.state.conversation_file_service
    original_persistence = app.state.conversation_persistence_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=33,
                file_type="excel",
                file_name="cells.xlsx",
                file_meta={"columns": ["电芯编号", "开路电压_V", "供应商"]},
            )
        ]
    )
    app.state.conversation_persistence_service = _FakeConversationPersistenceService()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/conversations/"):
            return httpx.Response(201, json={"success": True, "data": {"message_id": 1}})
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "开路电压_V 的分布是什么？",
                    "requested_mode": "thinking",
                    "conversation_id": 101,
                    "pdf_context": {"selected_ids": [33]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["body"]["route"] == "tabular_qa"
    assert captured["body"]["actual_mode"] == "fast"
    assert captured["body"]["source_scope"] == "table"
    assert captured["body"]["kb_enabled"] is False
    assert captured["body"]["selected_file_ids"] == [33]
    assert captured["body"]["execution_files"][0]["file_type"] == "excel"


def test_mode_ask_routes_pdf_kb_question_to_hybrid_scope():
    original = app.state.conversation_file_service
    original_persistence = app.state.conversation_persistence_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="battery-paper.pdf",
            )
        ]
    )
    app.state.conversation_persistence_service = _FakeConversationPersistenceService()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/conversations/"):
            return httpx.Response(201, json={"success": True, "data": {"message_id": 1}})
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "请结合知识库总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 101,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["body"]["route"] == "hybrid_qa"
    assert captured["body"]["source_scope"] == "pdf+kb"
    assert captured["body"]["kb_enabled"] is True
    assert captured["body"]["selected_file_ids"] == [11]


def test_mode_ask_routes_pdf_table_kb_question_to_hybrid_scope():
    original = app.state.conversation_file_service
    original_persistence = app.state.conversation_persistence_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf"),
            ConversationFileRow(
                file_id=33,
                file_type="excel",
                file_name="cells.xlsx",
                file_meta={"columns": ["开路电压_V"]},
            ),
        ]
    )
    app.state.conversation_persistence_service = _FakeConversationPersistenceService()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/conversations/"):
            return httpx.Response(201, json={"success": True, "data": {"message_id": 1}})
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "请结合知识库比较前两个文件",
                    "requested_mode": "thinking",
                    "conversation_id": 101,
                    "pdf_context": {"all_available_ids": [11, 33]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["body"]["route"] == "hybrid_qa"
    assert captured["body"]["source_scope"] == "pdf+table+kb"
    assert captured["body"]["kb_enabled"] is True
    assert captured["body"]["selected_file_ids"] == [11, 33]


def test_mode_ask_with_public_http_provider_forwards_auth_and_trace():
    original = app.state.conversation_file_service
    original_persistence = app.state.conversation_persistence_service
    file_provider = PublicHttpConversationFileProvider(base_url="http://127.0.0.1:8008")
    service = ConversationFileService(provider=file_provider)
    app.state.conversation_file_service = service
    app.state.conversation_persistence_service = _FakeConversationPersistenceService()
    captured = {"metadata_headers": None, "ask_headers": None, "body": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/conversations/101/files":
            captured["metadata_headers"] = dict(request.headers)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "files": [
                            {
                                "file_id": 33,
                                "file_type": "excel",
                                "file_name": "cells.xlsx",
                                "file_meta": {"columns": ["开路电压_V"]},
                            }
                        ]
                    },
                },
            )
        if request.url.path == "/api/fast/ask":
            captured["ask_headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        if request.url.path.startswith("/api/v1/conversations/"):
            return httpx.Response(201, json={"success": True, "data": {"message_id": 1}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    app.state.proxy_service.set_transport(transport)
    app.state.conversation_file_service.set_transport(transport)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            headers={
                "Authorization": "Bearer test-token",
                TRACE_ID_HEADER: "trace-route-1",
            },
            json={
                "question": "开路电压_V 的分布是什么？",
                "requested_mode": "thinking",
                "conversation_id": 101,
                "pdf_context": {"selected_ids": [33]},
            },
        )
    finally:
        app.state.proxy_service.set_transport(None)
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["metadata_headers"]["authorization"] == "Bearer test-token"
    assert captured["metadata_headers"][TRACE_ID_HEADER.lower()] == "trace-route-1"
    assert captured["ask_headers"]["authorization"] == "Bearer test-token"
    assert captured["ask_headers"][TRACE_ID_HEADER.lower()] == "trace-route-1"
    assert captured["body"]["actual_mode"] == "fast"
    assert captured["body"]["route"] == "tabular_qa"


def test_v1_ask_alias_is_removed():
    called = {"upstream": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["upstream"] = True
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/v1/ask",
            json={"question": "plain qa", "requested_mode": "thinking"},
        )

    assert response.status_code == 404
    assert called["upstream"] is False


def test_ask_alias_is_removed():
    called = {"upstream": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["upstream"] = True
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/ask",
            json={
                "question": "plain qa",
                "requested_mode": "thinking",
            },
        )

    assert response.status_code == 404
    assert called["upstream"] is False


def test_mode_ask_returns_503_when_conversation_file_provider_fails():
    original = app.state.conversation_file_service
    file_provider = PublicHttpConversationFileProvider(base_url="http://127.0.0.1:8008")
    service = ConversationFileService(provider=file_provider)
    app.state.conversation_file_service = service

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/conversations/101/files":
            return httpx.Response(503, json={"success": False})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    app.state.conversation_file_service.set_transport(transport)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={
                "question": "开路电压_V 的分布是什么？",
                "requested_mode": "thinking",
                "conversation_id": 101,
                "pdf_context": {"selected_ids": [33]},
            },
        )
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 503
    assert response.json()["code"] == "CONVERSATION_FILE_PROVIDER_UNAVAILABLE"


def test_mode_ask_stream_emits_provider_error_when_conversation_file_provider_fails():
    original = app.state.conversation_file_service
    file_provider = PublicHttpConversationFileProvider(base_url="http://127.0.0.1:8008")
    service = ConversationFileService(provider=file_provider)
    app.state.conversation_file_service = service

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/conversations/101/files":
            return httpx.Response(503, json={"success": False})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    app.state.conversation_file_service.set_transport(transport)
    try:
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={
                "question": "开路电压_V 的分布是什么？",
                "requested_mode": "thinking",
                "conversation_id": 101,
                "pdf_context": {"selected_ids": [33]},
            },
        ) as response:
            body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    assert b'CONVERSATION_FILE_PROVIDER_UNAVAILABLE' in body


def test_mode_ask_stream_converts_upstream_http_error_to_sse_error():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/thinking/ask_stream")
        return httpx.Response(500, json={"detail": "backend exploded"})

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking"},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"code":"UPSTREAM_ERROR"' in body
    assert b'"backend":"thinking"' in body


def test_mode_ask_forwards_canonical_file_aware_fields():
    original = app.state.conversation_file_service
    original_persistence = app.state.conversation_persistence_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="solid-state-review.pdf",
            )
        ]
    )
    app.state.conversation_persistence_service = _FakeConversationPersistenceService()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "请结合知识库总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 101,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["body"]["route"] == "hybrid_qa"
    assert captured["body"]["source_scope"] == "pdf+kb"
    assert captured["body"]["kb_enabled"] is True
    assert captured["body"]["selected_file_ids"] == [11]
    assert captured["body"]["primary_file_id"] == 11
    assert captured["body"]["file_selection"] == {
        "strategy": "selected_single",
        "selected_file_ids": [11],
        "turn_mode": "mixed",
        "source_scope": "pdf+kb",
        "kb_enabled": True,
    }



def test_mode_ask_short_circuits_clarification_in_gateway():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={
                "question": "请继续总结这篇文献",
                "requested_mode": "thinking",
                "pdf_context": {"selected_ids": [11, 22]},
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "FILE_SELECTION_CLARIFICATION_REQUIRED"
    assert calls == []

def test_mode_ask_forwards_chat_history_without_pdf_context():
    captured = {}
    chat_history = [
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={
                "question": "plain qa",
                "requested_mode": "thinking",
                "conversation_id": 7,
                "chat_history": chat_history,
                "pdf_context": {"selected_ids": [11], "last_focus_ids": [11]},
            },
        )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/thinking/ask")
    assert captured["body"]["chat_history"] == chat_history
    assert "pdf_context" not in captured["body"]
    assert captured["body"]["route"] == "kb_qa"
    assert captured["body"]["actual_mode"] == "thinking"


def test_mode_ask_stream_short_circuits_clarification_in_gateway():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={
                "question": "请继续总结这篇文献",
                "requested_mode": "thinking",
                "pdf_context": {"selected_ids": [11, 22]},
            },
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert calls == []
    assert b'"type":"metadata"' in body
    assert b'FILE_SELECTION_CLARIFICATION_REQUIRED' in body
    assert response.headers["content-type"].startswith("text/event-stream")

