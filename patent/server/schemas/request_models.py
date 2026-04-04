from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


class ProtocolMismatchRequestError(ValueError):
    """Raised when gateway forwarded a payload outside the patent Phase 1 contract."""


PatentRouteName = Literal["kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"]
PatentTurnMode = Literal["kb_only", "file_only", "mixed"]
PatentSourceScope = Literal["kb", "pdf", "table", "pdf+kb", "table+kb", "pdf+table", "pdf+table+kb"]

_ROUTE_TO_SOURCE_SCOPES: dict[str, set[str]] = {
    "kb_qa": {"kb"},
    "pdf_qa": {"pdf"},
    "tabular_qa": {"table"},
    "hybrid_qa": {"pdf+kb", "table+kb", "pdf+table", "pdf+table+kb"},
}

_PDF_FILE_TYPES = {"pdf"}
_TABLE_FILE_TYPES = {"csv", "excel", "table", "xls", "xlsx", "xlsm"}


@dataclass(frozen=True)
class PatentAskRequest:
    question: str
    conversation_id: int | None
    chat_history: list[dict[str, Any]]
    requested_mode: Literal["patent"]
    actual_mode: Literal["patent"]
    route: PatentRouteName
    source_scope: PatentSourceScope
    turn_mode: PatentTurnMode
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
        raise ValueError("conversation_id must be a positive integer or null")
    if isinstance(value, int):
        if value > 0:
            return value
        raise ValueError("conversation_id must be a positive integer or null")
    if not isinstance(value, str):
        raise ValueError("conversation_id must be a positive integer or null")
    text = value.strip()
    if not text:
        raise ValueError("conversation_id must be a positive integer or null")
    if text.isdigit():
        normalized = int(text)
        if normalized > 0:
            return normalized
    raise ValueError("conversation_id must be a positive integer or null")



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
        normalized.append(_require_int(item, field=f"{field} items"))
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


def _require_protocol_literal(payload: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if value == "":
        raise ValueError(f"{field} is required")
    normalized = value.strip()
    if value != normalized:
        joined = ", ".join(sorted(allowed))
        raise ProtocolMismatchRequestError(f"{field} must be one of {{{joined}}}")
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise ProtocolMismatchRequestError(f"{field} must be one of {{{joined}}}")
    return normalized


def _require_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be integers")
    return int(value)


def _selected_file_families(*, execution_files: list[dict[str, Any]], selected_file_ids: list[int]) -> set[str]:
    selected_ids = set(selected_file_ids)
    families: set[str] = set()
    for item in execution_files:
        if not isinstance(item, dict):
            continue
        file_id = item.get("file_id")
        if file_id is None:
            continue
        normalized_file_id = _require_int(file_id, field="execution_files.file_id")
        if normalized_file_id not in selected_ids:
            continue
        file_type = str(item.get("file_type") or "").strip().lower()
        if file_type in _PDF_FILE_TYPES:
            families.add("pdf")
        elif file_type in _TABLE_FILE_TYPES:
            families.add("table")
    return families



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
    route = _require_protocol_literal(payload, "route", set(_ROUTE_TO_SOURCE_SCOPES))
    turn_mode = _require_protocol_literal(payload, "turn_mode", {"kb_only", "file_only", "mixed"})

    kb_enabled = _require_bool(payload, "kb_enabled")
    allow_kb_verification = _require_bool(payload, "allow_kb_verification")

    used_files = _require_list_of_dicts(payload, "used_files")
    execution_files = _require_list_of_dicts(payload, "execution_files")
    selected_file_ids = _require_int_list(payload, "selected_file_ids")

    primary_file_id = payload.get("primary_file_id")
    if primary_file_id is not None:
        primary_file_id = _require_int(primary_file_id, field="primary_file_id")

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
    if source_scope is None:
        source_scope = "kb" if route == "kb_qa" else ""
    elif not isinstance(source_scope, str):
        raise ValueError("source_scope must be a string or null")
    else:
        source_scope = source_scope.strip()

    expected_turn_mode = _expected_turn_mode(route=route, source_scope=source_scope)
    if turn_mode != expected_turn_mode:
        raise ProtocolMismatchRequestError(f"turn_mode must be {expected_turn_mode}")

    allowed_source_scopes = _ROUTE_TO_SOURCE_SCOPES[route]
    if source_scope not in allowed_source_scopes:
        raise ProtocolMismatchRequestError(f"source_scope must be one of {sorted(allowed_source_scopes)}")

    if route == "kb_qa":
        if used_files:
            raise ProtocolMismatchRequestError("used_files must be empty for kb_qa")
        if execution_files:
            raise ProtocolMismatchRequestError("execution_files must be empty for kb_qa")
        if selected_file_ids:
            raise ProtocolMismatchRequestError("selected_file_ids must be empty for kb_qa")
        if primary_file_id is not None:
            raise ProtocolMismatchRequestError("primary_file_id must be null for kb_qa")
        if file_selection:
            raise ProtocolMismatchRequestError("file_selection must be empty for kb_qa")
        if allow_kb_verification:
            raise ProtocolMismatchRequestError("allow_kb_verification must be false for kb_qa")
    else:
        if not execution_files:
            raise ProtocolMismatchRequestError("execution_files must not be empty for file routes")
        if not selected_file_ids:
            raise ProtocolMismatchRequestError("selected_file_ids must not be empty for file routes")
        execution_file_ids = {
            _require_int(item.get("file_id"), field="execution_files.file_id")
            for item in execution_files
            if isinstance(item, dict) and item.get("file_id") is not None
        }
        if any(file_id not in execution_file_ids for file_id in selected_file_ids):
            raise ProtocolMismatchRequestError("selected_file_ids must exist in execution_files")
        if primary_file_id is not None and primary_file_id not in selected_file_ids:
            raise ProtocolMismatchRequestError("primary_file_id must exist in selected_file_ids")
        selected_families = _selected_file_families(
            execution_files=execution_files,
            selected_file_ids=selected_file_ids,
        )
        expected_families = {token for token in source_scope.split("+") if token in {"pdf", "table"}}
        if selected_families != expected_families:
            raise ProtocolMismatchRequestError("selected_file_ids must match source_scope exactly")
        if "kb" in source_scope.split("+"):
            if not kb_enabled:
                raise ProtocolMismatchRequestError("kb_enabled must be true when source_scope includes kb")
            if not allow_kb_verification:
                raise ProtocolMismatchRequestError("allow_kb_verification must be true when source_scope includes kb")
        elif kb_enabled:
            raise ProtocolMismatchRequestError("kb_enabled must be false when source_scope excludes kb")
        if allow_kb_verification and "kb" not in source_scope.split("+"):
            raise ProtocolMismatchRequestError("allow_kb_verification requires source_scope with kb")

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
        allow_kb_verification=allow_kb_verification,
        used_files=list(used_files),
        execution_files=list(execution_files),
        selected_file_ids=list(selected_file_ids),
        primary_file_id=primary_file_id,
        file_selection=dict(file_selection),
        trace_id=trace_id,
        options=dict(options),
    )


def _expected_turn_mode(*, route: str, source_scope: str) -> str:
    if route == "kb_qa":
        return "kb_only"
    if route in {"pdf_qa", "tabular_qa"}:
        return "file_only"
    if route == "hybrid_qa":
        return "mixed" if "kb" in source_scope.split("+") else "file_only"
    return "kb_only"
