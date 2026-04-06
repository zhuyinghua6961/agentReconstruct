import json
import threading
from dataclasses import replace
import anyio

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app
from app.core.trace import TRACE_ID_HEADER
from app.models.files import ConversationFileRow
from app.providers.conversation_files.public_http import PublicHttpConversationFileProvider
from app.routers.qa import _stream_with_quota
from app.services.conversation_files import ConversationFileService


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


class _FailingAsyncStream(httpx.AsyncByteStream):
    def __init__(self, *, first_chunk: bytes, exc: Exception) -> None:
        self._first_chunk = first_chunk
        self._exc = exc

    async def __aiter__(self):
        yield self._first_chunk
        raise self._exc

    async def aclose(self) -> None:
        return None


class _RecordingQuotaProxy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def finalize(self, *, request, grant_id, success):
        self.calls.append({"request": request, "grant_id": grant_id, "success": success})
        return type(
            "_FinalizeResult",
            (),
            {"success": True, "status_code": 200, "payload": {"success": True, "data": {"counted": success, "idempotent": False}}},
        )()


class _SimpleStreamingHandle:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def body_iter(self):
        for chunk in self._chunks:
            yield chunk


def _json_request_body(request: httpx.Request) -> dict:
    raw = request.content.decode("utf-8") if request.content else ""
    return json.loads(raw) if raw else {}


def test_mode_ask_calls_internal_quota_precheck_and_finalize_for_plain_question(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request), dict(request.headers)))
        if request.url.path == "/internal/quota/grants/precheck":
            payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-sync-1", "quota_type": payload["quota_type"], "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-sync-1/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-1", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={
                "question": "plain qa",
                "requested_mode": "thinking",
                "conversation_id": 7,
                "user_id": 42,
            },
        )

    assert response.status_code == 200
    assert [item[0] for item in calls] == [
        "/internal/quota/grants/precheck",
        "/api/thinking/ask",
        "/internal/quota/grants/grant-sync-1/finalize",
    ]
    assert calls[0][1]["quota_type"] == "ask_query"
    assert calls[0][2]["x-internal-service-name"] == "gateway"
    assert calls[2][1]["success"] is True


def test_mode_ask_routes_file_question_to_file_qa_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-file-1", "quota_type": payload["quota_type"], "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-file-1/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-file-1", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/fast/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "请总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 8,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    assert calls[0][0] == "/internal/quota/grants/precheck"
    assert calls[0][1]["quota_type"] == "file_qa"
    assert calls[1][0] == "/api/fast/ask"
    assert calls[2][0] == "/internal/quota/grants/grant-file-1/finalize"
    assert calls[2][1]["success"] is True


