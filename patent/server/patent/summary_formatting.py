from __future__ import annotations

import re
from typing import Literal


LITERATURE_SUMMARY_NOTE = "注*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。"

PRIMARY_SUMMARY_HEADINGS = (
    "研究目的和背景",
    "研究方法/实验设计",
    "主要发现和结果",
    "结论和意义",
)

LEGACY_FOUR_BLOCK_HEADINGS = (
    "结论",
    "证据",
    "对比",
    "限制",
)

DEGRADED_MARKERS = (
    "found no matching results",
    "未拿到可读",
    "未找到可用的知识库",
    "未找到匹配",
    "无法生成",
    "请稍后重试",
    "文件不可读",
    "暂时无法",
)

_WHITESPACE_PATTERN = re.compile(r"\s+")
_MARKDOWN_PREFIX_PATTERN = re.compile(r"^[#>\-\*\d\.\)\s]+")


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", str(value or "")).strip()


def _heading_pattern(heading: str) -> re.Pattern[str]:
    escaped = re.escape(heading)
    return re.compile(
        rf"(^|\n)\s*(?:#{{1,6}}\s*{escaped}\s*[：:]?\s*$|{escaped}\s*[：:]?\s*$)",
        flags=re.MULTILINE,
    )


def _has_heading(text: str, heading: str) -> bool:
    return _heading_pattern(heading).search(str(text or "")) is not None


def _extract_section_body(text: str, heading: str) -> str:
    normalized = str(text or "")
    matched = _heading_pattern(heading).search(normalized)
    if matched is None:
        return ""
    start = matched.end()
    next_positions = [
        candidate.start()
        for candidate in (
            _heading_pattern(title).search(normalized, start) for title in (*PRIMARY_SUMMARY_HEADINGS, *LEGACY_FOUR_BLOCK_HEADINGS)
        )
        if candidate is not None and candidate.start() >= start
    ]
    end = min(next_positions) if next_positions else len(normalized)
    return normalized[start:end].strip()


def is_degraded_summary_answer(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    return any(marker.lower() in lowered for marker in DEGRADED_MARKERS)


def extract_support_points(text: str, *, max_items: int, min_chars: int) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw_items = re.split(r"(?<=[。！？.!?])(?:\s+)?|\n+", normalized)
    points: list[str] = []
    for item in raw_items:
        line = _collapse_whitespace(_MARKDOWN_PREFIX_PATTERN.sub("", item))
        if len(line) < int(min_chars):
            continue
        if line in points:
            continue
        points.append(line)
        if len(points) >= int(max_items):
            break
    return points


def count_primary_summary_headings(text: str) -> int:
    return sum(1 for heading in PRIMARY_SUMMARY_HEADINGS if _has_heading(text, heading))


def has_legacy_four_block_structure(text: str) -> bool:
    return all(_has_heading(text, heading) for heading in LEGACY_FOUR_BLOCK_HEADINGS)


def _has_usable_primary_section_bodies(text: str) -> bool:
    for heading in PRIMARY_SUMMARY_HEADINGS:
        body = _extract_section_body(text, heading)
        if not extract_support_points(body, max_items=8, min_chars=10):
            return False
    return True


def classify_summary_answer(
    answer: str,
    *,
    prepared_text: str,
) -> Literal["preserve", "light_repair", "conservative_repair", "fallback"]:
    normalized_answer = str(answer or "").strip()
    if is_degraded_summary_answer(normalized_answer):
        return "fallback"

    if count_primary_summary_headings(normalized_answer) == len(PRIMARY_SUMMARY_HEADINGS) and _has_usable_primary_section_bodies(normalized_answer):
        return "preserve"

    answer_points = extract_support_points(normalized_answer, max_items=8, min_chars=10)
    prepared_points = extract_support_points(prepared_text, max_items=8, min_chars=12)
    heading_count = count_primary_summary_headings(normalized_answer)

    if normalized_answer and (heading_count >= 3 or has_legacy_four_block_structure(normalized_answer)) and len(answer_points) >= 3:
        return "light_repair"

    if normalized_answer and (len(answer_points) >= 2 or len(prepared_points) >= 2):
        return "conservative_repair"

    return "fallback"


__all__ = [
    "DEGRADED_MARKERS",
    "LEGACY_FOUR_BLOCK_HEADINGS",
    "LITERATURE_SUMMARY_NOTE",
    "PRIMARY_SUMMARY_HEADINGS",
    "classify_summary_answer",
    "count_primary_summary_headings",
    "extract_support_points",
    "has_legacy_four_block_structure",
    "is_degraded_summary_answer",
]
