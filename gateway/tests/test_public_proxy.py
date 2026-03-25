import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


class _TransportGuard:
    def __init__(self, handler):
        self._transport = httpx.MockTransport(handler)

    def __enter__(self):
        app.state.proxy_service.set_transport(self._transport)
        return self

    def __exit__(self, exc_type, exc, tb):
        app.state.proxy_service.set_transport(None)
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
