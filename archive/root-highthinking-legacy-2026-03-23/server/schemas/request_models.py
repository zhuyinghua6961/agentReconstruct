"""Request parsing and validation models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_MODES = {"fast", "thinking", "patent"}


class ModeNotSupportedRequestError(ValueError):
    """Raised when mode is outside allowed values."""


class ModeMismatchRequestError(ValueError):
    """Raised when body mode conflicts with route mode."""


@dataclass(frozen=True)
class AskRequest:
    question: str
    mode: str
    user_id: int | None
    conversation_id: str | int | None
    chat_history: list[dict[str, Any]]
    options: dict[str, Any]


def _validate_chat_history(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("chat_history must be an array")
    if len(raw) > 20:
        raise ValueError("chat_history max size is 20")

    result: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"chat_history[{idx}] must be an object")
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"chat_history[{idx}].role must be user|assistant|system")
        content = str(item.get("content") or "")
        if not content.strip():
            raise ValueError(f"chat_history[{idx}].content is required")
        if len(content) > 4000:
            raise ValueError(f"chat_history[{idx}].content exceeds 4000 chars")
        result.append({"role": role, "content": content})
    return result


def parse_ask_request(payload: Any, *, forced_mode: str | None = None) -> AskRequest:
    """Validate request JSON and normalize to AskRequest."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")

    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValueError("question is required")
    if len(question) > 4000:
        raise ValueError("question exceeds 4000 chars")

    allowed_values_text = ", ".join(sorted(ALLOWED_MODES))
    body_mode = str(payload.get("mode") or "").strip().lower()
    if forced_mode is not None:
        route_mode = str(forced_mode or "").strip().lower()
        if route_mode not in ALLOWED_MODES:
            raise ModeNotSupportedRequestError(f"mode must be one of: {allowed_values_text}")
        if body_mode and body_mode != route_mode:
            raise ModeMismatchRequestError("mode in path and body are inconsistent")
        mode = route_mode
    else:
        mode = body_mode or "fast"
        if mode not in ALLOWED_MODES:
            raise ModeNotSupportedRequestError(f"mode must be one of: {allowed_values_text}")

    user_id_raw = payload.get("user_id")
    user_id: int | None
    if user_id_raw is None:
        user_id = None
    else:
        try:
            user_id = int(user_id_raw)
        except Exception:
            raise ValueError("user_id must be integer")
        if user_id <= 0:
            raise ValueError("user_id must be positive")

    conversation_id = payload.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, (str, int)):
        raise ValueError("conversation_id must be string or number")

    chat_history = _validate_chat_history(payload.get("chat_history"))

    options_raw = payload.get("options")
    if options_raw is None:
        options = {}
    elif isinstance(options_raw, dict):
        options = options_raw
    else:
        raise ValueError("options must be an object")

    return AskRequest(
        question=question,
        mode=mode,
        user_id=user_id,
        conversation_id=conversation_id,
        chat_history=chat_history,
        options=options,
    )