def test_mode_ask_stream_counts_quota_only_after_done_event(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-stream-1", "quota_type": payload["quota_type"], "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-stream-1/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-1", "counted": payload["success"], "idempotent": False}})
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

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 9, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert [item[0] for item in calls] == [
        "/internal/quota/grants/precheck",
        "/api/thinking/ask_stream",
        "/internal/quota/grants/grant-stream-1/finalize",
    ]
    assert calls[2][1]["success"] is True


def test_mode_ask_stream_appends_quota_warning_when_finalize_fails(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-stream-warn", "quota_type": "ask_query", "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-stream-warn/finalize":
            return httpx.Response(503, json={"success": False, "code": "DB_UNAVAILABLE", "error": "db_unavailable"})
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

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 10, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert b'"quota"' in body
    assert b'"warning"' in body


def test_mode_patent_inflight_request_does_not_block_fast_quota_flow(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    patent_upstream_started = threading.Event()
    release_patent_upstream = threading.Event()
    fast_completed = threading.Event()
    errors: dict[str, Exception] = {}
    responses: dict[str, object] = {}
    precheck_index = {"value": 0}
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _json_request_body(request)
        calls.append((request.url.path, payload))
        if request.url.path == "/internal/quota/grants/precheck":
            precheck_index["value"] += 1
            grant_id = "grant-patent-inflight" if precheck_index["value"] == 1 else "grant-fast-followup"
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": grant_id, "quota_type": payload["quota_type"], "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-patent-inflight/finalize":
            finalize_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-inflight", "counted": finalize_payload["success"], "idempotent": False}})
        if request.url.path == "/internal/quota/grants/grant-fast-followup/finalize":
            finalize_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-fast-followup", "counted": finalize_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask":
            patent_upstream_started.set()
            assert release_patent_upstream.wait(timeout=2.0)
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "patent ok"}})
        if request.url.path == "/api/fast/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "fast ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    def _run_patent(client: TestClient) -> None:
        try:
            responses["patent"] = client.post(
                "/api/patent/ask",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 210,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
        except Exception as exc:
            errors["patent"] = exc

    def _run_fast(client: TestClient) -> None:
        try:
            responses["fast"] = client.post(
                "/api/fast/ask",
                json={
                    "question": "plain fast question",
                    "requested_mode": "fast",
                    "conversation_id": 211,
                    "user_id": 42,
                },
            )
            fast_completed.set()
        except Exception as exc:
            errors["fast"] = exc

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            patent_worker = threading.Thread(target=_run_patent, args=(client,), daemon=True)
            fast_worker = threading.Thread(target=_run_fast, args=(client,), daemon=True)
            patent_worker.start()
            assert patent_upstream_started.wait(timeout=1.0)
            fast_worker.start()
            assert fast_completed.wait(timeout=1.0)
            release_patent_upstream.set()
            patent_worker.join(timeout=2.0)
            fast_worker.join(timeout=2.0)
    finally:
        app.state.conversation_file_service = original_files

    assert errors == {}
    assert patent_worker.is_alive() is False
    assert fast_worker.is_alive() is False
    assert responses["patent"].status_code == 200
    assert responses["fast"].status_code == 200
    assert responses["patent"].json()["success"] is True
    assert responses["fast"].json()["success"] is True
    precheck_calls = [item for item in calls if item[0] == "/internal/quota/grants/precheck"]
    assert len(precheck_calls) == 2
    assert precheck_calls[0][1]["quota_type"] == "ask_query"
    assert precheck_calls[1][1]["quota_type"] == "ask_query"


def test_mode_ask_keeps_success_response_when_finalize_returns_not_found(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-not-found", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-sync-not-found/finalize":
            return httpx.Response(404, json={"success": False, "code": "NOT_FOUND", "error": "grant_not_found"})
        if request.url.path == "/api/thinking/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 212, "user_id": 42},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["quota"]["warning"]["code"] == "NOT_FOUND"
    assert payload["quota"]["warning"]["error"] == "grant_not_found"


def test_mode_ask_stream_appends_not_found_quota_warning_when_finalize_returns_not_found(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-stream-not-found", "quota_type": "ask_query", "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-stream-not-found/finalize":
            return httpx.Response(404, json={"success": False, "code": "NOT_FOUND", "error": "grant_not_found"})
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

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 213, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert b'"quota"' in body
    assert b'"warning"' in body
    assert b'"code":"NOT_FOUND"' in body
    assert b'"error":"grant_not_found"' in body


@pytest.mark.parametrize(
    ("label", "request_path", "payload", "rows", "expected_route", "expected_quota_type"),
    [
        (
            "kb_sync",
            "/api/patent/ask",
            {
                "question": "磷酸铁锂电压范围是多少？",
                "requested_mode": "patent",
                "conversation_id": 11,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "kb_qa",
            "ask_query",
        ),
        (
            "pdf_sync",
            "/api/patent/ask",
            {
                "question": "请总结这篇文献",
                "requested_mode": "patent",
                "conversation_id": 12,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "pdf_qa",
            "file_qa",
        ),
        (
            "tabular_sync",
            "/api/patent/ask",
            {
                "question": "请总结这个表格",
                "requested_mode": "patent",
                "conversation_id": 13,
                "user_id": 42,
                "pdf_context": {"selected_ids": [21]},
            },
            [ConversationFileRow(file_id=21, file_type="csv", file_name="assignee-table.csv")],
            "tabular_qa",
            "file_qa",
        ),
        (
            "hybrid_sync",
            "/api/patent/ask",
            {
                "question": "请结合知识库总结这篇文献",
                "requested_mode": "patent",
                "conversation_id": 14,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "hybrid_qa",
            "file_qa",
        ),
        (
            "kb_stream",
            "/api/patent/ask_stream",
            {
                "question": "磷酸铁锂电压范围是多少？",
                "requested_mode": "patent",
                "conversation_id": 15,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "kb_qa",
            "ask_query",
        ),
        (
            "pdf_stream",
            "/api/patent/ask_stream",
            {
                "question": "请总结这篇文献",
                "requested_mode": "patent",
                "conversation_id": 16,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "pdf_qa",
            "file_qa",
        ),
        (
            "tabular_stream",
            "/api/patent/ask_stream",
            {
                "question": "请总结这个表格",
                "requested_mode": "patent",
                "conversation_id": 17,
                "user_id": 42,
                "pdf_context": {"selected_ids": [21]},
            },
            [ConversationFileRow(file_id=21, file_type="csv", file_name="assignee-table.csv")],
            "tabular_qa",
            "file_qa",
        ),
        (
            "hybrid_stream",
            "/api/patent/ask_stream",
            {
                "question": "请结合知识库总结这篇文献",
                "requested_mode": "patent",
                "conversation_id": 18,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "hybrid_qa",
            "file_qa",
        ),
    ],
)
def test_mode_patent_routes_map_to_canonical_quota_buckets(
    monkeypatch, label, request_path, payload, rows, expected_route, expected_quota_type
):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(rows)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            request_payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": f"grant-{label}", "quota_type": request_payload["quota_type"], "noop": False}},
            )
        if request.url.path == f"/internal/quota/grants/grant-{label}/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": f"grant-{label}", "counted": request_payload["success"], "idempotent": False}},
            )
        if request.url.path == "/api/patent/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"patent"}\n\n'
                    b'data: {"type":"done","final_answer":"ok"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            if request_path.endswith("ask_stream"):
                with client.stream("POST", request_path, json=payload) as response:
                    body = b"".join(response.iter_bytes())
            else:
                response = client.post(request_path, json=payload)
                body = b""
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    if body:
        assert b'"type":"done"' in body
    assert calls[0][0] == "/internal/quota/grants/precheck"
    assert calls[0][1]["quota_type"] == expected_quota_type
    assert calls[1][0] == request_path
    assert calls[1][1]["route"] == expected_route
    assert calls[2][0] == f"/internal/quota/grants/grant-{label}/finalize"
    assert calls[2][1]["success"] is True


@pytest.mark.parametrize("request_path", ["/api/patent/ask", "/api/patent/ask_stream"])
@pytest.mark.parametrize(
    "user_id",
    [None, "", 0, -1, "abc"],
)
def test_mode_patent_skips_quota_calls_for_missing_or_invalid_user_id(monkeypatch, request_path, user_id):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/api/patent/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"patent"}\n\n'
                    b'data: {"type":"done","final_answer":"ok"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    payload = {
        "question": "磷酸铁锂电压范围是多少？",
        "requested_mode": "patent",
        "conversation_id": 19,
        "pdf_context": {"selected_ids": [11]},
    }
    if user_id is not None:
        payload["user_id"] = user_id

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            if request_path.endswith("ask_stream"):
                with client.stream("POST", request_path, json=payload) as response:
                    body = b"".join(response.iter_bytes())
            else:
                response = client.post(request_path, json=payload)
                body = b""
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    if body:
        assert b'"type":"done"' in body
    assert [item[0] for item in calls] == [request_path]


@pytest.mark.parametrize(
    ("label", "payload", "rows", "expected_route", "expected_quota_type"),
    [
        (
            "patent-sync-kb-quota",
            {
                "question": "磷酸铁锂电压范围是多少？",
                "requested_mode": "patent",
                "conversation_id": 21,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "kb_qa",
            "ask_query",
        ),
        (
            "patent-sync-file-quota",
            {
                "question": "请总结这篇文献",
                "requested_mode": "patent",
                "conversation_id": 22,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "pdf_qa",
            "file_qa",
        ),
    ],
)
def test_mode_ask_patent_sync_surfaces_canonical_quota_payload(monkeypatch, label, payload, rows, expected_route, expected_quota_type):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(rows)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            request_payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": label, "quota_type": request_payload["quota_type"], "noop": False}},
            )
        if request.url.path == f"/internal/quota/grants/{label}/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": label, "counted": request_payload["success"], "idempotent": False}},
            )
        if request.url.path == "/api/patent/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post("/api/patent/ask", json=payload)
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload["quota"]["quota_type"] == expected_quota_type
    assert response_payload["quota"]["counted"] is True
    assert calls[1][1]["route"] == expected_route
    assert calls[2][0] == f"/internal/quota/grants/{label}/finalize"
    assert calls[2][1]["success"] is True


@pytest.mark.parametrize(
    ("label", "payload", "rows", "expected_route", "expected_quota_type"),
    [
        (
            "patent-stream-kb-quota",
            {
                "question": "磷酸铁锂电压范围是多少？",
                "requested_mode": "patent",
                "conversation_id": 23,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "kb_qa",
            "ask_query",
        ),
        (
            "patent-stream-file-quota",
            {
                "question": "请总结这篇文献",
                "requested_mode": "patent",
                "conversation_id": 24,
                "user_id": 42,
                "pdf_context": {"selected_ids": [11]},
            },
            [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")],
            "pdf_qa",
            "file_qa",
        ),
    ],
)
def test_mode_ask_patent_stream_done_event_surfaces_canonical_quota_payload(
    monkeypatch, label, payload, rows, expected_route, expected_quota_type
):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(rows)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            request_payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": label, "quota_type": request_payload["quota_type"], "noop": False}},
            )
        if request.url.path == f"/internal/quota/grants/{label}/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": label, "counted": request_payload["success"], "idempotent": False}},
            )
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"patent"}\n\n'
                    + f'data: {{"type":"done","final_answer":"ok","route":"{expected_route}"}}\n\n'.encode("utf-8")
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream("POST", "/api/patent/ask_stream", json=payload) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert f'"route":"{expected_route}"'.encode("utf-8") in body
    assert f'"quota_type":"{expected_quota_type}"'.encode("utf-8") in body
    assert b'"counted":true' in body
    assert calls[1][1]["route"] == expected_route
    assert calls[2][0] == f"/internal/quota/grants/{label}/finalize"
    assert calls[2][1]["success"] is True


