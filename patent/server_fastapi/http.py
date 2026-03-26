from __future__ import annotations

from typing import Any

from fastapi import Request

from server.errors import codes
from server.errors.core import APIError



def to_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise APIError(
        code=codes.INVALID_REQUEST,
        message="boolean query parameter is invalid",
        status_code=400,
        error="invalid_request",
        retriable=False,
    )



def read_bool_query(request: Request, name: str, *, default: bool = False) -> bool:
    return to_bool(request.query_params.get(name), default=default)
