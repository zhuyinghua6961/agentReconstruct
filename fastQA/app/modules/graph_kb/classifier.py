from __future__ import annotations

import re
from typing import Any

from app.modules.graph_kb.models import GraphKbDecision


_BROAD_HINTS = (
    "为什么",
    "如何",
    "意义",
    "总结",
    "介绍",
    "综述",
    "机制",
    "趋势",
    "方法对比",
)
_FOLLOWUP_HINTS = ("它", "这个", "那篇", "前者", "后者", "上面那个", "最高的是哪篇")
_DOI_PATTERN = re.compile(r"10\.\d+/[A-Za-z0-9._\-()/]+", re.IGNORECASE)
_LIST_PATTERN = re.compile(r"^(?:请)?有哪些关于(?P<keyword>[A-Za-z0-9\u4e00-\u9fff\-+/().]+)的(?:文献|论文)$")
_COUNT_PATTERN = re.compile(r"^(?:请)?(?P<keyword>[A-Za-z0-9\u4e00-\u9fff\-+/().]+)有多少篇(?:文献|论文)$")
_RAW_MATERIAL_PATTERNS = (
    re.compile(r"^(?:请)?(?:有哪些|哪些)(?:使用|用了|以)(?P<keyword>[A-Za-z0-9\u4e00-\u9fff\-+/().]+?)(?:作为)?原料的(?:文献|论文)$"),
    re.compile(r"^(?:请)?(?:有哪些|哪些)(?:文献|论文)(?:使用|用了|以)(?P<keyword>[A-Za-z0-9\u4e00-\u9fff\-+/().]+?)(?:作为)?原料$"),
)
_PROPERTY_FILTER_HINTS = (
    "压实密度",
    "比容量",
    "电压",
    "容量",
    "倍率",
    "循环",
    "大于",
    "小于",
    "高于",
    "低于",
    "超过",
    "最高",
    "最低",
)
_RANKING_PATTERN = re.compile(r"(?:前\s*\d+|排名前|top\s*\d+)", re.IGNORECASE)
_DOI_CONTEXT_HINTS = {
    "testing": ("测试", "实验", "表征"),
    "process": ("工艺", "制备", "方法", "流程", "步骤"),
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_question(value: Any) -> str:
    return _text(value).rstrip("？?。.!！")


def _looks_like_property_filter(text: str) -> bool:
    normalized = _text(text)
    return any(hint in normalized for hint in _PROPERTY_FILTER_HINTS) or bool(_RANKING_PATTERN.search(normalized))


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _matches_raw_material_listing(text: str) -> str:
    for pattern in _RAW_MATERIAL_PATTERNS:
        match = pattern.fullmatch(text)
        if match is not None:
            return _text(match.group("keyword"))
    return ""


def classify_graph_kb_question(question: str, *, conversation_context: dict[str, Any] | None = None) -> GraphKbDecision:
    text = _normalized_question(question)
    lower = text.lower()
    context = conversation_context or {}
    state = context.get("conversation_state") if isinstance(context.get("conversation_state"), dict) else {}
    source_selection = context.get("source_selection") if isinstance(context.get("source_selection"), dict) else {}
    last_turn_route = _text(state.get("last_turn_route")).lower()
    source_scope = _text(source_selection.get("source_scope")).lower()
    selected_file_ids = list(source_selection.get("selected_file_ids") or [])
    used_files = list(source_selection.get("used_files") or [])
    execution_files = list(source_selection.get("execution_files") or [])

    if last_turn_route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
        return GraphKbDecision(decision="skip", reason="file_context_present", standalone=False, signals=("last_turn_file_route",))
    if any(token in source_scope for token in ("pdf", "table")) or selected_file_ids or used_files or execution_files:
        return GraphKbDecision(decision="skip", reason="file_context_present", standalone=False, signals=("source_selection_file_context",))

    if any(hint in text for hint in _FOLLOWUP_HINTS):
        return GraphKbDecision(decision="skip", reason="ambiguous_followup", standalone=False, signals=("followup_hint",))

    if any(hint in text for hint in _BROAD_HINTS):
        return GraphKbDecision(decision="skip", reason="broad_semantic_question", standalone=True, signals=("broad_hint",))

    raw_material_keyword = _matches_raw_material_listing(text)
    if raw_material_keyword and not _looks_like_property_filter(raw_material_keyword):
        return GraphKbDecision(decision="try_graph", reason="raw_material_listing", standalone=True, signals=("raw_material_hint",))

    has_doi = bool(_DOI_PATTERN.search(text) or "doi" in lower)
    if has_doi and (
        _contains_any(text, _DOI_CONTEXT_HINTS["testing"])
        or _contains_any(text, _DOI_CONTEXT_HINTS["process"])
    ):
        return GraphKbDecision(decision="try_graph", reason="doi_context_lookup", standalone=True, signals=("doi_context_hint",))

    if has_doi:
        return GraphKbDecision(decision="try_graph", reason="doi_lookup", standalone=True, signals=("doi_hint",))

    list_match = _LIST_PATTERN.fullmatch(text)
    count_match = _COUNT_PATTERN.fullmatch(text)
    keyword = ""
    if list_match is not None:
        keyword = _text(list_match.group("keyword"))
    elif count_match is not None:
        keyword = _text(count_match.group("keyword"))
    if keyword and not _looks_like_property_filter(keyword):
        return GraphKbDecision(decision="try_graph", reason="literature_listing", standalone=True, signals=("literature_hint",))

    return GraphKbDecision(decision="skip", reason="no_graph_signal", standalone=True, signals=("default_skip",))
