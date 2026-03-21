"""Trace id middleware and helpers."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

TRACE_ID_HEADER = "X-Trace-Id"


def get_trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", "") or "")


async def trace_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    trace_id = str(request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex).strip()
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers[TRACE_ID_HEADER] = trace_id
    return response
