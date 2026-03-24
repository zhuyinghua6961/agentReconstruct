"""Conversation context preparation for multi-turn ask execution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from server.schemas.request_models import AskRequest
from server.services import chat_persistence


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


_MAX_RECENT_MESSAGES = _env_int("MULTITURN_RECENT_MESSAGES", 8, minimum=1, maximum=20)
_MAX_MESSAGE_CHARS = _env_int("MULTITURN_MESSAGE_MAX_CHARS", 800, minimum=50, maximum=4000)
_MAX_TOTAL_CHARS = _env_int("MULTITURN_TOTAL_MAX_CHARS", 4000, minimum=200, maximum=20000)

_SUMMARY_EXCLUDED_KEYS = {"steps", "timings", "file_selection", "source_usage", "trace_id"}


@dataclass(frozen=True)
class ConversationContext:
    raw_question: str
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    conversation_id: int | None = None
    user_id: int | None = None


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _normalize_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    return text[:max_chars]


def _normalize_turns(raw_turns: list[dict[str, Any]] | list[Any]) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for item in raw_turns or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _normalize_text(item.get("content"), max_chars=_MAX_MESSAGE_CHARS)
        if not content:
            continue
        turns.append({"role": role, "content": content})
    return turns


def _message_signature(turn: dict[str, str]) -> tuple[str, str]:
    return (str(turn.get("role") or ""), str(turn.get("content") or ""))


def _find_overlap_length(*, server_turns: list[dict[str, str]], request_turns: list[dict[str, str]]) -> int:
    if not server_turns or not request_turns:
        return 0
    max_overlap = min(len(server_turns), len(request_turns))
    for overlap in range(max_overlap, 0, -1):
        server_suffix = [_message_signature(item) for item in server_turns[-overlap:]]
        request_prefix = [_message_signature(item) for item in request_turns[:overlap]]
        if server_suffix == request_prefix:
            return overlap
    return 0


def _merge_turns(*, server_turns: list[dict[str, str]], request_turns: list[dict[str, str]]) -> list[dict[str, str]]:
    overlap = _find_overlap_length(server_turns=server_turns, request_turns=request_turns)
    merged = list(server_turns)
    merged.extend(request_turns[overlap:])
    return merged


def _apply_history_budget(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    if not turns:
        return []
    recent = list(turns[-_MAX_RECENT_MESSAGES:])
    budgeted: list[dict[str, str]] = []
    total_chars = 0
    for item in reversed(recent):
        content = str(item.get("content") or "")
        next_total = total_chars + len(content)
        if budgeted and next_total > _MAX_TOTAL_CHARS:
            break
        budgeted.append(item)
        total_chars = next_total
    budgeted.reverse()
    return budgeted


def _sanitize_summary(summary: dict[str, Any] | Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in summary.items():
        key_text = str(key or "").strip()
        if not key_text or key_text in _SUMMARY_EXCLUDED_KEYS:
            continue
        sanitized[key_text] = value
    short_summary = _normalize_text(sanitized.get("short_summary"), max_chars=1200)
    if short_summary:
        sanitized["short_summary"] = short_summary
        sanitized.setdefault("recent_focus", short_summary)
    return sanitized


def sanitize_conversation_context(context: ConversationContext) -> ConversationContext:
    return ConversationContext(
        raw_question=_normalize_text(context.raw_question, max_chars=4000),
        recent_turns=_apply_history_budget(_normalize_turns(list(context.recent_turns or []))),
        summary=_sanitize_summary(context.summary),
        conversation_id=_safe_int(context.conversation_id),
        user_id=_safe_int(context.user_id),
    )


def _load_server_context_snapshot(*, request: AskRequest, user_id: int | None, conversation_id: int | None) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if user_id is None or conversation_id is None:
        return [], {}
    try:
        context = chat_persistence.load_conversation_context(
            user_id=user_id,
            conversation_id=conversation_id,
            trace_id=str(request.trace_id or ""),
            route=str(request.route or "thinking_qa"),
            requested_mode=str(request.requested_mode or request.mode or "thinking"),
            actual_mode=str(request.actual_mode or request.mode or "thinking"),
            payload=None,
        )
    except Exception:
        return [], {}
    if not isinstance(context, dict):
        return [], {}
    messages = context.get("chat_history") if isinstance(context.get("chat_history"), list) else []
    summary = context.get("summary")
    return _normalize_turns(messages), dict(summary) if isinstance(summary, dict) else {}


def build_conversation_context(*, request: AskRequest) -> ConversationContext:
    raw_question = _normalize_text(request.question, max_chars=4000)
    user_id = _safe_int(request.user_id)
    conversation_id = _safe_int(request.conversation_id)

    server_turns, server_summary = _load_server_context_snapshot(request=request, user_id=user_id, conversation_id=conversation_id)
    request_turns = _normalize_turns(list(request.chat_history or []))
    merged_turns = _merge_turns(server_turns=server_turns, request_turns=request_turns)
    if merged_turns and merged_turns[-1]["role"] == "user" and merged_turns[-1]["content"] == raw_question:
        merged_turns = merged_turns[:-1]

    return sanitize_conversation_context(
        ConversationContext(
            raw_question=raw_question,
            recent_turns=_apply_history_budget(merged_turns),
            summary=server_summary,
            conversation_id=conversation_id,
            user_id=user_id,
        )
    )


__all__ = ["ConversationContext", "build_conversation_context", "sanitize_conversation_context"]
