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


def test_mode_ask_routes_file_question_to_fast_backend():
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
                "question": "请总结这篇文献",
                "requested_mode": "thinking",
                "pdf_context": {"selected_ids": [11]},
            },
        )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/fast/ask")
    assert captured["body"]["actual_mode"] == "fast"
    assert captured["body"]["route"] == "pdf_qa"
    assert response.headers["x-gateway-backend"] == "fast"


def test_v1_ask_stream_alias_routes_to_requested_backend():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/thinking/ask_stream")
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"metadata","query_mode":"fast"}\n\n'
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

    assert response.status_code == 200
    assert b'"type":"content"' in body
    assert response.headers["x-gateway-backend"] == "thinking"


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


def test_mode_ask_stream_persists_user_and_assistant_messages():
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
    assert fake_persistence.user_calls[0]["conversation_id"] == 42
    assert fake_persistence.user_calls[0]["content"] == "plain qa"
    assert fake_persistence.assistant_calls[0]["conversation_id"] == 42
    assert fake_persistence.assistant_calls[0]["summary"].done_seen is True
    assert fake_persistence.assistant_calls[0]["summary"].reference_links[0]["doi"] == "10.1/demo"


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
    assert captured["body"]["execution_files"][0]["file_type"] == "excel"


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


def test_v1_ask_alias_accepts_legacy_mode_field():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/v1/ask",
            json={"question": "plain qa", "mode": "thinking"},
        )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/thinking/ask")
    assert captured["body"]["requested_mode"] == "thinking"
    assert response.headers["x-gateway-backend"] == "thinking"


def test_v1_ask_alias_accepts_legacy_mode_field():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/v1/ask",
            json={
                "question": "plain qa",
                "mode": "thinking",
            },
        )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/thinking/ask")
    assert captured["body"]["requested_mode"] == "thinking"
    assert captured["body"]["actual_mode"] == "thinking"
    assert response.headers["x-gateway-backend"] == "thinking"


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
