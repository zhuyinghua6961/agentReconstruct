import asyncio

import httpx
from starlette.requests import Request

from app.core.trace import TRACE_ID_HEADER
from app.core.config import (
    AdmissionSettings,
    BackendEndpoints,
    GatewaySettings,
    RedisSettings,
    RouteClassifierSettings,
)
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
            public="http://127.0.0.1:8102",
            fast="http://127.0.0.1:8008",
            thinking="http://127.0.0.1:8009",
            patent="http://127.0.0.1:8010",
        ),
        redis=RedisSettings(
            enabled=False,
            url="",
            host="127.0.0.1",
            port=6379,
            username="",
            password="",
            db=0,
            key_prefix="gateway",
            socket_connect_timeout_seconds=2,
            socket_timeout_seconds=2,
        ),
        admission=AdmissionSettings(
            enabled=False,
            runtime_role="web",
            dispatcher_enabled=False,
            control_api_token="",
            poll_interval_seconds=5,
            max_concurrent=10,
            fast_or_patent_max_concurrent=10,
            thinking_max_concurrent=2,
            per_user_max_active=5,
            thinking_min_slots=1,
            queue_max_size=200,
            queued_ttl_seconds=900,
            post_admit_attach_ttl_seconds=600,
        ),
        route_classifier=RouteClassifierSettings(
            enabled=False,
            provider="noop",
            high_confidence_threshold=0.8,
            medium_confidence_threshold=0.6,
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
    assert provider._base_url == "http://127.0.0.1:8102"


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
