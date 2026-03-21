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
    requested_mode: str = "fast"
    actual_mode: str = "fast"
    route: str = "kb_qa"
    turn_mode: str = "kb_only"
    allow_kb_verification: bool = False
    used_files: list[dict[str, Any]] = None  # type: ignore[assignment]
    execution_files: list[dict[str, Any]] = None  # type: ignore[assignment]
    trace_id: str | None = None
    user_id: int | None = None
    conversation_id: str | int | None = None
    chat_history: list[dict[str, Any]] = None  # type: ignore[assignment]
    options: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_mode", str(self.requested_mode or self.mode or "fast").strip().lower() or "fast")
        object.__setattr__(self, "actual_mode", str(self.actual_mode or self.mode or "fast").strip().lower() or "fast")
        object.__setattr__(self, "route", str(self.route or "kb_qa").strip() or "kb_qa")
        object.__setattr__(self, "turn_mode", str(self.turn_mode or "kb_only").strip() or "kb_only")
        object.__setattr__(self, "used_files", list(self.used_files or []))
        object.__setattr__(self, "execution_files", list(self.execution_files or []))
        object.__setattr__(self, "chat_history", list(self.chat_history or []))
        object.__setattr__(self, "options", dict(self.options or {}))


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
        body_actual_mode = str(payload.get("actual_mode") or "").strip().lower()
        if body_actual_mode and body_actual_mode != route_mode:
            raise ModeMismatchRequestError("actual_mode in body and mode in path are inconsistent")
        mode = route_mode
    else:
        mode = body_mode or "fast"
        if mode not in ALLOWED_MODES:
            raise ModeNotSupportedRequestError(f"mode must be one of: {allowed_values_text}")

    requested_mode = str(payload.get("requested_mode") or body_mode or mode or "fast").strip().lower() or "fast"
    if requested_mode not in ALLOWED_MODES:
        raise ModeNotSupportedRequestError(f"mode must be one of: {allowed_values_text}")

    actual_mode = str(payload.get("actual_mode") or mode).strip().lower() or mode
    if actual_mode not in ALLOWED_MODES:
        raise ModeNotSupportedRequestError(f"mode must be one of: {allowed_values_text}")
    if forced_mode is not None and actual_mode != mode:
        raise ModeMismatchRequestError("actual_mode in body and mode in path are inconsistent")

    route = str(payload.get("route") or "kb_qa").strip() or "kb_qa"
    turn_mode = str(payload.get("turn_mode") or "kb_only").strip() or "kb_only"
    allow_kb_verification = bool(payload.get("allow_kb_verification", False))
    used_files_raw = payload.get("used_files")
    execution_files_raw = payload.get("execution_files")
    used_files = list(used_files_raw) if isinstance(used_files_raw, list) else []
    execution_files = list(execution_files_raw) if isinstance(execution_files_raw, list) else []
    trace_id = payload.get("trace_id")
    if trace_id is not None:
        trace_id = str(trace_id).strip() or None

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
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        route=route,
        turn_mode=turn_mode,
        allow_kb_verification=allow_kb_verification,
        used_files=used_files,
        execution_files=execution_files,
        trace_id=trace_id,
        user_id=user_id,
        conversation_id=conversation_id,
        chat_history=chat_history,
        options=options,
    )
