from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


class ProtocolMismatchRequestError(ValueError):
    """Raised when gateway forwarded a payload outside the patent Phase 1 contract."""


@dataclass(frozen=True)
class PatentAskRequest:
    question: str
    conversation_id: int | None
    chat_history: list[dict[str, Any]]
    requested_mode: Literal["patent"]
    actual_mode: Literal["patent"]
    route: Literal["kb_qa"]
    source_scope: str | None
    turn_mode: Literal["kb_only"]
    kb_enabled: bool
    allow_kb_verification: bool
    used_files: list[dict[str, Any]]
    execution_files: list[dict[str, Any]]
    selected_file_ids: list[int]
    primary_file_id: int | None
    file_selection: dict[str, Any]
    trace_id: str
    options: dict[str, Any]

    @property
    def persistence_mode(self) -> Literal["durable", "ephemeral"]:
        return "durable" if self.conversation_id is not None else "ephemeral"

    @property
    def is_durable(self) -> bool:
        return self.conversation_id is not None



def _normalize_conversation_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        normalized = int(text)
        return normalized if normalized > 0 else None
    return None



def _require_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value



def _require_list_of_dicts(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} items must be objects")
    return list(value)



def _require_int_list(payload: dict[str, Any], field: str) -> list[int]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    normalized: list[int] = []
    for item in value:
        try:
            normalized.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} items must be integers") from exc
    return normalized



def _require_exact_string(payload: dict[str, Any], field: str, expected: str, *, protocol: bool = False) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if value == "":
        raise ValueError(f"{field} is required")
    if value != expected:
        if protocol:
            raise ProtocolMismatchRequestError(f"{field} must be {expected}")
        raise ValueError(f"{field} must be {expected}")
    return expected



def parse_patent_request(payload: Any) -> PatentAskRequest:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")

    question_value = payload.get("question")
    if not isinstance(question_value, str):
        raise ValueError("question must be a string")
    question = question_value.strip()
    if not question:
        raise ValueError("question is required")

    trace_value = payload.get("trace_id")
    if not isinstance(trace_value, str):
        raise ValueError("trace_id must be a string")
    trace_id = trace_value.strip()
    if not trace_id:
        raise ValueError("trace_id is required")

    requested_mode = _require_exact_string(payload, "requested_mode", "patent", protocol=True)
    actual_mode = _require_exact_string(payload, "actual_mode", "patent", protocol=True)
    route = _require_exact_string(payload, "route", "kb_qa", protocol=True)
    turn_mode = _require_exact_string(payload, "turn_mode", "kb_only", protocol=True)

    kb_enabled = _require_bool(payload, "kb_enabled")
    allow_kb_verification = _require_bool(payload, "allow_kb_verification")
    if allow_kb_verification:
        raise ProtocolMismatchRequestError("allow_kb_verification must be false in Phase 1")

    used_files = _require_list_of_dicts(payload, "used_files")
    if used_files:
        raise ProtocolMismatchRequestError("used_files must be empty in Phase 1")

    execution_files = _require_list_of_dicts(payload, "execution_files")
    if execution_files:
        raise ProtocolMismatchRequestError("execution_files must be empty in Phase 1")

    selected_file_ids = _require_int_list(payload, "selected_file_ids")
    if selected_file_ids:
        raise ProtocolMismatchRequestError("selected_file_ids must be empty in Phase 1")

    primary_file_id = payload.get("primary_file_id")
    if primary_file_id is not None:
        raise ProtocolMismatchRequestError("primary_file_id must be null in Phase 1")

    file_selection = payload.get("file_selection")
    if file_selection is None:
        file_selection = {}
    if not isinstance(file_selection, dict):
        raise ValueError("file_selection must be an object")

    chat_history = payload.get("chat_history")
    if chat_history is None:
        chat_history = []
    if not isinstance(chat_history, list):
        raise ValueError("chat_history must be an array")
    if any(not isinstance(item, dict) for item in chat_history):
        raise ValueError("chat_history items must be objects")

    options = payload.get("options")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")

    source_scope = payload.get("source_scope")
    if source_scope is not None:
        if not isinstance(source_scope, str):
            raise ValueError("source_scope must be a string or null")
        source_scope = source_scope.strip() or None

    return PatentAskRequest(
        question=question,
        conversation_id=_normalize_conversation_id(payload.get("conversation_id")),
        chat_history=list(chat_history),
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        route=route,
        source_scope=source_scope,
        turn_mode=turn_mode,
        kb_enabled=kb_enabled,
        allow_kb_verification=False,
        used_files=[],
        execution_files=[],
        selected_file_ids=[],
        primary_file_id=None,
        file_selection=dict(file_selection),
        trace_id=trace_id,
        options=dict(options),
    )