def test_mode_ask_patent_aborts_quota_when_upstream_payload_is_unsuccessful(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-sync-fail", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-sync-fail/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-sync-fail", "counted": request_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask":
            return httpx.Response(200, json={"success": False, "error": "patent_failed"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 25,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert calls[-1][0] == "/internal/quota/grants/grant-patent-sync-fail/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_patent_aborts_quota_when_upstream_payload_has_error_field(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-sync-error", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-sync-error/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-sync-error", "counted": request_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask":
            return httpx.Response(200, json={"success": True, "error": "patent_failed"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 251,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert response.json()["error"] == "patent_failed"
    assert calls[-1][0] == "/internal/quota/grants/grant-patent-sync-error/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_patent_stream_aborts_quota_when_done_event_never_arrives(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-abort", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-stream-abort/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-abort", "counted": request_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"patent"}\n\n'
                    b'data: {"type":"content","content":"partial"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/patent/ask_stream",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 26,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert b'"type":"done"' not in body
    assert calls[-1][0] == "/internal/quota/grants/grant-patent-stream-abort/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_patent_stream_aborts_quota_when_upstream_returns_http_error(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-http-error", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-stream-http-error/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-http-error", "counted": request_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(500, json={"detail": "backend exploded"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/patent/ask_stream",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 261,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert b'"code":"UPSTREAM_ERROR"' in body
    assert calls[-1][0] == "/internal/quota/grants/grant-patent-stream-http-error/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_patent_stream_aborts_quota_when_upstream_emits_error_event(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-sse-error", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-stream-sse-error/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-sse-error", "counted": request_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"patent"}\n\n'
                    b'data: {"type":"error","error":"patent_failed","message":"patent failed"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/patent/ask_stream",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 262,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert b'"type":"error"' in body
    assert b'"type":"done"' not in body
    assert calls[-1][0] == "/internal/quota/grants/grant-patent-stream-sse-error/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_patent_stream_does_not_count_when_error_event_precedes_done(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-error-then-done", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-stream-error-then-done/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-error-then-done", "counted": request_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"patent"}\n\n'
                    b'data: {"type":"error","error":"patent_failed","message":"patent failed"}\n\n'
                    b'data: {"type":"done","final_answer":"partial"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/patent/ask_stream",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 263,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert b'"type":"error"' in body
    assert b'"type":"done"' in body
    assert calls[-1][0] == "/internal/quota/grants/grant-patent-stream-error-then-done/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_patent_keeps_success_response_when_finalize_fails(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-sync-warn", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-sync-warn/finalize":
            return httpx.Response(503, json={"success": False, "code": "DB_UNAVAILABLE", "error": "db_unavailable"})
        if request.url.path == "/api/patent/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 27,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["quota"]["quota_type"] == "ask_query"
    assert payload["quota"]["warning"]["code"] == "DB_UNAVAILABLE"


def test_mode_ask_patent_stream_appends_quota_warning_when_finalize_fails(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-stream-warn", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-stream-warn/finalize":
            return httpx.Response(503, json={"success": False, "code": "DB_UNAVAILABLE", "error": "db_unavailable"})
        if request.url.path == "/api/patent/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"patent"}\n\n'
                    b'data: {"type":"done","final_answer":"ok"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/patent/ask_stream",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 28,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original_files

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert b'"quota"' in body
    assert b'"warning"' in body
    assert b'"quota_type":"ask_query"' in body


def test_mode_ask_patent_kb_route_ignores_disabled_file_gate_and_still_counts_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original_files = app.state.conversation_file_service
    original_settings = app.state.settings
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    app.state.settings = replace(original_settings, patent_file_routes_enabled=False)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-kb-gate-off", "quota_type": request_payload["quota_type"], "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-patent-kb-gate-off/finalize":
            request_payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-patent-kb-gate-off", "counted": request_payload["success"], "idempotent": False}})
        if request.url.path == "/api/patent/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 29,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original_files
        app.state.settings = original_settings

    assert response.status_code == 200
    assert calls[0][0] == "/internal/quota/grants/precheck"
    assert calls[0][1]["quota_type"] == "ask_query"
    assert calls[1][0] == "/api/patent/ask"
    assert calls[1][1]["route"] == "kb_qa"
    assert calls[2][0] == "/internal/quota/grants/grant-patent-kb-gate-off/finalize"
    assert calls[2][1]["success"] is True


def test_mode_ask_clarification_skips_quota_calls(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(file_id=11, file_type="pdf", file_name="solid-state-review.pdf"),
            ConversationFileRow(file_id=22, file_type="pdf", file_name="battery-paper.pdf"),
        ]
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "请继续总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 12,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11, 22]},
                },
            )
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 400
    assert response.json()["code"] == "FILE_SELECTION_CLARIFICATION_REQUIRED"
    assert calls == []


def test_mode_ask_returns_json_quota_error_surface_on_precheck_failure(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(429, json={"success": False, "code": "QUOTA_EXCEEDED", "error": "quota_exceeded"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 121, "user_id": 42},
        )

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "QUOTA_EXCEEDED"


def test_mode_ask_stream_returns_sse_quota_error_surface_on_precheck_failure(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(
                429,
                json={
                    "success": False,
                    "code": "QUOTA_EXCEEDED",
                    "error": "quota_exceeded",
                    "message": "quota exceeded",
                    "data": {"quota_type": "ask_query", "remaining": 0, "limit": 20},
                },
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 122, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert body.index(b'"type":"metadata"') < body.index(b'"type":"error"')
    assert b'"code":"QUOTA_EXCEEDED"' in body
    assert b'"data":{"quota_type":"ask_query","remaining":0,"limit":20}' in body


def test_mode_ask_stream_returns_sse_system_quota_error_with_data_on_precheck_failure(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(
                503,
                json={
                    "success": False,
                    "code": "QUOTA_CONFIG_MISSING",
                    "error": "quota_config_missing",
                    "message": "quota config missing",
                    "data": {"quota_type": "ask_query", "config_missing": True},
                },
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 123, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert body.index(b'"type":"metadata"') < body.index(b'"type":"error"')
    assert b'"code":"QUOTA_CONFIG_MISSING"' in body
    assert b'"data":{"quota_type":"ask_query","config_missing":true}' in body


def test_mode_ask_stream_quota_error_payload_is_not_double_escaped(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(
                429,
                json={
                    "success": False,
                    "code": 'QUOTA_"EXCEEDED"',
                    "error": 'quota_"exceeded"\\path',
                    "message": 'quota "limit" hit at C:\\quota\\path',
                    "data": {"quota_type": "ask_query"},
                },
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 124, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b'quota \\"limit\\" hit at C:\\\\quota\\\\path' in body
    assert b'\\\\\\\\quota' not in body
    assert b'quota_\\"exceeded\\"\\\\path' in body


def test_mode_ask_aborts_quota_when_upstream_payload_is_unsuccessful(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-fail", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-sync-fail/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-fail", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask":
            return httpx.Response(200, json={"success": False, "error": "llm_failed"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 13, "user_id": 42},
        )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert calls[-1][0] == "/internal/quota/grants/grant-sync-fail/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_keeps_success_response_when_finalize_fails(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-warn", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-sync-warn/finalize":
            return httpx.Response(503, json={"success": False, "code": "DB_UNAVAILABLE", "error": "db_unavailable"})
        if request.url.path == "/api/thinking/ask":
            return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 14, "user_id": 42},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["quota"]["warning"]["code"] == "DB_UNAVAILABLE"


def test_mode_ask_aborts_quota_when_upstream_status_is_non_2xx(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-500", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-sync-500/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-500", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask":
            return httpx.Response(500, json={"detail": "backend exploded"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 141, "user_id": 42},
        )

    assert response.status_code == 500
    assert calls[-1][0] == "/internal/quota/grants/grant-sync-500/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_aborts_quota_when_upstream_payload_has_error_field(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-error", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-sync-error/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-sync-error", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask":
            return httpx.Response(200, json={"success": True, "error": "llm_failed"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/thinking/ask",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 142, "user_id": 42},
        )

    assert response.status_code == 200
    assert response.json()["error"] == "llm_failed"
    assert calls[-1][0] == "/internal/quota/grants/grant-sync-error/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_stream_aborts_quota_when_done_event_never_arrives(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-abort", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-stream-abort/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-abort", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"thinking"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 15, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"type":"done"' not in body
    assert calls[-1][0] == "/internal/quota/grants/grant-stream-abort/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_stream_routes_file_question_to_file_qa_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            payload = _json_request_body(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-stream-file", "quota_type": payload["quota_type"], "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-stream-file/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-file", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/fast/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'data: {"type":"metadata","query_mode":"fast","route":"pdf_qa"}\n\n'
                    b'data: {"type":"content","content":"hello"}\n\n'
                    b'data: {"type":"done","final_answer":"hello","route":"pdf_qa"}\n\n'
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
                    "question": "请总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 16,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert calls[0][0] == "/internal/quota/grants/precheck"
    assert calls[0][1]["quota_type"] == "file_qa"
    assert calls[1][0] == "/api/fast/ask_stream"
    assert calls[2][0] == "/internal/quota/grants/grant-stream-file/finalize"
    assert calls[2][1]["success"] is True


def test_mode_ask_stream_preserves_done_event_metadata_when_quota_is_appended(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-metadata", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-stream-metadata/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-metadata", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask_stream":
            return httpx.Response(
                200,
                content=(
                    b'event: done\r\n'
                    b'id: final-1\r\n'
                    b'data: {"type":"done","final_answer":"hello"}\r\n\r\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 18, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'event: done' in body
    assert b'id: final-1' in body
    assert b'"quota"' in body


def test_mode_ask_stream_aborts_quota_when_upstream_returns_http_error(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-http-error", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-stream-http-error/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-http-error", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask_stream":
            return httpx.Response(500, json={"detail": "backend exploded"})
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 17, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"code":"UPSTREAM_ERROR"' in body
    assert calls[-1][0] == "/internal/quota/grants/grant-stream-http-error/finalize"
    assert calls[-1][1]["success"] is False


def test_mode_ask_stream_aborts_quota_when_midstream_timeout_occurs(monkeypatch):
    monkeypatch.setenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "authority-test-token")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, _json_request_body(request)))
        if request.url.path == "/internal/quota/grants/precheck":
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-timeout", "quota_type": "ask_query", "noop": False}})
        if request.url.path == "/internal/quota/grants/grant-stream-timeout/finalize":
            payload = _json_request_body(request)
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-stream-timeout", "counted": payload["success"], "idempotent": False}})
        if request.url.path == "/api/thinking/ask_stream":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_FailingAsyncStream(
                    first_chunk=b'data: {"type":"content","content":"partial"}\n\n',
                    exc=httpx.ReadTimeout("stream timeout", request=request),
                ),
            )
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    with _TransportGuard(handler):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/thinking/ask_stream",
            json={"question": "plain qa", "requested_mode": "thinking", "conversation_id": 19, "user_id": 42},
        ) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"content":"partial"' in body
    assert b'"code":"UPSTREAM_STREAM_UNAVAILABLE"' in body
    assert calls[-1][0] == "/internal/quota/grants/grant-stream-timeout/finalize"
    assert calls[-1][1]["success"] is False


def test_stream_with_quota_aborts_grant_when_client_closes_stream_early():
    async def _run():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/thinking/ask_stream",
            "headers": [],
            "app": app,
        }
        quota_proxy = _RecordingQuotaProxy()
        handle = _SimpleStreamingHandle(
            [
                b'data: {"type":"metadata","query_mode":"thinking"}\n\n',
                b'data: {"type":"content","content":"partial"}\n\n',
                b'data: {"type":"done","final_answer":"partial"}\n\n',
            ]
        )
        stream = _stream_with_quota(
            handle=handle,
            request=Request(scope),
            quota_proxy=quota_proxy,
            grant_id="grant-stream-close",
            quota_type="ask_query",
            trace_id="trace-close",
            backend="thinking",
        )
        first_chunk = await anext(stream)
        assert b'"type":"metadata"' in first_chunk
        await stream.aclose()
        assert len(quota_proxy.calls) == 1
        assert quota_proxy.calls[0]["grant_id"] == "grant-stream-close"
        assert quota_proxy.calls[0]["success"] is False

    anyio.run(_run)


def test_stream_with_quota_counts_success_when_client_closes_after_done_is_seen():
    async def _run():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/thinking/ask_stream",
            "headers": [],
            "app": app,
        }
        quota_proxy = _RecordingQuotaProxy()
        handle = _SimpleStreamingHandle(
            [
                (
                    b'data: {"type":"metadata","query_mode":"thinking"}\n\n'
                    b'data: {"type":"done","final_answer":"partial"}\n\n'
                ),
            ]
        )
        stream = _stream_with_quota(
            handle=handle,
            request=Request(scope),
            quota_proxy=quota_proxy,
            grant_id="grant-stream-close-after-done",
            quota_type="ask_query",
            trace_id="trace-close-after-done",
            backend="thinking",
        )
        first_chunk = await anext(stream)
        assert b'"type":"metadata"' in first_chunk
        await stream.aclose()
        assert len(quota_proxy.calls) == 1
        assert quota_proxy.calls[0]["grant_id"] == "grant-stream-close-after-done"
        assert quota_proxy.calls[0]["success"] is True

    anyio.run(_run)


def test_stream_with_quota_preserves_cancel_error_envelope():
    async def _run():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/thinking/ask_stream",
            "headers": [],
            "app": app,
        }
        quota_proxy = _RecordingQuotaProxy()
        handle = _SimpleStreamingHandle(
            [
                (
                    b'data: {"type":"metadata","query_mode":"thinking"}\n\n'
                    b'data: {"type":"error","code":"ASK_CANCELLED","error":"cancelled","message":"cancelled","retriable":false,"trace_id":"trace-cancel"}\n\n'
                ),
            ]
        )
        stream = _stream_with_quota(
            handle=handle,
            request=Request(scope),
            quota_proxy=quota_proxy,
            grant_id="grant-stream-cancel",
            quota_type="ask_query",
            trace_id="trace-cancel",
            backend="thinking",
        )
        chunks = [chunk async for chunk in stream]
        body = b"".join(chunks)
        assert body.index(b'"type":"metadata"') < body.index(b'"type":"error"')
        assert b'"code":"ASK_CANCELLED"' in body
        assert b'"error":"cancelled"' in body
        assert b'"type":"error"' in body
        assert b'"type":"canceled"' not in body
        assert len(quota_proxy.calls) == 1
        assert quota_proxy.calls[0]["grant_id"] == "grant-stream-cancel"
        assert quota_proxy.calls[0]["success"] is False

    anyio.run(_run)


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
    assert captured["body"]["turn_mode"] == "kb_only"
    assert captured["body"]["source_scope"] == "kb"
    assert captured["body"]["needs_clarification"] is False
    assert captured["body"]["strategy"] == "none"
    assert captured["body"]["execution_files"] == []
    assert captured["body"]["classifier_used"] is False
    assert captured["body"]["route_confidence"] == 1.0
    assert "NO_FILE_INTENT" in captured["body"]["route_reasons"]
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
    original = app.state.conversation_file_service
    original_persistence = app.state.conversation_persistence_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]
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
                    "question": "请总结这篇文献",
                    "requested_mode": "thinking",
                    "conversation_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["url"].endswith("/api/fast/ask")
    assert captured["body"]["actual_mode"] == "fast"
    assert captured["body"]["route"] == "pdf_qa"
    assert captured["body"]["source_scope"] == "pdf"
    assert captured["body"]["turn_mode"] == "file_only"
    assert captured["body"]["needs_clarification"] is False
    assert captured["body"]["kb_enabled"] is False
    assert captured["body"]["selected_file_ids"] == [11]
    assert captured["body"]["strategy"] == "explicit_selection"
    assert captured["body"]["classifier_used"] is False
    assert captured["body"]["route_confidence"] == 1.0
    assert "EXPLICIT_SELECTED_FILES" in captured["body"]["route_reasons"]
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "fast"


def test_mode_ask_keeps_requested_backend_for_plain_question_with_selected_scope():
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
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "thinking",
                    "conversation_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.conversation_persistence_service = original_persistence

    assert response.status_code == 200
    assert captured["url"].endswith("/api/thinking/ask")
    assert captured["body"]["actual_mode"] == "thinking"
    assert captured["body"]["route"] == "kb_qa"
    assert captured["body"]["source_scope"] == "kb"
    assert captured["body"]["selected_file_ids"] == []
    assert captured["body"]["file_selection"] == {}
    assert captured["body"]["strategy"] == "none"
    assert captured["body"]["execution_files"] == []
    assert "NO_FILE_INTENT" in captured["body"]["route_reasons"]
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "thinking"


def test_mode_ask_keeps_patent_backend_for_plain_question_with_selected_scope_without_forwarding_file_fields():
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="battery-paper.pdf",
            )
        ]
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "final_answer": "ok"})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "磷酸铁锂电压范围是多少？",
                    "requested_mode": "patent",
                    "conversation_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    assert captured["url"].endswith("/api/patent/ask")
    assert captured["body"]["actual_mode"] == "patent"
    assert captured["body"]["route"] == "kb_qa"
    assert captured["body"]["source_scope"] == "kb"
    assert captured["body"]["used_files"] == []
    assert captured["body"]["execution_files"] == []
    assert captured["body"]["selected_file_ids"] == []
    assert captured["body"]["file_selection"] == {}
    assert captured["body"]["strategy"] == "none"
    assert response.headers["x-gateway-backend"] == "patent"

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
    assert captured["body"]["turn_mode"] == "mixed"
    assert captured["body"]["strategy"] == "explicit_selection"
    assert captured["body"]["classifier_used"] is False
    assert "EXPLICIT_MIXED_INTENT" in captured["body"]["route_reasons"]
    assert fake_persistence.user_calls == []
    assert fake_persistence.assistant_calls == []
    assert response.headers["x-gateway-backend"] == "fast"


def test_mode_ask_routes_patent_file_question_to_patent_backend():
    original = app.state.conversation_file_service
    original_settings = app.state.settings
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="battery-paper.pdf",
            )
        ]
    )
    app.state.settings = replace(original_settings, patent_file_routes_enabled=True)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "请总结这篇文献",
                    "requested_mode": "patent",
                    "conversation_id": 301,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.settings = original_settings

    assert response.status_code == 200
    assert captured["url"].endswith("/api/patent/ask")
    assert captured["body"]["requested_mode"] == "patent"
    assert captured["body"]["actual_mode"] == "patent"
    assert captured["body"]["route"] == "pdf_qa"
    assert captured["body"]["source_scope"] == "pdf"
    assert "EXPLICIT_SELECTED_FILES" in captured["body"]["route_reasons"]
    assert response.headers["x-gateway-backend"] == "patent"


def test_mode_ask_routes_patent_mixed_question_to_patent_backend():
    original = app.state.conversation_file_service
    original_settings = app.state.settings
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="battery-paper.pdf",
            )
        ]
    )
    app.state.settings = replace(original_settings, patent_file_routes_enabled=True)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "请结合知识库总结这篇文献",
                    "requested_mode": "patent",
                    "conversation_id": 302,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.settings = original_settings

    assert response.status_code == 200
    assert captured["url"].endswith("/api/patent/ask")
    assert captured["body"]["requested_mode"] == "patent"
    assert captured["body"]["actual_mode"] == "patent"
    assert captured["body"]["route"] == "hybrid_qa"
    assert captured["body"]["source_scope"] == "pdf+kb"
    assert captured["body"]["turn_mode"] == "mixed"
    assert captured["body"]["kb_enabled"] is True
    assert "EXPLICIT_MIXED_INTENT" in captured["body"]["route_reasons"]
    assert response.headers["x-gateway-backend"] == "patent"


