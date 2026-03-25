"""Trace id middleware and helpers."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping

from fastapi import Request, Response

TRACE_ID_HEADER = "X-Trace-Id"
TRACE_ID_HEADER_CANDIDATES = (
    TRACE_ID_HEADER,
    "X-Trace-ID",
    "X-Request-ID",
)
TRACE_COMPAT_HEADER_NAMES_LOWER = {item.lower() for item in TRACE_ID_HEADER_CANDIDATES}


def resolve_trace_id(headers: Mapping[str, str]) -> str:
    for header_name in TRACE_ID_HEADER_CANDIDATES:
        value = str(headers.get(header_name) or "").strip()
        if value:
            return value
    return uuid.uuid4().hex


def get_trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", "") or "")


async def trace_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    trace_id = resolve_trace_id(request.headers)
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers[TRACE_ID_HEADER] = trace_id
    return response
