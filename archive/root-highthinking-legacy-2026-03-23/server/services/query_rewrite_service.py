"""Lightweight multi-turn question rewrite helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
from dataclasses import dataclass
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
    )
]

_SUMMARY_MAX_AGE_HOURS = max(1, int(str(os.getenv("MULTITURN_SUMMARY_MAX_AGE_HOURS", "72") or "72").strip() or "72"))

_FOLLOWUP_PREFIX_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^那",
        r"^那么",
        r"^然后",
        r"^再",
        r"^继续",
        r"^关于这个",
        r"^关于它",
    )
]


@dataclass(frozen=True)
class QuestionRewriteResult:
    raw_question: str
    effective_question: str
    rewrite_applied: bool
    rewrite_reason: str = ""
    anchor_text: str = ""


def _normalize_text(value: Any, *, max_chars: int = 4000) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    return text[:max_chars]


def _needs_contextual_rewrite(question: str) -> bool:
    text = _normalize_text(question)
    if not text:
        return False
    has_ambiguous_reference = any(pattern.search(text) for pattern in _AMBIGUOUS_PATTERNS)
    if has_ambiguous_reference:
        return True
    if len(text) <= 12:
        return any(pattern.search(text) for pattern in _FOLLOWUP_PREFIX_PATTERNS)
    return False


def _is_good_anchor(text: str) -> bool:
    normalized = _normalize_text(text, max_chars=120)
    if not normalized:
        return False
    if len(normalized) < 4:
        return False
    if any(pattern.search(normalized) for pattern in _AMBIGUOUS_PATTERNS):
        return False
    return True


def _summary_is_fresh(summary: dict[str, Any] | None) -> bool:
    payload = summary if isinstance(summary, dict) else {}
    updated_at = _normalize_text(payload.get("updated_at"), max_chars=64)
    if not updated_at:
        return True
    try:
        parsed = datetime.fromisoformat(updated_at)
    except Exception:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return parsed >= now - timedelta(hours=_SUMMARY_MAX_AGE_HOURS)


def _extract_anchor(*, recent_turns: list[dict[str, str]], summary: dict[str, Any] | None = None) -> str:
    summary_payload = summary if isinstance(summary, dict) else {}
    user_candidates: list[str] = []
    assistant_candidates: list[str] = []
    for item in reversed(recent_turns or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        candidate = _normalize_text(item.get("content"), max_chars=120)
        if candidate:
            if role == "user":
                user_candidates.append(candidate)
            elif role == "assistant":
                assistant_candidates.append(candidate)
    for candidate in user_candidates:
        if _is_good_anchor(candidate):
            return candidate
    for candidate in assistant_candidates:
        if _is_good_anchor(candidate):
            return candidate
    if _summary_is_fresh(summary_payload):
        for key in ("recent_focus", "topic", "user_goal"):
            candidate = _normalize_text(summary_payload.get(key), max_chars=120)
            if candidate:
                return candidate
    if user_candidates:
        return user_candidates[0]
    if assistant_candidates:
        return assistant_candidates[0]
    return ""


def rewrite_question(
    *,
    raw_question: str,
    recent_turns: list[dict[str, str]] | None = None,
    summary: dict[str, Any] | None = None,
) -> QuestionRewriteResult:
    question = _normalize_text(raw_question)
    summary_payload = summary if isinstance(summary, dict) else {}
    if not question:
        return QuestionRewriteResult(
            raw_question="",
            effective_question="",
            rewrite_applied=False,
            rewrite_reason="empty_question",
        )
    if not _needs_contextual_rewrite(question):
        return QuestionRewriteResult(
            raw_question=question,
            effective_question=question,
            rewrite_applied=False,
            rewrite_reason="self_contained",
        )

    anchor = _extract_anchor(recent_turns=list(recent_turns or []), summary=summary_payload)
    if not anchor:
        return QuestionRewriteResult(
            raw_question=question,
            effective_question=question,
            rewrite_applied=False,
            rewrite_reason="no_context_anchor",
        )

    effective = f"结合前文关于“{anchor}”的上下文，回答这个问题：{question}"
    return QuestionRewriteResult(
        raw_question=question,
        effective_question=effective,
        rewrite_applied=effective != question,
        rewrite_reason="contextual_reference",
        anchor_text=anchor,
    )


__all__ = ["QuestionRewriteResult", "rewrite_question"]