def test_mode_ask_patent_file_route_is_open_by_default():
    original = app.state.conversation_file_service
    original_settings = app.state.settings
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="battery-paper.pdf",
            )
        ]
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "请总结这篇文献",
                    "requested_mode": "patent",
                    "conversation_id": 301,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original
        app.state.settings = original_settings

    assert response.status_code == 200
    assert captured["url"].endswith("/api/patent/ask")
    assert captured["body"]["requested_mode"] == "patent"
    assert captured["body"]["actual_mode"] == "patent"
    assert captured["body"]["route"] == "pdf_qa"
    assert captured["body"]["source_scope"] == "pdf"
    assert response.headers["x-gateway-backend"] == "patent"


def test_mode_ask_patent_file_route_returns_gated_error_when_disabled():
    original_files = app.state.conversation_file_service
    original_settings = app.state.settings
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=11,
                file_type="pdf",
                file_name="battery-paper.pdf",
            )
        ]
    )
    app.state.settings = replace(original_settings, patent_file_routes_enabled=False)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/patent/ask",
                json={
                    "question": "请总结这篇文献",
                    "requested_mode": "patent",
                    "conversation_id": 301,
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            )
    finally:
        app.state.conversation_file_service = original_files
        app.state.settings = original_settings

    payload = response.json()
    assert response.status_code == 503
    assert calls == []
    assert payload["success"] is False
    assert payload["code"] == "PATENT_FILE_ROUTE_DISABLED"
    assert payload["retriable"] is False
    assert payload["requested_mode"] == "patent"
    assert payload["actual_mode"] == "patent"
    assert payload["route"] == "pdf_qa"
    assert payload["detail"]["source_scope"] == "pdf"
    assert payload["detail"]["selected_file_ids"] == [11]
    assert response.headers["x-gateway-backend"] == "patent"

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
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]
    )
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

    try:
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
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    assert b'"type":"done"' in body
    assert calls == ["/api/fast/ask_stream"]
    assert response.headers["x-gateway-backend"] == "fast"


