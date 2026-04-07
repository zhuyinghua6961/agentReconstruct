"""HTTP proxy helpers for upstream backend forwarding."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Request, Response

from app.core.config import GatewaySettings
from app.core.trace import TRACE_COMPAT_HEADER_NAMES_LOWER, TRACE_ID_HEADER, get_trace_id
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

_PUBLIC_PATH_REWRITES = {
    "/api/upload_pdf": "/upload_pdf",
    "/api/upload_excel": "/upload_excel",
    "/api/clear_pdf": "/clear_pdf",
    "/api/health": "/health",
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
    _abort_requested: bool = field(default=False, init=False, repr=False)

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
        except Exception:
            if self._abort_requested:
                return
            raise
        finally:
            await self.upstream.aclose()
            await self.client.aclose()

    async def abort(self) -> None:
        self._abort_requested = True
        first_error: Exception | None = None
        for stream in self._abort_stream_chain():
            close_stream = getattr(stream, "aclose", None)
            if not callable(close_stream):
                continue
            try:
                await close_stream()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        for resource in (self.upstream, self.client):
            try:
                await resource.aclose()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _abort_stream_chain(self) -> list[Any]:
        chain: list[Any] = []
        pending = [getattr(self.upstream, "stream", None), getattr(self.upstream, "_stream", None)]
        seen: set[int] = set()
        while pending:
            stream = pending.pop()
            if stream is None:
                continue
            marker = id(stream)
            if marker in seen:
                continue
            seen.add(marker)
            chain.append(stream)
            nested = getattr(stream, "_stream", None)
            if nested is not None:
                pending.append(nested)
        chain.reverse()
        return chain


class ProxyService:
    def __init__(self, settings: GatewaySettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    def build_plan(self, *, target: BackendTarget, path: str, query: str = "", streaming: bool = False) -> ProxyPlan:
        normalized_path = self._resolve_upstream_path(target=target, path=path)
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
        headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in TRACE_COMPAT_HEADER_NAMES_LOWER
        }
        if trace_id:
            headers[TRACE_ID_HEADER] = trace_id
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
            lowered = key.lower()
            if lowered in _HOP_BY_HOP_RESPONSE_HEADERS:
                continue
            if lowered in TRACE_COMPAT_HEADER_NAMES_LOWER:
                continue
            result[key] = value
        return result

    def _resolve_upstream_path(self, *, target: BackendTarget, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        if target.name == "public":
            return _PUBLIC_PATH_REWRITES.get(normalized_path, normalized_path)
        return normalized_path
