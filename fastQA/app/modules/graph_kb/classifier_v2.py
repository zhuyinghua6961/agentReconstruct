from __future__ import annotations

import re
from typing import Any

from app.modules.graph_kb.client import plan_graph_kb_query
from app.modules.graph_kb.models import SemanticDecision


_FILE_ROUTE_HINTS = {"pdf_qa", "tabular_qa", "hybrid_qa"}
_FOLLOWUP_HINTS = ("它", "这个", "那篇", "前者", "后者", "上面那个", "最高的是哪篇")
_NUMERIC_ATTRIBUTES = (
    "压实密度",
    "比容量",
    "容量",
    "电压",
    "倍率",
    "循环性能",
    "循环寿命",
    "粒径",
    "放电容量",
)
_PRECISE_KEYWORDS = ("大于", "小于", "高于", "低于", "超过", "最高", "最低", "最大", "最小", "统计", "top")
_COMMUNITY_KEYWORDS = ("关系网络", "关系", "网络", "社区", "数据质量", "机制关联")
_SEMANTIC_KEYWORDS = ("如何", "为什么", "影响", "方法", "总结", "介绍", "趋势", "稳定")
_GRAPH_NON_NUMERIC_ATTRIBUTES = ("原料", "工艺", "方法", "设备", "配方", "文献", "论文", "测试", "表征", "描述")
_ENUMERATION_HINTS = ("有哪些", "哪些", "列出", "给出", "多少篇", "包含")
_ENTITY_KEYWORDS = ("lfp", "lifepo4", "ncm", "磷酸铁锂", "石墨", "三元")
_ANALYSIS_HINTS = ("分析", "趋势", "对比", "差异", "特点", "稳定", "机制")
_RANKING_PATTERN = re.compile(r"(?:前\s*\d+|排名前|top\s*\d+)", re.IGNORECASE)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_question(question: str) -> str:
    return _text(question).rstrip("？?。.!！")


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in hints)


def _has_numeric_attribute(text: str) -> bool:
    return _contains_any(text, _NUMERIC_ATTRIBUTES)


def _has_precise_keyword(text: str) -> bool:
    return _contains_any(text, _PRECISE_KEYWORDS) or bool(_RANKING_PATTERN.search(text))


def _has_entity_keyword(text: str) -> bool:
    return _contains_any(text, _ENTITY_KEYWORDS)


def _has_graph_non_numeric_attribute(text: str) -> bool:
    return _contains_any(text, _GRAPH_NON_NUMERIC_ATTRIBUTES) and not _has_numeric_attribute(text)


def _has_enumeration_hint(text: str) -> bool:
    return _contains_any(text, _ENUMERATION_HINTS)


def _has_file_context(conversation_context: dict[str, Any] | None) -> bool:
    context = conversation_context or {}
    state = context.get("conversation_state") if isinstance(context.get("conversation_state"), dict) else {}
    source_selection = context.get("source_selection") if isinstance(context.get("source_selection"), dict) else {}
    last_turn_route = _text(state.get("last_turn_route")).lower()
    source_scope = _text(source_selection.get("source_scope")).lower()
    selected_file_ids = list(source_selection.get("selected_file_ids") or [])
    used_files = list(source_selection.get("used_files") or [])
    execution_files = list(source_selection.get("execution_files") or [])
    return (
        last_turn_route in _FILE_ROUTE_HINTS
        or any(token in source_scope for token in ("pdf", "table"))
        or bool(selected_file_ids or used_files or execution_files)
    )


def _legacy_hybrid_rule(text: str) -> bool:
    return _has_numeric_attribute(text) and _has_precise_keyword(text) and _contains_any(text, _ANALYSIS_HINTS)


def _precise_keywords_and_numeric_attributes(text: str) -> bool:
    return _has_numeric_attribute(text) and _has_precise_keyword(text) and _has_entity_keyword(text)


def _community_keywords(text: str) -> bool:
    return _contains_any(text, _COMMUNITY_KEYWORDS)


def _semantic_keywords(text: str) -> bool:
    return _contains_any(text, _SEMANTIC_KEYWORDS)


def _graph_non_numeric_attributes_with_enumeration(text: str) -> bool:
    return _has_graph_non_numeric_attribute(text) and _has_enumeration_hint(text)


def _numeric_attribute_only(text: str) -> bool:
    return _has_numeric_attribute(text) and not _has_entity_keyword(text)


def _entity_keywords(text: str) -> bool:
    return _has_entity_keyword(text)


def map_legacy_route_to_tri_state(
    *,
    legacy_route: str,
    question: str,
    conversation_context: dict[str, Any] | None,
    matched_rule: str,
    standalone: bool,
) -> SemanticDecision:
    legacy_template_plan = plan_graph_kb_query(question)
    if legacy_route == "community":
        mode = "skip_graph"
    elif legacy_route == "precise":
        mode = "direct_answer" if legacy_template_plan is not None else "graph_for_rag"
    elif legacy_route == "hybrid":
        mode = "graph_for_rag"
    else:
        mode = "graph_for_rag" if (_has_entity_keyword(question) or _has_numeric_attribute(question) or _has_graph_non_numeric_attribute(question)) else "skip_graph"

    diagnostics = {
        "matched_rule": matched_rule,
        "legacy_template_id": legacy_template_plan.template_id if legacy_template_plan is not None else "",
    }
    if _has_file_context(conversation_context):
        diagnostics["override"] = "file_context_present"
        diagnostics["requires_context_resolution"] = True
        return SemanticDecision(
            mode="graph_for_rag",
            legacy_route=legacy_route,
            standalone=False,
            diagnostics=diagnostics,
        )

    if any(hint in question for hint in _FOLLOWUP_HINTS):
        diagnostics["override"] = "ambiguous_followup"
        diagnostics["requires_context_resolution"] = True
        return SemanticDecision(
            mode="graph_for_rag",
            legacy_route=legacy_route,
            standalone=False,
            diagnostics=diagnostics,
        )

    return SemanticDecision(
        mode=mode,
        legacy_route=legacy_route,
        standalone=standalone,
        diagnostics=diagnostics,
    )


def classify_graph_question_v2(*, question: str, conversation_context: dict[str, Any] | None = None) -> SemanticDecision:
    text = _normalized_question(question)
    standalone = not bool(conversation_context)

    if _legacy_hybrid_rule(text):
        legacy_route = "hybrid"
        matched_rule = "hybrid_rule"
    elif _precise_keywords_and_numeric_attributes(text):
        legacy_route = "precise"
        matched_rule = "precise_keywords_and_numeric_attributes"
    elif _community_keywords(text):
        legacy_route = "community"
        matched_rule = "community_keywords"
    elif _semantic_keywords(text):
        legacy_route = "semantic"
        matched_rule = "semantic_keywords"
    elif plan_graph_kb_query(text) is not None:
        legacy_route = "precise"
        matched_rule = "legacy_template_signal"
    elif _graph_non_numeric_attributes_with_enumeration(text):
        legacy_route = "precise"
        matched_rule = "graph_enumeration"
    elif _numeric_attribute_only(text):
        legacy_route = "precise"
        matched_rule = "numeric_attribute_only"
    elif _entity_keywords(text):
        legacy_route = "precise"
        matched_rule = "entity_keywords"
    else:
        legacy_route = "semantic"
        matched_rule = "default_semantic"

    return map_legacy_route_to_tri_state(
        legacy_route=legacy_route,
        question=text,
        conversation_context=conversation_context,
        matched_rule=matched_rule,
        standalone=standalone,
    )