def test_mode_ask_stream_patent_file_route_returns_gated_error_when_disabled():
    original_files = app.state.conversation_file_service
    original_settings = app.state.settings
    app.state.conversation_file_service = _ConversationFilesStub(
        [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]
    )
    app.state.settings = replace(original_settings, patent_file_routes_enabled=False)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/patent/ask_stream",
                json={
                    "question": "请总结这篇文献",
                    "requested_mode": "patent",
                    "user_id": 42,
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original_files
        app.state.settings = original_settings

    assert response.status_code == 200
    assert calls == []
    assert b'"type":"metadata"' in body
    assert b'"requested_mode":"patent"' in body
    assert b'"actual_mode":"patent"' in body
    assert b'"route":"pdf_qa"' in body
    assert b'"selected_file_ids":[11]' in body
    assert b'"type":"error"' in body
    assert b'"code":"PATENT_FILE_ROUTE_DISABLED"' in body
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-gateway-backend"] == "patent"


def test_mode_ask_stream_routes_patent_mixed_question_to_patent_backend():
    original = app.state.conversation_file_service
    original_settings = app.state.settings
    app.state.conversation_file_service = _ConversationFilesStub(
        [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]
    )
    app.state.settings = replace(original_settings, patent_file_routes_enabled=True)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert str(request.url).endswith("/api/patent/ask_stream")
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["requested_mode"] == "patent"
        assert payload["actual_mode"] == "patent"
        assert payload["route"] == "hybrid_qa"
        assert payload["source_scope"] == "pdf+kb"
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"metadata","query_mode":"patent","route":"hybrid_qa","source_scope":"pdf+kb"}\n\n'
                b'data: {"type":"content","content":"hello"}\n\n'
                b'data: {"type":"done","final_answer":"hello","route":"hybrid_qa","source_scope":"pdf+kb"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/patent/ask_stream",
                json={
                    "question": "请结合知识库总结这篇文献",
                    "requested_mode": "patent",
                    "pdf_context": {"selected_ids": [11]},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original
        app.state.settings = original_settings

    assert response.status_code == 200
    assert calls == ["/api/patent/ask_stream"]
    assert b'"type":"done"' in body
    assert b'"route":"hybrid_qa"' in body
    assert b'"source_scope":"pdf+kb"' in body
    assert response.headers["x-gateway-backend"] == "patent"


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
    original_files = app.state.conversation_file_service
    original = app.state.conversation_persistence_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]
    )
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
        app.state.conversation_file_service = original_files
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
    assert captured["body"]["execution_files"][0]["file_id"] == 11
    assert captured["body"]["strategy"] == "explicit_selection"
    assert captured["body"]["primary_file_id"] == 11
    assert captured["body"]["route_confidence"] == 1.0
    assert captured["body"]["classifier_used"] is False
    assert captured["body"]["file_selection"] == {
        "strategy": "explicit_selection",
        "selected_file_ids": [11],
        "turn_mode": "mixed",
        "source_scope": "pdf+kb",
        "kb_enabled": True,
    }
    assert "EXPLICIT_SELECTED_FILES" in captured["body"]["route_reasons"]
    assert "EXPLICIT_MIXED_INTENT" in captured["body"]["route_reasons"]



