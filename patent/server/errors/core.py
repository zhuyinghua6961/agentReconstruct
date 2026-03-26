from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from server.errors import codes


@dataclass(slots=True)
class APIError(Exception):
    code: str
    message: str
    status_code: int
    error: str = "api_error"
    retriable: bool = False
    extra: dict[str, Any] = field(default_factory=dict)



def build_error_payload(
    *,
    code: str,
    message: str,
    error: str,
    retriable: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "code": code,
        "message": message,
        "error": error,
        "retriable": retriable,
    }
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
