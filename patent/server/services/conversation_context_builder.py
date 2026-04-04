from __future__ import annotations

import os
from typing import Any

from server.schemas.request_models import PatentAskRequest


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


_DEFAULT_MAX_RECENT_TURNS = _env_int("PATENT_CONTEXT_RECENT_TURNS", 8, minimum=1, maximum=20)
_DEFAULT_MAX_MESSAGE_CHARS = _env_int("PATENT_CONTEXT_MESSAGE_MAX_CHARS", 800, minimum=50, maximum=4000)
_DEFAULT_MAX_TOTAL_CHARS = _env_int("PATENT_CONTEXT_TOTAL_MAX_CHARS", 4000, minimum=200, maximum=20000)


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
    open_threads = [str(item).strip() for item in list(summary.get("open_threads") or []) if str(item or "").strip()]
    if open_threads:
        normalized["open_threads"] = open_threads
    memory_facts = [str(item).strip() for item in list(summary.get("memory_facts") or []) if str(item or "").strip()]
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
    seen: set[int] = set()
    for item in list(state.get("last_focus_file_ids") or []):
        parsed = _safe_positive_int(item)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        focus_ids.append(parsed)
    if focus_ids:
        normalized["last_focus_file_ids"] = focus_ids
    return normalized


def _normalize_source_scope(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"kb", "pdf", "table", "pdf+kb", "table+kb", "pdf+table", "pdf+table+kb"}:
        return normalized
    return "kb"


def _normalize_source_selection(
    *,
    source_scope: Any,
    selected_file_ids: list[int] | list[Any] | None,
) -> dict[str, Any]:
    selected_ids: list[int] = []
    seen: set[int] = set()
    for item in list(selected_file_ids or []):
        parsed = _safe_positive_int(item)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        selected_ids.append(parsed)
    return {
        "source_scope": _normalize_source_scope(source_scope),
        "selected_file_ids": selected_ids,
    }


def normalize_source_selection(*, request: PatentAskRequest) -> dict[str, Any]:
    return _normalize_source_selection(
        source_scope=request.source_scope,
        selected_file_ids=request.selected_file_ids,
    )


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


def normalize_patent_conversation_context(
    *,
    recent_turns_for_llm: list[dict[str, Any]] | list[Any] | None,
    summary_for_llm: dict[str, Any] | None,
    conversation_state: dict[str, Any] | None,
    source_selection: dict[str, Any] | None,
    max_message_chars: int = _DEFAULT_MAX_MESSAGE_CHARS,
) -> dict[str, Any]:
    return {
        "recent_turns_for_llm": normalize_turns(list(recent_turns_for_llm or []), max_message_chars=max_message_chars),
        "summary_for_llm": normalize_summary(summary_for_llm),
        "conversation_state": normalize_conversation_state(conversation_state),
        "source_selection": _normalize_source_selection(
            source_scope=(source_selection or {}).get("source_scope") if isinstance(source_selection, dict) else "kb",
            selected_file_ids=(source_selection or {}).get("selected_file_ids") if isinstance(source_selection, dict) else [],
        ),
    }


def build_patent_conversation_context(
    *,
    request: PatentAskRequest,
    raw_context: dict[str, Any] | None,
    max_recent_turns: int = _DEFAULT_MAX_RECENT_TURNS,
    max_total_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
    max_message_chars: int = _DEFAULT_MAX_MESSAGE_CHARS,
) -> dict[str, Any]:
    context = dict(raw_context or {})
    return {
        "recent_turns_for_llm": merge_recent_turns(
            current_question=request.question,
            request_chat_history=request.chat_history,
            authority_chat_history=list(context.get("chat_history") or []),
            max_recent_turns=max_recent_turns,
            max_total_chars=max_total_chars,
            max_message_chars=max_message_chars,
        ),
        "summary_for_llm": normalize_summary(context.get("summary")),
        "conversation_state": normalize_conversation_state(context.get("conversation_state")),
        "source_selection": normalize_source_selection(request=request),
    }


__all__ = [
    "build_patent_conversation_context",
    "merge_recent_turns",
    "normalize_conversation_state",
    "normalize_patent_conversation_context",
    "normalize_source_selection",
    "normalize_summary",
    "normalize_turns",
]
