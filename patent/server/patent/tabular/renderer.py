from __future__ import annotations

import re
from typing import Any


def has_usable_tabular_result(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("empty_reason") or "").strip():
        return False
    rows = [item for item in (result.get("rows") or []) if isinstance(item, dict)]
    return bool(rows)


def _render_rows(rows: list[dict[str, Any]], *, limit: int = 5, columns: list[str] | None = None) -> str:
    if not rows:
        return "无"
    selected_columns = [str(item) for item in (columns or []) if str(item)]
    lines: list[str] = []
    for index, row in enumerate(rows[:limit], start=1):
        normalized_row = row if isinstance(row, dict) else {}
        if selected_columns:
            visible_row = {key: normalized_row.get(key) for key in selected_columns if key in normalized_row}
            if visible_row:
                normalized_row = visible_row
        parts = [f"{key}={value}" for key, value in normalized_row.items()]
        lines.append(f"- 样例 {index}: " + "; ".join(parts))
    return "\n".join(lines)


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _column_match_keys(column_name: str) -> set[str]:
    text = str(column_name or "").strip()
    if not text:
        return set()
    keys = {_compact_text(text)}
    chinese_only = "".join(re.findall(r"[\u4e00-\u9fff]+", text))
    if len(chinese_only) >= 2:
        keys.add(_compact_text(chinese_only))
    for part in re.split(r"[_\-()（）/\\[\],\s]+", text):
        normalized = _compact_text(part)
        if len(normalized) >= 2:
            keys.add(normalized)
    return {item for item in keys if item}


def _ordered_column_names(*, available_columns: list[str], focus_columns: list[str]) -> list[str]:
    ordered: list[str] = []
    for column in focus_columns:
        if column in available_columns and column not in ordered:
            ordered.append(column)
    for column in available_columns:
        if column not in ordered:
            ordered.append(column)
    return ordered


def infer_tabular_summary_focus_columns(
    *,
    question: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    max_columns: int = 4,
) -> list[str]:
    summary_stats = result.get("summary_stats") if isinstance(result.get("summary_stats"), dict) else {}
    available_columns = [str(item) for item in (summary_stats.get("columns") or []) if str(item)]
    if not available_columns:
        return []

    selected: list[str] = []

    def add(column_name: Any) -> None:
        normalized = str(column_name or "").strip()
        if normalized and normalized in available_columns and normalized not in selected:
            selected.append(normalized)

    for item in plan.get("focus_columns") or []:
        add(item)
    add(plan.get("group_by"))
    for item in plan.get("lookup_columns") or []:
        add(item)
    for item in plan.get("filters") or []:
        if isinstance(item, dict):
            add(item.get("column"))

    normalized_question = _compact_text(question)
    if normalized_question:
        for column in available_columns:
            keys = _column_match_keys(column)
            if any(key and key in normalized_question for key in keys):
                add(column)

    return selected[: max(1, int(max_columns))] if selected else []


def _render_summary_sections(
    summary_stats: dict[str, Any],
    *,
    focus_columns: list[str] | None = None,
    profile_limit: int = 12,
    numeric_limit: int = 8,
    categorical_limit: int = 6,
) -> list[str]:
    selected_columns = [str(item) for item in (focus_columns or []) if str(item)]
    columns = [str(item) for item in (summary_stats.get("columns") or []) if str(item)]
    ordered_columns = _ordered_column_names(available_columns=columns, focus_columns=selected_columns)

    lines: list[str] = ["全表统计摘要:"]
    lines.append(f"- row_count: {summary_stats.get('row_count', 0)}")
    lines.append(f"- column_count: {summary_stats.get('column_count', 0)}")
    if selected_columns:
        lines.append("- focus_columns: " + ", ".join(selected_columns))
    if ordered_columns:
        lines.append("- columns: " + ", ".join(ordered_columns))

    profile_map = {
        str(item.get("name") or ""): item
        for item in (summary_stats.get("column_profiles") or [])
        if isinstance(item, dict) and str(item.get("name") or "")
    }
    visible_profile_names = [column for column in ordered_columns if column in profile_map][: max(0, int(profile_limit))]
    if visible_profile_names:
        lines.append("列画像摘要:")
        for column in visible_profile_names:
            item = profile_map[column]
            lines.append(
                f"- {column}: kind={item.get('kind')}, unique_count={item.get('unique_count')}, missing_ratio={item.get('missing_ratio')}"
            )

    numeric_summaries = summary_stats.get("numeric_summaries") if isinstance(summary_stats.get("numeric_summaries"), dict) else {}
    numeric_columns = [column for column in ordered_columns if column in numeric_summaries][: max(0, int(numeric_limit))]
    if numeric_columns:
        lines.append("数值列摘要:")
        for column in numeric_columns:
            stats = numeric_summaries.get(column) or {}
            lines.append(
                f"- {column}: min={stats.get('min')}, max={stats.get('max')}, mean={stats.get('mean')}, median={stats.get('median')}"
            )

    categorical_summaries = summary_stats.get("categorical_summaries") if isinstance(summary_stats.get("categorical_summaries"), dict) else {}
    categorical_columns = [column for column in ordered_columns if column in categorical_summaries][: max(0, int(categorical_limit))]
    if categorical_columns:
        lines.append("类别列分布摘要:")
        for column in categorical_columns:
            stats = categorical_summaries.get(column) or {}
            top_values = stats.get("top_values") if isinstance(stats.get("top_values"), list) else []
            rendered = []
            for item in top_values[:5]:
                if not isinstance(item, dict):
                    continue
                rendered.append(f"{item.get('value')}({item.get('count')}, ratio={item.get('ratio')})")
            if rendered:
                lines.append(f"- {column}: " + "; ".join(rendered))
    return lines


