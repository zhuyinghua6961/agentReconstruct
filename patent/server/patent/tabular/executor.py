from __future__ import annotations

from typing import Any


def _find_sheet(workbook: dict[str, Any], sheet_name: str) -> dict[str, Any] | None:
    for sheet in workbook.get("sheets") or []:
        if str(sheet.get("sheet_name") or "") == str(sheet_name or ""):
            return dict(sheet)
    return None


def _to_float(value: object) -> float | None:
    normalized = str(value or "").strip().replace(",", "").replace("%", "")
    if not normalized:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def _empty_result(*, sheet_name: str, operation: str, aggregate: str, reason: str) -> dict[str, Any]:
    return {
        "sheet_name": sheet_name,
        "operation": operation,
        "rows": [],
        "row_count": 0,
        "empty_reason": reason,
        "summary_stats": {
            "aggregate": aggregate,
            "source_row_count": 0,
        },
    }


def _apply_filters(rows: list[dict[str, Any]], filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered_rows = list(rows)
    for filter_item in filters:
        column = str(filter_item.get("column") or "")
        value = str(filter_item.get("value") or "")
        filtered_rows = [row for row in filtered_rows if str(row.get(column) or "") == value]
    return filtered_rows


def execute_tabular_plan(*, workbook: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    operation = str(plan.get("operation") or "summary")
    aggregate = str(plan.get("aggregate") or "mean")
    target_sheet = str(plan.get("sheet_name") or "")
    sheet = _find_sheet(workbook, target_sheet)
    if sheet is None:
        return _empty_result(sheet_name=target_sheet, operation=operation, aggregate=aggregate, reason="sheet_not_found")
    rows = [dict(row) for row in (sheet.get("rows") or []) if isinstance(row, dict)]
    filters = [dict(item) for item in (plan.get("filters") or []) if isinstance(item, dict)]
    rows = _apply_filters(rows, filters)
    if not rows:
        return _empty_result(sheet_name=str(sheet.get("sheet_name") or ""), operation=operation, aggregate=aggregate, reason="no_rows")

    if operation in {"aggregate", "compare"}:
        group_by = str(plan.get("group_by") or "")
        metric_columns = [str(item) for item in (plan.get("metric_columns") or []) if str(item)]
        if not group_by and operation == "compare":
            return _empty_result(
                sheet_name=str(sheet.get("sheet_name") or ""),
                operation=operation,
                aggregate=aggregate,
                reason="group_by_missing",
            )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            group_value = str(row.get(group_by) or "") if group_by else "all"
            grouped.setdefault(group_value, []).append(row)

        result_rows: list[dict[str, Any]] = []
        for group_value, group_rows in grouped.items():
            rendered: dict[str, Any] = {group_by or "group": group_value}
            for metric_column in metric_columns:
                numeric_values = [_to_float(row.get(metric_column)) for row in group_rows]
                numeric_values = [value for value in numeric_values if value is not None]
                if aggregate == "count":
                    rendered[metric_column or "count"] = len(group_rows)
                    continue
                if not numeric_values:
                    rendered[metric_column] = ""
                    continue
                if aggregate == "sum":
                    rendered[metric_column] = round(sum(numeric_values), 4)
                else:
                    rendered[metric_column] = round(sum(numeric_values) / len(numeric_values), 4)
            result_rows.append(rendered)

        return {
            "sheet_name": str(sheet.get("sheet_name") or ""),
            "operation": operation,
            "rows": result_rows,
            "row_count": len(result_rows),
            "empty_reason": "",
            "summary_stats": {
                "aggregate": aggregate,
                "group_by": group_by,
                "metric_columns": metric_columns,
                "source_row_count": len(rows),
                "filters": filters,
            },
        }

    if operation == "lookup":
        lookup_columns = [str(item) for item in (plan.get("lookup_columns") or []) if str(item)]
        if not lookup_columns:
            return _empty_result(
                sheet_name=str(sheet.get("sheet_name") or ""),
                operation=operation,
                aggregate=aggregate,
                reason="lookup_columns_missing",
            )
        result_rows = [
            {column: row.get(column, "") for column in lookup_columns}
            for row in rows
        ]
        return {
            "sheet_name": str(sheet.get("sheet_name") or ""),
            "operation": operation,
            "rows": result_rows,
            "row_count": len(result_rows),
            "empty_reason": "" if result_rows else "no_lookup_match",
            "summary_stats": {
                "aggregate": aggregate,
                "filters": filters,
                "lookup_columns": lookup_columns,
                "source_row_count": len(rows),
            },
        }

    if operation != "summary":
        return _empty_result(
            sheet_name=str(sheet.get("sheet_name") or ""),
            operation=operation,
            aggregate=aggregate,
            reason="unsupported_operation",
        )

    return {
        "sheet_name": str(sheet.get("sheet_name") or ""),
        "operation": "summary",
        "rows": rows[:5],
        "row_count": len(rows),
        "empty_reason": "",
        "summary_stats": {
            "aggregate": aggregate,
            "source_row_count": len(rows),
        },
    }


__all__ = ["execute_tabular_plan"]
