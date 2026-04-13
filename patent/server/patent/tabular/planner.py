from __future__ import annotations

import re
from typing import Any

from server.patent.tabular.schema_profiler import normalize_identifier


def _pick_sheet(profile: dict[str, Any], question: str) -> dict[str, Any] | None:
    sheets = [sheet for sheet in (profile.get("sheets") or []) if isinstance(sheet, dict)]
    if not sheets:
        return None
    normalized_question = normalize_identifier(question)
    for sheet in sheets:
        if normalize_identifier(str(sheet.get("sheet_name") or "")) in normalized_question:
            return sheet
    return sheets[0]


def _pick_metric_columns(question: str, sheet: dict[str, Any]) -> list[str]:
    numeric_columns = [str(item) for item in (sheet.get("numeric_columns") or []) if str(item)]
    normalized_question = normalize_identifier(question)
    matched = [column for column in numeric_columns if normalize_identifier(column) in normalized_question]
    if matched:
        return matched
    return numeric_columns[:1]


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


def plan_tabular_query(*, question: str, profile: dict[str, Any]) -> dict[str, Any]:
    sheet = _pick_sheet(profile, question)
    if sheet is None:
        return {
            "needs_clarification": True,
            "clarification_message": "未找到可用的工作表结构信息。",
            "clarification_reason": "sheet_missing",
        }

    metric_columns = _pick_metric_columns(question, sheet)
    group_by = _pick_group_by(question, sheet)
    filters = _extract_filters(question, sheet)
    lookup_columns = _pick_lookup_columns(
        question,
        sheet,
        excluded_columns={str(item.get("column") or "") for item in filters},
    )
    if any(keyword in question for keyword in ("比较", "对比")) and metric_columns:
        operation = "compare"
    elif filters and any(keyword in question for keyword in ("多少", "是什么", "which", "what")):
        operation = "lookup"
    elif metric_columns:
        operation = "aggregate"
    else:
        operation = "summary"

    if any(keyword in question for keyword in ("均值", "平均")):
        aggregate = "mean"
    elif any(keyword in question for keyword in ("总和", "合计", "sum")):
        aggregate = "sum"
    elif any(keyword in question for keyword in ("数量", "几条", "多少个", "count")):
        aggregate = "count"
    else:
        aggregate = "mean"

    return {
        "needs_clarification": False,
        "clarification_message": "",
        "clarification_reason": "",
        "operation": operation,
        "sheet_name": str(sheet.get("sheet_name") or ""),
        "metric_columns": metric_columns,
        "group_by": group_by,
        "lookup_columns": lookup_columns,
        "filters": filters,
        "aggregate": aggregate,
    }


__all__ = ["plan_tabular_query"]