def test_mode_ask_short_circuits_clarification_in_gateway():
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(file_id=11, file_type="pdf", file_name="solid-state-review.pdf"),
            ConversationFileRow(file_id=22, file_type="pdf", file_name="battery-paper.pdf"),
        ]
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
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
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 400
    assert response.json()["code"] == "FILE_SELECTION_CLARIFICATION_REQUIRED"
    assert response.json()["needs_clarification"] is True
    assert [item["file_id"] for item in response.json()["detail"]["clarify_candidates"]] == [11, 22]
    assert response.json()["detail"]["file_selection"]["strategy"] == "clarify_required"
    assert response.json()["detail"]["file_selection"]["selected_file_ids"] == [11, 22]
    assert response.json()["detail"]["route_reasons"] == ["MULTIPLE_FILES_NEED_CLARIFICATION"]
    assert response.json()["detail"]["route_confidence"] == 0.0
    assert response.json()["detail"]["classifier_used"] is False
    assert calls == []


def test_mode_ask_short_circuits_file_not_ready_status_in_gateway():
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=44,
                file_type="pdf",
                file_name="processing.pdf",
                parse_status="uploaded",
                index_status="pending",
                processing_stage="indexing",
                display_no=1,
            )
        ]
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "#1",
                    "requested_mode": "thinking",
                    "pdf_context": {},
                },
            )
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 409
    assert response.json()["code"] == "FILE_NOT_READY"
    assert response.json()["retriable"] is True
    assert response.json()["detail"]["file_selection"]["strategy"] == "explicit_ref"
    assert response.json()["detail"]["file_selection"]["selected_file_ids"] == [44]
    assert response.json()["detail"]["route_reasons"] == ["EXPLICIT_FILE_REF"]
    assert response.json()["detail"]["route_confidence"] == 1.0
    assert response.json()["detail"]["classifier_used"] is False
    assert calls == []


