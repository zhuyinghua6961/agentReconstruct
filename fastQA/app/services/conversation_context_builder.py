from __future__ import annotations

import os
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


_DEFAULT_MAX_RECENT_TURNS = _env_int("FASTQA_CONTEXT_RECENT_TURNS", 8, minimum=1, maximum=20)
_DEFAULT_MAX_MESSAGE_CHARS = _env_int("FASTQA_CONTEXT_MESSAGE_MAX_CHARS", 800, minimum=50, maximum=4000)
_DEFAULT_MAX_TOTAL_CHARS = _env_int("FASTQA_CONTEXT_TOTAL_MAX_CHARS", 4000, minimum=200, maximum=20000)


def _normalize_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    return text[:max_chars]


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def normalize_turns(turns: list[dict[str, Any]] | list[Any], *, max_message_chars: int = _DEFAULT_MAX_MESSAGE_CHARS) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in turns or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _normalize_text(item.get("content"), max_chars=max_message_chars)
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def normalize_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    normalized: dict[str, Any] = {}
    short_summary = " ".join(str(summary.get("short_summary") or "").split()).strip()
    if short_summary:
        normalized["short_summary"] = short_summary
    open_threads = [str(item).strip() for item in list(summary.get("open_threads") or []) if str(item).strip()]
    if open_threads:
        normalized["open_threads"] = open_threads
    memory_facts = [str(item).strip() for item in list(summary.get("memory_facts") or []) if str(item).strip()]
    if memory_facts:
        normalized["memory_facts"] = memory_facts
    return normalized


def normalize_conversation_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    normalized: dict[str, Any] = {}
    route = _normalize_text(state.get("last_turn_route"), max_chars=64)
    if route:
        normalized["last_turn_route"] = route
    focus_ids: list[int] = []
    seen_focus_ids: set[int] = set()
    for item in list(state.get("last_focus_file_ids") or []):
        parsed = _safe_positive_int(item)
        if parsed is None or parsed in seen_focus_ids:
            continue
        seen_focus_ids.add(parsed)
        focus_ids.append(parsed)
    if focus_ids:
        normalized["last_focus_file_ids"] = focus_ids
    return normalized


