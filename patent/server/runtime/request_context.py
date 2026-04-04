from __future__ import annotations

from contextvars import ContextVar, Token
import uuid


_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="req_unknown")



def generate_trace_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"



def set_trace_id(trace_id: str) -> Token:
    return _TRACE_ID.set(str(trace_id or "").strip() or "req_unknown")



def clear_trace_id(token: Token | None = None) -> None:
    if token is None:
        _TRACE_ID.set("req_unknown")
        return
    try:
        _TRACE_ID.reset(token)
    except (LookupError, RuntimeError, ValueError):
        _TRACE_ID.set("req_unknown")



def get_trace_id() -> str:
    return str(_TRACE_ID.get())
