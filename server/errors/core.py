"""Framework-neutral API error helpers."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from server.errors import codes
from server.runtime.request_context import get_trace_id


@dataclass
class APIError(Exception):
    """Structured API error for transport-layer mapping."""

    code: str
    message: str
    status_code: int
    error: str | None = None
    retriable: bool | None = None
    extra: dict[str, Any] | None = None


def build_error_payload(
    *,
    code: str,
    message: str,
    error: str | None = None,
    retriable: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error": error or code.lower(),
        "code": code,
        "message": message,
        "trace_id": get_trace_id(),
    }
    if retriable is not None:
        payload["retriable"] = bool(retriable)
    if extra:
        payload.update(extra)
    return payload


def build_internal_error_payload() -> dict[str, Any]:
    return build_error_payload(
        code=codes.INTERNAL_ERROR,
        message="internal server error",
        error="internal_error",
        retriable=False,
    )


def raise_invalid_request(message: str) -> None:
    raise APIError(
        code=codes.INVALID_REQUEST,
        message=message,
        status_code=int(HTTPStatus.BAD_REQUEST),
        error="invalid_request",
        retriable=False,
    )
