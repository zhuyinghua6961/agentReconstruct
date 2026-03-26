from __future__ import annotations

from typing import Any


_MAX_SHORT_SUMMARY_CHARS = 240
_MAX_OPEN_THREAD_CHARS = 160
_MAX_MEMORY_FACT_CHARS = 180
_MAX_TOPIC_CHARS = 120
_MAX_TURNS = 6
_MAX_MEMORY_FACTS = 3


def _normalize_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    return text[:max_chars]


def _normalize_turns(turns: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in list(turns or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _normalize_text(item.get("content"), max_chars=600)
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized[-_MAX_TURNS:]


def _compose_short_summary(*, topic: str, latest_user: str, latest_assistant: str, latest_role: str) -> str:
    if latest_role == "assistant":
        if topic and latest_assistant and topic != latest_assistant:
            summary = f"主题：{topic}；最新结论：{latest_assistant}"
        else:
            summary = latest_assistant or topic
    else:
        if topic and latest_user and topic != latest_user:
            summary = f"主题：{topic}；当前问题：{latest_user}"
        elif topic:
            summary = f"主题：{topic}"
        else:
            summary = latest_user or topic
    return _normalize_text(summary, max_chars=_MAX_SHORT_SUMMARY_CHARS)


def build_authority_summary(*, recent_turns: list[dict[str, Any]] | None) -> dict[str, Any]:
    turns = _normalize_turns(recent_turns)
    if not turns:
        return {
            "short_summary": "",
            "memory_facts": [],
            "open_threads": [],
        }

    user_turns = [item["content"] for item in turns if item["role"] == "user"]
    assistant_turns = [item["content"] for item in turns if item["role"] == "assistant"]
    latest_turn = turns[-1]
    topic = _normalize_text((user_turns[0] if user_turns else latest_turn["content"]), max_chars=_MAX_TOPIC_CHARS)
    latest_user = _normalize_text((user_turns[-1] if user_turns else ""), max_chars=_MAX_OPEN_THREAD_CHARS)
    latest_assistant = _normalize_text((assistant_turns[-1] if assistant_turns else ""), max_chars=_MAX_MEMORY_FACT_CHARS)

    memory_facts: list[str] = []
    seen: set[str] = set()
    for text in reversed(assistant_turns):
        normalized = _normalize_text(text, max_chars=_MAX_MEMORY_FACT_CHARS)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        memory_facts.append(normalized)
        if len(memory_facts) >= _MAX_MEMORY_FACTS:
            break
    memory_facts.reverse()

    open_threads: list[str] = []
    if latest_turn["role"] == "user" and latest_user:
        open_threads.append(latest_user)

    return {
        "short_summary": _compose_short_summary(
            topic=topic,
            latest_user=latest_user,
            latest_assistant=latest_assistant,
            latest_role=latest_turn["role"],
        ),
        "memory_facts": memory_facts,
        "open_threads": open_threads,
    }


__all__ = ["build_authority_summary"]
