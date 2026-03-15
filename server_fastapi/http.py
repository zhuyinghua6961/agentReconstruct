"""Shared FastAPI request helpers."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from server.errors.core import raise_invalid_request


async def read_json_payload(request: Request) -> Any:
    if getattr(request.state, "_json_payload_loaded", False):
        return request.state._json_payload
    try:
        payload = await request.json()
    except Exception:
        payload = None
    request.state._json_payload = payload
    request.state._json_payload_loaded = True
    return payload


async def resolve_user_id(request: Request, *, required: bool = True) -> int | None:
    raw = request.query_params.get("user_id")
    if raw is None:
        payload = await read_json_payload(request)
        if isinstance(payload, dict) and payload.get("user_id") is not None:
            raw = payload.get("user_id")
    if raw is None:
        if required:
            raise_invalid_request("user_id is required")
        return None
    try:
        user_id = int(raw)
    except Exception:
        raise_invalid_request("user_id must be integer")
    if user_id <= 0:
        raise_invalid_request("user_id must be positive")
    return user_id


def to_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)