def _normalize_file_context_items(items: list[dict[str, Any]] | list[Any] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        payload: dict[str, Any] = {}
        file_id = _safe_positive_int(item.get("file_id"))
        if file_id is not None:
            payload["file_id"] = file_id
        for key in ("file_type", "file_name", "selected_reason", "source"):
            value = _normalize_text(item.get(key), max_chars=256)
            if value:
                payload[key] = value
        if payload:
            normalized.append(payload)
    return normalized


def normalize_source_selection(
    *,
    source_scope: str | None,
    selected_file_ids: list[int] | list[Any] | None,
    used_files: list[dict[str, Any]] | list[Any] | None,
    execution_files: list[dict[str, Any]] | list[Any] | None,
    primary_file_id: int | None = None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    normalized_source_scope = _normalize_text(source_scope, max_chars=32)
    if normalized_source_scope:
        normalized["source_scope"] = normalized_source_scope
    normalized_selected_ids: list[int] = []
    seen_selected_ids: set[int] = set()
    for item in list(selected_file_ids or []):
        parsed = _safe_positive_int(item)
        if parsed is None or parsed in seen_selected_ids:
            continue
        seen_selected_ids.add(parsed)
        normalized_selected_ids.append(parsed)
    normalized["selected_file_ids"] = normalized_selected_ids
    normalized_used_files = _normalize_file_context_items(used_files)
    normalized_execution_files = _normalize_file_context_items(execution_files)
    if normalized_used_files:
        normalized["used_files"] = normalized_used_files
    if normalized_execution_files:
        normalized["execution_files"] = normalized_execution_files
    normalized_primary_file_id = _safe_positive_int(primary_file_id)
    if normalized_primary_file_id is not None:
        normalized["primary_file_id"] = normalized_primary_file_id
    return normalized


def _message_signature(turn: dict[str, str]) -> tuple[str, str]:
    return (str(turn.get("role") or ""), str(turn.get("content") or ""))


def _find_overlap_length(*, authority_turns: list[dict[str, str]], request_turns: list[dict[str, str]]) -> int:
    if not authority_turns or not request_turns:
        return 0
    max_overlap = min(len(authority_turns), len(request_turns))
    for overlap in range(max_overlap, 0, -1):
        authority_suffix = [_message_signature(item) for item in authority_turns[-overlap:]]
        request_prefix = [_message_signature(item) for item in request_turns[:overlap]]
        if authority_suffix == request_prefix:
            return overlap
    return 0


def merge_recent_turns(
    *,
    current_question: str,
    request_chat_history: list[dict[str, Any]] | list[Any],
    authority_chat_history: list[dict[str, Any]] | list[Any],
    max_recent_turns: int = _DEFAULT_MAX_RECENT_TURNS,
    max_total_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
    max_message_chars: int = _DEFAULT_MAX_MESSAGE_CHARS,
) -> list[dict[str, str]]:
    authority_turns = normalize_turns(authority_chat_history, max_message_chars=max_message_chars)
    request_turns = normalize_turns(request_chat_history, max_message_chars=max_message_chars)
    overlap = _find_overlap_length(authority_turns=authority_turns, request_turns=request_turns)
    merged = list(authority_turns)
    merged.extend(request_turns[overlap:])

    normalized_question = _normalize_text(current_question, max_chars=max_message_chars)
    if merged and normalized_question and merged[-1]["role"] == "user" and merged[-1]["content"] == normalized_question:
        merged = merged[:-1]

    recent = list(merged[-max_recent_turns:])
    budgeted: list[dict[str, str]] = []
    total_chars = 0
    for item in reversed(recent):
        content = str(item.get("content") or "")
        next_total = total_chars + len(content)
        if budgeted and next_total > max_total_chars:
            break
        budgeted.append(item)
        total_chars = next_total
    budgeted.reverse()
    return budgeted


def normalize_conversation_context(
    *,
    recent_turns_for_llm: list[dict[str, Any]] | list[Any] | None,
    summary_for_llm: dict[str, Any] | None,
    conversation_state: dict[str, Any] | None,
    source_selection: dict[str, Any] | None,
    max_message_chars: int = _DEFAULT_MAX_MESSAGE_CHARS,
) -> dict[str, Any]:
    normalized = {
        "recent_turns_for_llm": normalize_turns(list(recent_turns_for_llm or []), max_message_chars=max_message_chars),
        "summary_for_llm": normalize_summary(summary_for_llm),
        "conversation_state": normalize_conversation_state(conversation_state),
        "source_selection": normalize_source_selection(
            source_scope=(source_selection or {}).get("source_scope") if isinstance(source_selection, dict) else "",
            selected_file_ids=(source_selection or {}).get("selected_file_ids") if isinstance(source_selection, dict) else [],
            used_files=(source_selection or {}).get("used_files") if isinstance(source_selection, dict) else [],
            execution_files=(source_selection or {}).get("execution_files") if isinstance(source_selection, dict) else [],
            primary_file_id=(source_selection or {}).get("primary_file_id") if isinstance(source_selection, dict) else None,
        ),
    }
    return normalized


def build_conversation_context(
    *,
    current_question: str,
    request_chat_history: list[dict[str, Any]] | list[Any],
    authority_chat_history: list[dict[str, Any]] | list[Any],
    authority_summary: dict[str, Any] | None,
    authority_conversation_state: dict[str, Any] | None,
    source_scope: str | None,
    selected_file_ids: list[int] | list[Any] | None,
    used_files: list[dict[str, Any]] | list[Any] | None,
    execution_files: list[dict[str, Any]] | list[Any] | None,
    primary_file_id: int | None = None,
    max_recent_turns: int = _DEFAULT_MAX_RECENT_TURNS,
    max_total_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
    max_message_chars: int = _DEFAULT_MAX_MESSAGE_CHARS,
) -> dict[str, Any]:
    return {
        "recent_turns_for_llm": merge_recent_turns(
            current_question=current_question,
            request_chat_history=request_chat_history,
            authority_chat_history=authority_chat_history,
            max_recent_turns=max_recent_turns,
            max_total_chars=max_total_chars,
            max_message_chars=max_message_chars,
        ),
        "summary_for_llm": normalize_summary(authority_summary),
        "conversation_state": normalize_conversation_state(authority_conversation_state),
        "source_selection": normalize_source_selection(
            source_scope=source_scope,
            selected_file_ids=selected_file_ids,
            used_files=used_files,
            execution_files=execution_files,
            primary_file_id=primary_file_id,
        ),
    }


__all__ = [
    "build_conversation_context",
    "merge_recent_turns",
    "normalize_conversation_context",
    "normalize_conversation_state",
    "normalize_source_selection",
    "normalize_summary",
    "normalize_turns",
]
