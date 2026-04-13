from __future__ import annotations

from typing import Any


def has_usable_tabular_result(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("empty_reason") or "").strip():
        return False
    rows = [item for item in (result.get("rows") or []) if isinstance(item, dict)]
    return bool(rows)


def _render_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    rendered: list[str] = []
    for index, row in enumerate(rows[:limit], start=1):
        normalized_row = row if isinstance(row, dict) else {}
        parts = [f"{key}={value}" for key, value in normalized_row.items()]
        if parts:
            rendered.append(f"- 样例 {index}: " + "; ".join(parts))
    return rendered


def build_tabular_result_context(*, file_name: str, plan: dict[str, Any], result: dict[str, Any]) -> str:
    lines = [
        f"文件: {file_name}",
        f"匹配工作表: {str(result.get('sheet_name') or plan.get('sheet_name') or 'unknown')}",
        f"执行操作: {str(result.get('operation') or plan.get('operation') or 'summary')}",
    ]

    aggregate = str((result.get("summary_stats") or {}).get("aggregate") or plan.get("aggregate") or "").strip()
    if aggregate:
        lines.append(f"聚合方式: {aggregate}")

    group_by = str((result.get("summary_stats") or {}).get("group_by") or plan.get("group_by") or "").strip()
    if group_by:
        lines.append(f"分组列: {group_by}")

    metric_columns = [str(item) for item in ((result.get("summary_stats") or {}).get("metric_columns") or plan.get("metric_columns") or []) if str(item)]
    if metric_columns:
        lines.append("指标列: " + ", ".join(metric_columns))

    lookup_columns = [str(item) for item in ((result.get("summary_stats") or {}).get("lookup_columns") or plan.get("lookup_columns") or []) if str(item)]
    if lookup_columns:
        lines.append("返回列: " + ", ".join(lookup_columns))

    filters = [dict(item) for item in ((result.get("summary_stats") or {}).get("filters") or plan.get("filters") or []) if isinstance(item, dict)]
    if filters:
        lines.append("过滤条件:")
        for item in filters:
            lines.append(f"- {item.get('column')} = {item.get('value')}")

    source_row_count = (result.get("summary_stats") or {}).get("source_row_count")
    if source_row_count is not None:
        lines.append(f"命中行数: {source_row_count}")

    empty_reason = str(result.get("empty_reason") or "").strip()
    if empty_reason:
        lines.append(f"空结果原因: {empty_reason}")

    rows = [dict(item) for item in (result.get("rows") or []) if isinstance(item, dict)]
    if rows:
        lines.append("结果样例:")
        lines.extend(_render_rows(rows))

    return "\n".join(lines).strip()


__all__ = ["build_tabular_result_context", "has_usable_tabular_result"]
