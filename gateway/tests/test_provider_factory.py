import asyncio

import httpx
from starlette.requests import Request

from app.core.trace import TRACE_ID_HEADER
from app.core.config import BackendEndpoints, GatewaySettings
from app.providers.conversation_files.noop import NoopConversationFileProvider
from app.providers.conversation_files.base import ConversationFileProviderError
from app.providers.conversation_files.public_http import PublicHttpConversationFileProvider
from app.services.provider_factory import build_conversation_file_provider


def _settings(provider: str) -> GatewaySettings:
    return GatewaySettings(
        app_name="gateway",
        environment="test",
        debug=False,
        host="127.0.0.1",
        port=8101,
        request_timeout_seconds=30,
        sse_timeout_seconds=600,
        conversation_file_provider=provider,
        endpoints=BackendEndpoints(
            public="http://127.0.0.1:8008",
            fast="http://127.0.0.1:8008",
            thinking="http://127.0.0.1:8009",
            patent="http://127.0.0.1:8010",
        ),
    )


def test_build_noop_conversation_file_provider():
    provider = build_conversation_file_provider(_settings("noop"))
    assert isinstance(provider, NoopConversationFileProvider)
    assert provider.provider_name == "noop"


def test_build_public_http_conversation_file_provider():
    provider = build_conversation_file_provider(_settings("public_http"))
    assert isinstance(provider, PublicHttpConversationFileProvider)
    assert provider.provider_name == "public_http"


def test_public_http_provider_fetches_and_normalizes_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/conversations/42/files"
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

    provider = PublicHttpConversationFileProvider(
        base_url="http://127.0.0.1:8008",
        transport=httpx.MockTransport(handler),
    )
    rows = asyncio.run(provider.list_files(conversation_id=42))
    assert len(rows) == 1
    assert rows[0].file_id == 33
    assert rows[0].is_table is True
    assert rows[0].file_meta["columns"] == ["开路电压_V"]


def test_public_http_provider_accepts_top_level_files_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token-1"
        assert request.headers[TRACE_ID_HEADER] == "trace-abc"
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": 18,
                        "file_type": "pdf",
                        "file_name": "paper.pdf",
                    }
                ]
            },
        )

    provider = PublicHttpConversationFileProvider(
        base_url="http://127.0.0.1:8008",
        transport=httpx.MockTransport(handler),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/internal",
            "headers": [
                (b"authorization", b"Bearer token-1"),
            ],
        }
    )
    request.state.trace_id = "trace-abc"
    rows = asyncio.run(provider.list_files(conversation_id=9, request=request))
    assert len(rows) == 1
    assert rows[0].file_id == 18
    assert rows[0].is_pdf is True


def test_public_http_provider_raises_when_upstream_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/conversations/42/files"
        return httpx.Response(503, json={"success": False})

    provider = PublicHttpConversationFileProvider(
        base_url="http://127.0.0.1:8008",
        transport=httpx.MockTransport(handler),
    )

    try:
        asyncio.run(provider.list_files(conversation_id=42))
        assert False, "expected ConversationFileProviderError"
    except ConversationFileProviderError as exc:
        assert exc.provider == "public_http"
        assert exc.status_code == 503