def build_tabular_result_context(
    *,
    file_name: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    question: str = "",
    rich_summary: bool = False,
    profile_limit: int = 12,
    numeric_limit: int = 8,
    categorical_limit: int = 6,
) -> str:
    lines = [
        f"文件: {file_name}",
        f"匹配工作表: {str(result.get('sheet_name') or plan.get('sheet_name') or 'unknown')}",
        f"执行操作: {str(result.get('operation') or plan.get('operation') or 'summary')}",
    ]
    if result.get("row_count_before") is not None:
        lines.append(f"过滤前行数: {result.get('row_count_before')}")
    if result.get("row_count_after") is not None:
        lines.append(f"过滤后行数: {result.get('row_count_after')}")

    summary_stats = result.get("summary_stats") if isinstance(result.get("summary_stats"), dict) else {}
    operation = str(result.get("operation") or plan.get("operation") or "").strip().lower()
    if summary_stats:
        if operation == "summary" and rich_summary:
            focus_columns = infer_tabular_summary_focus_columns(question=question, plan=plan, result=result)
            lines.extend(
                _render_summary_sections(
                    summary_stats,
                    focus_columns=focus_columns,
                    profile_limit=profile_limit,
                    numeric_limit=numeric_limit,
                    categorical_limit=categorical_limit,
                )
            )
        elif operation == "compare_tables":
            aggregate = str(summary_stats.get("aggregate") or plan.get("aggregate") or "").strip()
            group_by = str(summary_stats.get("group_by") or plan.get("group_by") or "").strip()
            metric_columns = [
                str(item)
                for item in (summary_stats.get("metric_columns") or plan.get("metric_columns") or [])
                if str(item)
            ]
            lines.append("多表对比摘要:")
            table_count = summary_stats.get("table_count")
            if table_count is not None:
                lines.append(f"- 表格数: {table_count}")
            source_row_count = summary_stats.get("source_row_count")
            if source_row_count is not None:
                lines.append(f"- 命中行数: {source_row_count}")
            returned_count = summary_stats.get("returned_count")
            if returned_count is not None:
                lines.append(f"- 返回结果行数: {returned_count}")
            lines.append(f"- 分组对比: {'是' if int(summary_stats.get('grouped_compare') or 0) else '否'}")
            if aggregate:
                lines.append(f"- 聚合方式: {aggregate}")
            if group_by:
                lines.append(f"- 分组列: {group_by}")
            if metric_columns:
                lines.append("指标列: " + ", ".join(metric_columns))
        else:
            aggregate = str(summary_stats.get("aggregate") or plan.get("aggregate") or "").strip()
            if aggregate:
                lines.append(f"聚合方式: {aggregate}")
            group_by = str(summary_stats.get("group_by") or plan.get("group_by") or "").strip()
            if group_by:
                lines.append(f"分组列: {group_by}")
            metric_columns = [str(item) for item in (summary_stats.get("metric_columns") or plan.get("metric_columns") or []) if str(item)]
            if metric_columns:
                lines.append("指标列: " + ", ".join(metric_columns))
            lookup_columns = [str(item) for item in (summary_stats.get("lookup_columns") or plan.get("lookup_columns") or []) if str(item)]
            if lookup_columns:
                lines.append("返回列: " + ", ".join(lookup_columns))
            source_row_count = summary_stats.get("source_row_count")
            if source_row_count is not None:
                lines.append(f"命中行数: {source_row_count}")

    filters = [dict(item) for item in (plan.get("filters") or summary_stats.get("filters") or []) if isinstance(item, dict)]
    if filters:
        lines.append("过滤条件:")
        for item in filters:
            lines.append(f"- {item.get('column')} = {item.get('value')}")

    rows = [dict(item) for item in (result.get("rows") or []) if isinstance(item, dict)]
    if operation == "summary" and rich_summary:
        focus_columns = infer_tabular_summary_focus_columns(question=question, plan=plan, result=result)
        lines.append("代表性样例:")
        lines.append(_render_rows(rows, limit=5, columns=focus_columns if focus_columns else None))
    elif rows:
        lines.append("对比结果:" if operation == "compare_tables" else "结果样例:")
        lines.append(_render_rows(rows, limit=5))

    empty_reason = str(result.get("empty_reason") or "").strip()
    if empty_reason:
        lines.append(f"空结果原因: {empty_reason}")

    return "\n".join(lines).strip()


__all__ = ["build_tabular_result_context", "has_usable_tabular_result", "infer_tabular_summary_focus_columns"]
