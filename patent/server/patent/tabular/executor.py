from __future__ import annotations

from typing import Any


def _column_names(*, sheet: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    headers = [str(item) for item in (sheet.get("headers") or []) if str(item)]
    if headers:
        return headers
    if rows:
        return [str(item) for item in rows[0].keys() if str(item)]
    return []


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


def _round_number(value: float) -> float:
    return round(float(value), 4)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _empty_result(
    *,
    sheet_name: str,
    operation: str,
    aggregate: str,
    reason: str,
    row_count_before: int = 0,
    row_count_after: int = 0,
) -> dict[str, Any]:
    return {
        "sheet_name": sheet_name,
        "operation": operation,
        "rows": [],
        "row_count": 0,
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
        "empty_reason": reason,
        "summary_stats": {
            "aggregate": aggregate,
            "source_row_count": row_count_after,
        },
    }


def _apply_filters(rows: list[dict[str, Any]], filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered_rows = list(rows)
    for filter_item in filters:
        column = str(filter_item.get("column") or "")
        value = str(filter_item.get("value") or "")
        filtered_rows = [row for row in filtered_rows if str(row.get(column) or "") == value]
    return filtered_rows


def _build_column_profiles(*, rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    row_count = max(1, len(rows))
    profiles: list[dict[str, Any]] = []
    for column in columns:
        values = [row.get(column) for row in rows]
        non_empty_values = [value for value in values if str(value or "").strip()]
        numeric_values = [_to_float(value) for value in values]
        numeric_values = [value for value in numeric_values if value is not None]
        is_numeric = bool(numeric_values) and (len(numeric_values) / row_count) >= 0.6
        profiles.append(
            {
                "name": column,
                "kind": "numeric" if is_numeric else "categorical",
                "missing_ratio": round((row_count - len(non_empty_values)) / row_count, 4),
                "unique_count": len({str(value).strip() for value in non_empty_values}),
            }
        )
    return profiles


def _build_numeric_summaries(*, rows: list[dict[str, Any]], profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    numeric_columns = [str(item.get("name") or "") for item in profiles if str(item.get("kind") or "") == "numeric"]
    for column in numeric_columns:
        numeric_values = [_to_float(row.get(column)) for row in rows]
        numeric_values = [value for value in numeric_values if value is not None]
        if not numeric_values:
            continue
        summaries[column] = {
            "min": _round_number(min(numeric_values)),
            "max": _round_number(max(numeric_values)),
            "mean": _round_number(sum(numeric_values) / len(numeric_values)),
            "median": _round_number(_median(numeric_values)),
        }
    return summaries


def _build_categorical_summaries(
    *,
    rows: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    top_n: int = 5,
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    row_count = max(1, len(rows))
    categorical_columns = [str(item.get("name") or "") for item in profiles if str(item.get("kind") or "") == "categorical"]
    for column in categorical_columns:
        counts: dict[str, int] = {}
        for row in rows:
            value = str(row.get(column) or "").strip()
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
        if not counts:
            continue
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        summaries[column] = {
            "top_values": [
                {
                    "value": value,
                    "count": count,
                    "ratio": round(count / row_count, 4),
                }
                for value, count in ordered[:top_n]
            ]
        }
    return summaries


def _evenly_spaced_positions(*, row_count: int, limit: int) -> list[int]:
    if row_count <= 0 or limit <= 0:
        return []
    if row_count <= limit:
        return list(range(row_count))
    return [round(index * (row_count - 1) / max(1, limit - 1)) for index in range(limit)]


def _build_representative_summary_rows(
    *,
    rows: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return [dict(row) for row in rows]

    candidate_positions: list[int] = []
    numeric_columns = [str(item.get("name") or "") for item in profiles if str(item.get("kind") or "") == "numeric"][:2]
    for column in numeric_columns:
        numeric_positions = [
            (index, numeric_value)
            for index, numeric_value in enumerate(_to_float(row.get(column)) for row in rows)
            if numeric_value is not None
        ]
        if not numeric_positions:
            continue
        min_position = min(numeric_positions, key=lambda item: (item[1], item[0]))[0]
        max_position = max(numeric_positions, key=lambda item: (item[1], -item[0]))[0]
        candidate_positions.extend([min_position, max_position])

    categorical_columns = [str(item.get("name") or "") for item in profiles if str(item.get("kind") or "") == "categorical"]
    for column in categorical_columns:
        counts: dict[str, int] = {}
        first_positions: dict[str, int] = {}
        for index, row in enumerate(rows):
            value = str(row.get(column) or "").strip()
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
            first_positions.setdefault(value, index)
        if not counts:
            continue
        rare_value = sorted(counts.items(), key=lambda item: (item[1], item[0]))[0][0]
        common_value = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        candidate_positions.extend([first_positions[rare_value], first_positions[common_value]])
        if len(candidate_positions) >= limit * 2:
            break

    candidate_positions.extend(_evenly_spaced_positions(row_count=len(rows), limit=limit))

    picked_positions: list[int] = []
    seen: set[int] = set()
    for position in candidate_positions:
        normalized = int(position)
        if normalized < 0 or normalized >= len(rows) or normalized in seen:
            continue
        seen.add(normalized)
        picked_positions.append(normalized)
        if len(picked_positions) >= limit:
            break

    return [dict(rows[position]) for position in picked_positions]


def execute_tabular_plan(*, workbook: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    operation = str(plan.get("operation") or "summary")
    aggregate = str(plan.get("aggregate") or "mean")
    target_sheet = str(plan.get("sheet_name") or "")
    sheet = _find_sheet(workbook, target_sheet)
    if sheet is None:
        return _empty_result(sheet_name=target_sheet, operation=operation, aggregate=aggregate, reason="sheet_not_found")
    source_rows = [dict(row) for row in (sheet.get("rows") or []) if isinstance(row, dict)]
    filters = [dict(item) for item in (plan.get("filters") or []) if isinstance(item, dict)]
    rows = _apply_filters(source_rows, filters)
    row_count_before = len(source_rows)
    row_count_after = len(rows)
    if not rows:
        return _empty_result(
            sheet_name=str(sheet.get("sheet_name") or ""),
            operation=operation,
            aggregate=aggregate,
            reason="no_rows",
            row_count_before=row_count_before,
            row_count_after=row_count_after,
        )

    if operation in {"aggregate", "compare"}:
        group_by = str(plan.get("group_by") or "")
        metric_columns = [str(item) for item in (plan.get("metric_columns") or []) if str(item)]
        if not group_by and operation == "compare":
            return _empty_result(
                sheet_name=str(sheet.get("sheet_name") or ""),
                operation=operation,
                aggregate=aggregate,
                reason="group_by_missing",
                row_count_before=row_count_before,
                row_count_after=row_count_after,
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
            "row_count_before": row_count_before,
            "row_count_after": row_count_after,
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
                row_count_before=row_count_before,
                row_count_after=row_count_after,
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
            "row_count_before": row_count_before,
            "row_count_after": row_count_after,
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
            row_count_before=row_count_before,
            row_count_after=row_count_after,
        )

    columns = _column_names(sheet=sheet, rows=rows)
    column_profiles = _build_column_profiles(rows=rows, columns=columns)
    numeric_summaries = _build_numeric_summaries(rows=rows, profiles=column_profiles)
    categorical_summaries = _build_categorical_summaries(rows=rows, profiles=column_profiles)
    representative_rows = _build_representative_summary_rows(rows=rows, profiles=column_profiles, limit=5)

    return {
        "sheet_name": str(sheet.get("sheet_name") or ""),
        "operation": "summary",
        "rows": representative_rows,
        "row_count": len(rows),
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
        "empty_reason": "",
        "summary_stats": {
            "aggregate": aggregate,
            "source_row_count": len(rows),
            "row_count": len(rows),
            "column_count": len(columns),
            "columns": columns,
            "column_profiles": column_profiles,
            "numeric_summaries": numeric_summaries,
            "categorical_summaries": categorical_summaries,
            "filters": filters,
        },
    }


__all__ = ["execute_tabular_plan"]
