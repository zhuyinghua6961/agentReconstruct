from __future__ import annotations

import httpx
import pytest
from starlette.requests import Request as StarletteRequest

from app.main import app
from app.routers.patent_proxy import _should_count_patent_search_response
from app.routers.qa import _record_ask_usage_activity
from app.services.usage_stats_client import UsageStatsClientResult


class _TransportGuard:
    def __init__(self, handler):
        self._transport = httpx.MockTransport(handler)

    def __enter__(self):
        app.state.proxy_service.set_transport(self._transport)
        app.state.quota_proxy_service.set_transport(self._transport)
        app.state.usage_stats_client.set_transport(self._transport)
        return self

    def __exit__(self, exc_type, exc, tb):
        app.state.proxy_service.set_transport(None)
        app.state.quota_proxy_service.set_transport(None)
        app.state.usage_stats_client.set_transport(None)
        return False


class _RecordingUsageStatsClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def set_transport(self, transport) -> None:
        _ = transport

    async def record_event(self, **kwargs):
        self.calls.append(dict(kwargs))
        return UsageStatsClientResult(status_code=200, payload={"success": True})


class _Auth:
    async def require_auth_context(self, request):
        from app.core.auth import AuthContext

        _ = request
        return AuthContext(user_id=7)


def test_should_count_patent_search_response_rules():
    ok = type(
        "_Response",
        (),
        {
            "status_code": 200,
            "body": b'{"items":[{"canonical_patent_id":"CN1"}],"count":1}',
        },
    )()
    bad = type(
        "_Response",
        (),
        {
            "status_code": 200,
            "body": b'{"error":"x","items":[]}',
        },
    )()
    unavailable = type(
        "_Response",
        (),
        {
            "status_code": 200,
            "body": b'{"code":"RETRIEVAL_RUNTIME_UNAVAILABLE"}',
        },
    )()
    assert _should_count_patent_search_response(ok) is True
    assert _should_count_patent_search_response(bad) is False
    assert _should_count_patent_search_response(unavailable) is False


@pytest.mark.anyio
async def test_record_ask_usage_activity_records_supported_types():
    client = _RecordingUsageStatsClient()
    scope = {"type": "http", "method": "POST", "path": "/api/fast/ask", "headers": [], "app": app}
    request = StarletteRequest(scope)
    request.app.state.usage_stats_client = client

    await _record_ask_usage_activity(
        request=request,
        user_id=7,
        quota_type="ask_query",
        trace_id="trace-1",
        conversation_id=12,
        success=True,
    )
    await _record_ask_usage_activity(
        request=request,
        user_id=7,
        quota_type="file_qa",
        trace_id="trace-2",
        conversation_id=13,
        success=True,
    )
    await _record_ask_usage_activity(
        request=request,
        user_id=7,
        quota_type="doc_assist",
        trace_id="trace-3",
        conversation_id=14,
        success=True,
    )

    assert len(client.calls) == 2
    assert client.calls[0]["event_type"] == "ask_query"
    assert client.calls[1]["event_type"] == "file_qa"


def test_patent_proxy_records_usage_on_success(monkeypatch):
    monkeypatch.setattr(app.state, "gateway_auth_service", _Auth())
    recorded: list[dict] = []

    class _Client:
        def set_transport(self, transport):
            _ = transport

        async def record_event(self, **kwargs):
            recorded.append(dict(kwargs))
            return UsageStatsClientResult(status_code=200, payload={"success": True})

    app.state.usage_stats_client = _Client()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/internal/quota/grants/precheck"):
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-1"}})
        if "/finalize" in request.url.path:
            return httpx.Response(200, json={"success": True, "data": {"counted": True}})
        if request.url.path.endswith("/internal/activity/record"):
            recorded.append(dict(request.json()))
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"items": [{"canonical_patent_id": "CN123"}], "count": 1})

    with _TransportGuard(handler):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get(
            "/api/patent_search?query=battery",
            headers={"Authorization": "Bearer demo"},
        )

    assert response.status_code == 200
    assert any(call.get("event_type") == "patent_search" for call in recorded)


def test_patent_proxy_skips_usage_when_search_fails(monkeypatch):
    monkeypatch.setattr(app.state, "gateway_auth_service", _Auth())
    recorded: list[dict] = []

    class _Client:
        def set_transport(self, transport):
            _ = transport

        async def record_event(self, **kwargs):
            recorded.append(dict(kwargs))
            return UsageStatsClientResult(status_code=200, payload={"success": True})

    app.state.usage_stats_client = _Client()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/internal/quota/grants/precheck"):
            return httpx.Response(200, json={"success": True, "data": {"grant_id": "grant-1"}})
        if "/finalize" in request.url.path:
            return httpx.Response(200, json={"success": True, "data": {"counted": False}})
        return httpx.Response(200, json={"error": "runtime down", "items": []})

    with _TransportGuard(handler):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get(
            "/api/patent_search?query=battery",
            headers={"Authorization": "Bearer demo"},
        )

    assert response.status_code == 200
    assert recorded == []
