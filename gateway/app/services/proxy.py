"""HTTP proxy helpers for upstream backend forwarding."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request, Response

from app.core.config import GatewaySettings
from app.core.trace import TRACE_ID_HEADER, get_trace_id
from app.services.backend_registry import BackendTarget


_HOP_BY_HOP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

_HOP_BY_HOP_RESPONSE_HEADERS = {
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class ProxyPlan:
    backend: str
    upstream_url: str
    streaming: bool = False


@dataclass
class StreamingProxyHandle:
    backend: str
    status_code: int
    headers: dict[str, str]
    upstream: httpx.Response
    client: httpx.AsyncClient

    async def body_iter(self) -> AsyncIterator[bytes]:
        try:
            if getattr(self.upstream, "is_stream_consumed", False):
                content = self.upstream.content
                if content:
                    yield content
                return
            async for chunk in self.upstream.aiter_raw():
                if chunk:
                    yield chunk
        finally:
            await self.upstream.aclose()
            await self.client.aclose()


class ProxyService:
    def __init__(self, settings: GatewaySettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    def build_plan(self, *, target: BackendTarget, path: str, query: str = "", streaming: bool = False) -> ProxyPlan:
        normalized_path = path if path.startswith("/") else f"/{path}"
        upstream_url = f"{target.base_url}{normalized_path}"
        if query:
            upstream_url = f"{upstream_url}?{query}"
        return ProxyPlan(
            backend=target.name,
            upstream_url=upstream_url,
            streaming=streaming,
        )

    async def forward(self, *, request: Request, target: BackendTarget, path: str | None = None) -> Response:
        upstream_path = path or request.url.path
        upstream_query = str(request.url.query or "")
        plan = self.build_plan(target=target, path=upstream_path, query=upstream_query, streaming=False)
        body = await request.body()
        headers = self._prepare_upstream_headers(request)

        async with self._build_client() as client:
            upstream = await client.request(
                method=request.method,
                url=plan.upstream_url,
                headers=headers,
                content=body,
            )

        return self._build_downstream_response(plan=plan, upstream=upstream)

    async def forward_json(self, *, request: Request, target: BackendTarget, path: str, payload: dict[str, Any]) -> Response:
        plan = self.build_plan(target=target, path=path, streaming=False)
        headers = self._prepare_upstream_headers(request)

        async with self._build_client() as client:
            upstream = await client.request(
                method=request.method,
                url=plan.upstream_url,
                headers=headers,
                json=payload,
            )

        return self._build_downstream_response(plan=plan, upstream=upstream)

    async def open_request_stream(
        self,
        *,
        request: Request,
        target: BackendTarget,
        path: str | None = None,
    ) -> StreamingProxyHandle:
        upstream_path = path or request.url.path
        upstream_query = str(request.url.query or "")
        plan = self.build_plan(target=target, path=upstream_path, query=upstream_query, streaming=True)
        headers = self._prepare_upstream_headers(request)
        client = self._build_client(streaming=True)
        upstream_request = client.build_request(
            method=request.method,
            url=plan.upstream_url,
            headers=headers,
            content=request.stream(),
        )
        upstream = await client.send(upstream_request, stream=True)
        response_headers = self._filter_response_headers(dict(upstream.headers))
        response_headers.setdefault("X-Gateway-Backend", plan.backend)
        return StreamingProxyHandle(
            backend=plan.backend,
            status_code=upstream.status_code,
            headers=response_headers,
            upstream=upstream,
            client=client,
        )

    async def open_json_stream(
        self,
        *,
        request: Request,
        target: BackendTarget,
        path: str,
        payload: dict[str, Any],
    ) -> StreamingProxyHandle:
        plan = self.build_plan(target=target, path=path, streaming=True)
        headers = self._prepare_upstream_headers(request)
        client = self._build_client(streaming=True)
        upstream_request = client.build_request(
            method=request.method,
            url=plan.upstream_url,
            headers=headers,
            json=payload,
        )
        upstream = await client.send(upstream_request, stream=True)
        response_headers = self._filter_response_headers(dict(upstream.headers))
        response_headers.setdefault("X-Gateway-Backend", plan.backend)
        return StreamingProxyHandle(
            backend=plan.backend,
            status_code=upstream.status_code,
            headers=response_headers,
            upstream=upstream,
            client=client,
        )

    async def probe_health(self, *, target: BackendTarget) -> dict[str, Any]:
        plan = self.build_plan(target=target, path="/api/health")
        try:
            async with self._build_client(timeout=min(self._settings.request_timeout_seconds, 5)) as client:
                upstream = await client.get(plan.upstream_url)
            payload: Any
            try:
                payload = upstream.json()
            except Exception:
                payload = None
            return {
                "ok": 200 <= upstream.status_code < 300,
                "status_code": upstream.status_code,
                "backend": target.name,
                "base_url": target.base_url,
                "payload": payload,
            }
        except Exception as exc:
            return {
                "ok": False,
                "status_code": None,
                "backend": target.name,
                "base_url": target.base_url,
                "error": str(exc),
            }

    def _build_client(self, *, timeout: int | None = None, streaming: bool = False) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._build_timeout(timeout=timeout, streaming=streaming),
            follow_redirects=False,
            transport=self._transport,
        )

    def _build_timeout(self, *, timeout: int | None = None, streaming: bool = False) -> httpx.Timeout | float:
        base_timeout = float(timeout or self._settings.request_timeout_seconds)
        if not streaming:
            return base_timeout
        return httpx.Timeout(
            connect=base_timeout,
            write=base_timeout,
            pool=base_timeout,
            read=float(self._settings.sse_timeout_seconds),
        )

    def _prepare_upstream_headers(self, request: Request) -> dict[str, str]:
        headers = self._filter_request_headers(dict(request.headers))
        trace_id = get_trace_id(request)
        if trace_id and TRACE_ID_HEADER.lower() not in {key.lower() for key in headers}:
            headers.setdefault(TRACE_ID_HEADER, trace_id)
        return headers

    def _build_downstream_response(self, *, plan: ProxyPlan, upstream: httpx.Response) -> Response:
        response_headers = self._filter_response_headers(dict(upstream.headers))
        response_headers.setdefault("X-Gateway-Backend", plan.backend)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )

    def _filter_request_headers(self, headers: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in headers.items():
            if key.lower() in _HOP_BY_HOP_REQUEST_HEADERS:
                continue
            result[key] = value
        return result

    def _filter_response_headers(self, headers: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in headers.items():
            if key.lower() in _HOP_BY_HOP_RESPONSE_HEADERS:
                continue
            result[key] = value
        return result
