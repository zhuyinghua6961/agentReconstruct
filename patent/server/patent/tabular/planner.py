from __future__ import annotations

import re
from typing import Any

from server.patent.tabular.schema_profiler import normalize_identifier

_SUMMARY_KEYWORDS = ("分析", "总结", "概述", "概括", "特点", "规律", "整体", "总体", "异常")
_COMPARE_KEYWORDS = ("比较", "对比", "差异", "区别")
_LOOKUP_KEYWORDS = ("多少", "是什么", "which", "what")
_AGGREGATE_MEAN_KEYWORDS = ("均值", "平均", "average", "mean")
_AGGREGATE_SUM_KEYWORDS = ("总和", "合计", "sum")
_AGGREGATE_COUNT_KEYWORDS = ("数量", "几条", "多少个", "count")
_GROUPED_AGGREGATE_KEYWORDS = ("统计", "均值", "平均", "总和", "合计", "sum", "count")


def _contains_any(question: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in question for keyword in keywords)


def _pick_sheet(profile: dict[str, Any], question: str) -> dict[str, Any] | None:
    sheets = [sheet for sheet in (profile.get("sheets") or []) if isinstance(sheet, dict)]
    if not sheets:
        return None
    normalized_question = normalize_identifier(question)
    for sheet in sheets:
        if normalize_identifier(str(sheet.get("sheet_name") or "")) in normalized_question:
            return sheet
    return sheets[0]


def _pick_metric_columns(question: str, sheet: dict[str, Any], *, allow_fallback: bool = True) -> list[str]:
    numeric_columns = [str(item) for item in (sheet.get("numeric_columns") or []) if str(item)]
    normalized_question = normalize_identifier(question)
    matched = [column for column in numeric_columns if normalize_identifier(column) in normalized_question]
    if matched:
        return matched
    if not allow_fallback:
        return []
    return numeric_columns[:1]


def _pick_focus_columns(
    question: str,
    sheet: dict[str, Any],
    *,
    filters: list[dict[str, str]],
    group_by: str,
    metric_columns: list[str],
) -> list[str]:
    columns = [str(item) for item in (sheet.get("column_names") or []) if str(item)]
    normalized_question = normalize_identifier(question)
    selected: list[str] = []

    def add(column_name: str) -> None:
        normalized = str(column_name or "").strip()
        if normalized and normalized in columns and normalized not in selected:
            selected.append(normalized)

    for column in columns:
        if normalize_identifier(column) in normalized_question:
            add(column)

    for item in filters:
        add(str(item.get("column") or ""))

    add(group_by)

    matched_metric_columns = [
        column for column in metric_columns if normalize_identifier(column) in normalized_question
    ]
    for column in matched_metric_columns:
        add(column)

    return selected


def _pick_group_by(question: str, sheet: dict[str, Any]) -> str:
    candidate_columns = [
        *[str(item) for item in (sheet.get("text_columns") or []) if str(item)],
        *[str(item) for item in (sheet.get("date_like_columns") or []) if str(item)],
    ]
    normalized_question = normalize_identifier(question)
    for column in candidate_columns:
        if normalize_identifier(column) in normalized_question:
            return column
    if any(keyword in question for keyword in ("不同", "各", "每个", "按", "按照", "分组")) and candidate_columns:
        return candidate_columns[0]
    return ""


def _pick_lookup_columns(question: str, sheet: dict[str, Any], *, excluded_columns: set[str] | None = None) -> list[str]:
    normalized_question = normalize_identifier(question)
    excluded = excluded_columns or set()
    columns = [str(item) for item in (sheet.get("column_names") or []) if str(item)]
    matched = [
        column
        for column in columns
        if column not in excluded and normalize_identifier(column) in normalized_question
    ]
    if matched:
        return matched[:2]
    numeric_columns = [str(item) for item in (sheet.get("numeric_columns") or []) if str(item)]
    return [column for column in numeric_columns if column not in excluded][:1]


def _extract_filters(question: str, sheet: dict[str, Any]) -> list[dict[str, str]]:
    filters: list[dict[str, str]] = []
    columns = [str(item) for item in (sheet.get("column_names") or []) if str(item)]
    normalized_map = {normalize_identifier(column): column for column in columns}
    for matched in re.finditer(r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*[=为]\s*([A-Za-z0-9_\-\u4e00-\u9fff\.]+)", question):
        column_hint = normalize_identifier(matched.group(1))
        value = str(matched.group(2) or "").strip()
        column = normalized_map.get(column_hint)
        if not column or not value:
            continue
        filters.append({"column": column, "value": value})
    return filters


def _is_summary_intent(question: str) -> bool:
    return _contains_any(question, _SUMMARY_KEYWORDS)


def _is_lookup_intent(question: str) -> bool:
    lowered = question.lower()
    return any(keyword in lowered for keyword in _LOOKUP_KEYWORDS)


def _is_explicit_aggregate_intent(question: str) -> bool:
    return (
        _contains_any(question, _AGGREGATE_MEAN_KEYWORDS)
        or _contains_any(question, _AGGREGATE_SUM_KEYWORDS)
        or _contains_any(question, _AGGREGATE_COUNT_KEYWORDS)
    )


def _is_grouped_aggregate_intent(question: str, *, group_by: str) -> bool:
    return bool(group_by) and _contains_any(question, _GROUPED_AGGREGATE_KEYWORDS)


def plan_tabular_query(*, question: str, profile: dict[str, Any]) -> dict[str, Any]:
    sheet = _pick_sheet(profile, question)
    if sheet is None:
        return {
            "needs_clarification": True,
            "clarification_message": "未找到可用的工作表结构信息。",
            "clarification_reason": "sheet_missing",
        }

    explicit_metric_columns = _pick_metric_columns(question, sheet, allow_fallback=False)
    fallback_metric_columns = explicit_metric_columns or _pick_metric_columns(question, sheet)
    group_by = _pick_group_by(question, sheet)
    filters = _extract_filters(question, sheet)
    lookup_columns = _pick_lookup_columns(
        question,
        sheet,
        excluded_columns={str(item.get("column") or "") for item in filters},
    )
    focus_columns = _pick_focus_columns(
        question,
        sheet,
        filters=filters,
        group_by=group_by,
        metric_columns=fallback_metric_columns,
    )

    if _contains_any(question, _COMPARE_KEYWORDS) and fallback_metric_columns:
        operation = "compare"
    elif filters and _is_lookup_intent(question):
        operation = "lookup"
    elif _is_grouped_aggregate_intent(question, group_by=group_by) or _is_explicit_aggregate_intent(question):
        operation = "aggregate"
    elif _is_summary_intent(question):
        operation = "summary"
    else:
        operation = "summary"

    if _contains_any(question, _AGGREGATE_MEAN_KEYWORDS):
        aggregate = "mean"
    elif _contains_any(question, _AGGREGATE_SUM_KEYWORDS):
        aggregate = "sum"
    elif _contains_any(question, _AGGREGATE_COUNT_KEYWORDS):
        aggregate = "count"
    else:
        aggregate = "mean"

    metric_columns = explicit_metric_columns if operation == "summary" else fallback_metric_columns

    return {
        "needs_clarification": False,
        "clarification_message": "",
        "clarification_reason": "",
        "operation": operation,
        "sheet_name": str(sheet.get("sheet_name") or ""),
        "metric_columns": metric_columns,
        "focus_columns": focus_columns,
        "group_by": group_by,
        "lookup_columns": lookup_columns,
        "filters": filters,
        "aggregate": aggregate,
    }


__all__ = ["plan_tabular_query"]