def test_mode_ask_short_circuits_unresolved_file_reference_as_clarification():
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub([])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            response = client.post(
                "/api/thinking/ask",
                json={
                    "question": "#1",
                    "requested_mode": "thinking",
                    "pdf_context": {},
                },
            )
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 400
    assert response.json()["code"] == "FILE_SELECTION_CLARIFICATION_REQUIRED"
    assert response.json()["needs_clarification"] is True
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
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(file_id=11, file_type="pdf", file_name="solid-state-review.pdf"),
            ConversationFileRow(file_id=22, file_type="pdf", file_name="battery-paper.pdf"),
        ]
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
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
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    assert calls == []
    assert b'"type":"metadata"' in body
    assert b'"needs_clarification":true' in body
    assert b'"clarify_candidates"' in body
    assert b'"file_selection"' in body
    assert b'"route_reasons":["MULTIPLE_FILES_NEED_CLARIFICATION"]' in body
    assert b'"route_confidence":0.0' in body
    assert b'"classifier_used":false' in body
    assert b'FILE_SELECTION_CLARIFICATION_REQUIRED' in body
    assert response.headers["content-type"].startswith("text/event-stream")


def test_mode_ask_stream_short_circuits_file_not_ready_status_in_gateway():
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [
            ConversationFileRow(
                file_id=44,
                file_type="pdf",
                file_name="processing.pdf",
                parse_status="uploaded",
                index_status="pending",
                processing_stage="indexing",
                display_no=1,
            )
        ]
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with client.stream(
                "POST",
                "/api/thinking/ask_stream",
                json={
                    "question": "#1",
                    "requested_mode": "thinking",
                    "pdf_context": {},
                },
            ) as response:
                body = b"".join(response.iter_bytes())
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    assert calls == []
    assert b'"type":"metadata"' in body
    assert b'FILE_NOT_READY' in body
    assert b'"retriable":true' in body
    assert b'"file_selection"' in body
    assert b'"route_reasons":["EXPLICIT_FILE_REF"]' in body
    assert b'"route_confidence":1.0' in body
    assert b'"classifier_used":false' in body
    assert response.headers["content-type"].startswith("text/event-stream")


