"""Per-request context utilities."""

from __future__ import annotations

from contextvars import ContextVar
import uuid


_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="req_unknown")


def generate_trace_id() -> str:
    """Generate a compact request trace id."""
    return f"req_{uuid.uuid4().hex[:12]}"


def set_trace_id(trace_id: str) -> None:
    _TRACE_ID.set(str(trace_id or "").strip() or "req_unknown")


def clear_trace_id() -> None:
    _TRACE_ID.set("req_unknown")


def get_trace_id() -> str:
    return str(_TRACE_ID.get())
