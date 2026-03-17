"""Lightweight conversation summary helpers for multi-turn context."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


_AMBIGUOUS_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(^|[\s，。？！,?.!])它",
        r"(^|[\s，。？！,?.!])这个",
        r"(^|[\s，。？！,?.!])那个",
        r"(^|[\s，。？！,?.!])这些",
        r"(^|[\s，。？！,?.!])那些",
        r"前者",
        r"后者",
        r"上面",
        r"上述",
        r"两者",
        r"该材料",
        r"该方案",
        r"其",
        r"^那",
        r"^那么",
    )
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _normalize_text(value: Any, *, max_chars: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    return text[:max_chars]


def _normalize_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    payload = summary if isinstance(summary, dict) else {}
    known_facts_raw = payload.get("known_facts")
    known_facts = known_facts_raw if isinstance(known_facts_raw, list) else []
    open_questions_raw = payload.get("open_questions")
    open_questions = open_questions_raw if isinstance(open_questions_raw, list) else []
    entities_raw = payload.get("entities")
    entities = entities_raw if isinstance(entities_raw, list) else []
    return {
        "topic": _normalize_text(payload.get("topic"), max_chars=120),
        "entities": [_normalize_text(item, max_chars=80) for item in entities if _normalize_text(item, max_chars=80)],
        "known_facts": [
            _normalize_text(item, max_chars=180)
            for item in known_facts
            if _normalize_text(item, max_chars=180)
        ][:3],
        "user_goal": _normalize_text(payload.get("user_goal"), max_chars=120),
        "open_questions": [
            _normalize_text(item, max_chars=160)
            for item in open_questions
            if _normalize_text(item, max_chars=160)
        ][:3],
        "recent_focus": _normalize_text(payload.get("recent_focus"), max_chars=120),
        "updated_at": _normalize_text(payload.get("updated_at"), max_chars=64),
    }


def _normalize_turns(messages: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _normalize_text(item.get("content"), max_chars=600)
        if not content:
            continue
        result.append({"role": role, "content": content})
    return result


def _looks_like_self_contained_topic(text: str) -> bool:
    normalized = _normalize_text(text, max_chars=160)
    if len(normalized) < 8:
        return False
    return not any(pattern.search(normalized) for pattern in _AMBIGUOUS_PATTERNS)


def build_conversation_summary(
    *,
    messages: list[dict[str, Any]] | None,
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = _normalize_summary(previous_summary)
    turns = _normalize_turns(messages)
    if not turns:
        if previous:
            previous["updated_at"] = _now_iso()
            return previous
        return {}

    recent_turns = turns[-6:]
    user_turns = [item["content"] for item in recent_turns if item["role"] == "user"]
    assistant_turns = [item["content"] for item in recent_turns if item["role"] == "assistant"]
    latest_turn = recent_turns[-1]
    latest_user = user_turns[-1] if user_turns else ""
    latest_assistant = assistant_turns[-1] if assistant_turns else ""
    first_user = user_turns[0] if user_turns else latest_turn["content"]

    topic_reset = bool(latest_user) and _looks_like_self_contained_topic(latest_user)

    known_facts: list[str] = []
    seen_facts: set[str] = set()
    for text in reversed(assistant_turns):
        normalized = _normalize_text(text, max_chars=180)
        key = normalized.lower()
        if not normalized or key in seen_facts:
            continue
        seen_facts.add(key)
        known_facts.append(normalized)
        if len(known_facts) >= 3:
            break
    known_facts.reverse()

    recent_focus = latest_turn["content"] or latest_user or latest_assistant or previous.get("recent_focus") or ""
    if topic_reset:
        topic = latest_user
    else:
        topic = previous.get("topic") or first_user or recent_focus
    user_goal = latest_user or previous.get("user_goal") or topic

    open_questions: list[str] = []
    if latest_turn["role"] == "user":
        open_questions.append(_normalize_text(latest_turn["content"], max_chars=160))
        if topic_reset:
            known_facts = []

    return {
        "topic": _normalize_text(topic, max_chars=120),
        "entities": previous.get("entities") if isinstance(previous.get("entities"), list) and not topic_reset else [],
        "known_facts": known_facts,
        "user_goal": _normalize_text(user_goal, max_chars=120),
        "open_questions": open_questions,
        "recent_focus": _normalize_text(recent_focus, max_chars=120),
        "updated_at": _now_iso(),
    }


__all__ = ["build_conversation_summary"]