def test_mode_ask_logs_route_decision_context(caplog):
    original = app.state.conversation_file_service
    app.state.conversation_file_service = _ConversationFilesStub(
        [ConversationFileRow(file_id=11, file_type="pdf", file_name="battery-paper.pdf")]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"final_answer": "ok"}})

    try:
        with _TransportGuard(handler):
            client = TestClient(app)
            with caplog.at_level("INFO", logger="app.routers.qa"):
                response = client.post(
                    "/api/thinking/ask",
                    json={
                        "question": "请总结这篇文献",
                        "requested_mode": "thinking",
                        "conversation_id": 88,
                        "pdf_context": {"selected_ids": [11]},
                    },
                )
    finally:
        app.state.conversation_file_service = original

    assert response.status_code == 200
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "gateway route decision" in text
    assert "requested_mode=thinking" in text
    assert "actual_mode=fast" in text
    assert "route=pdf_qa" in text
    assert "turn_mode=file_only" in text
    assert "source_scope=pdf" in text
    assert "selected_file_ids=[11]" in text
    assert "strategy=explicit_selection" in text
    assert "route_reasons=['EXPLICIT_SELECTED_FILES']" in text
    assert "classifier_used=False" in text
    assert "route_confidence=1.0" in text


def test_mode_ask_stream_forwards_user_id_to_fast_backend():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/quota/grants/precheck":
            payload = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"success": True, "data": {"grant_id": "grant-user-id", "quota_type": payload["quota_type"], "noop": False}},
            )
        if request.url.path == "/internal/quota/grants/grant-user-id/finalize":
            payload = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-user-id", "counted": payload["success"], "idempotent": False}})
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"type":"done","final_answer":"ok"}\n\n',
        )

    with _TransportGuard(handler):
        client = TestClient(app)
        response = client.post(
            "/api/fast/ask_stream",
            json={
                "question": "plain qa",
                "requested_mode": "fast",
                "conversation_id": 42,
                "user_id": 7,
            },
        )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/fast/ask_stream")
    assert captured["body"]["user_id"] == 7


import asyncio


def test_gateway_stream_summary_keeps_reference_objects_from_done_event():
    from app.core.config import GatewaySettings
    from app.services.conversation_persistence import ConversationPersistenceService

    service = ConversationPersistenceService(GatewaySettings.from_env())
    summary = service.new_stream_summary()

    async def body_iter():
        yield b'data: {"type":"content","content":"hello"}\n\n'
        yield (
            b'data: {"type":"done","final_answer":"hello","query_mode":"thinking",'
            b'"references":[{"doi":"10.1/a"}],'
            b'"reference_objects":[{"doi":"10.1/a","section_name":"Discussion","chunk_index":2,"evidence_text":"evidence","locator_confidence":"section"}],'
            b'"reference_links":[{"doi":"10.1/a","pdf_url":"/api/v1/view_pdf/10.1/a"}],'
            b'"doi_locations":{}}\n\n'
        )

    async def _collect():
        chunks = []
        async for chunk in service.extract_stream(body_iter=body_iter(), summary=summary):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())

    assert len(chunks) == 2
    assert summary.done_seen is True
    assert summary.references == [{"doi": "10.1/a"}]
    assert summary.reference_objects == [
        {
            "doi": "10.1/a",
            "section_name": "Discussion",
            "chunk_index": 2,
            "evidence_text": "evidence",
            "locator_confidence": "section",
        }
    ]
    metadata = summary.to_metadata()
    assert metadata["reference_objects"] == summary.reference_objects
