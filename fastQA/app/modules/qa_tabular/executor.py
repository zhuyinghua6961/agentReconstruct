from __future__ import annotations

from typing import Any


def _to_plain_value(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    return value


def _round_number(value: Any, *, digits: int = 4) -> Any:
    value = _to_plain_value(value)
    if isinstance(value, float):
        return round(value, digits)
    return value


def _to_records(frame, *, limit: int = 10) -> list[dict[str, Any]]:
    if frame is None:
        return []
    rows: list[dict[str, Any]] = []
    head = frame.head(limit)
    for record in head.to_dict(orient="records"):
        cleaned: dict[str, Any] = {}
        for key, value in record.items():
            cleaned[str(key)] = _to_plain_value(value)
        rows.append(cleaned)
    return rows


def _coerce_series(series):
    try:
        import pandas as pd
    except Exception as exc:
        raise RuntimeError(f"pandas not available: {exc}") from exc
    normalized = (
        series.astype(str)
        .str.strip()
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
    )
    return pd.to_numeric(normalized, errors="coerce")


def _resolved_metric_columns(plan: dict[str, Any]) -> list[str]:
    columns = [str(item) for item in (plan.get("metric_columns") or []) if str(item)]
    if columns:
        return columns
    metric_column = str(plan.get("metric_column") or "")
    return [metric_column] if metric_column else []


def _aggregate_numeric(numeric, aggregate: str):
    if aggregate == "sum":
        return numeric.sum()
    if aggregate == "max":
        return numeric.max()
    if aggregate == "min":
        return numeric.min()
    return numeric.mean()


def _finalize_rows(rows: list[dict[str, Any]], *, total_count: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    clipped = list(rows[: max(0, int(limit))])
    truncated_count = max(0, int(total_count) - len(clipped))
    return clipped, truncated_count


def _apply_filters(frame, filters: list[dict[str, Any]]):
    result = frame
    warnings: list[str] = []
    for item in filters or []:
        column = str(item.get("column") or "")
        op = str(item.get("op") or "==")
        value = item.get("value")
        if column not in result.columns:
            warnings.append(f"过滤列不存在: {column}")
            continue
        series = result[column]
        if op in {">", ">=", "<", "<="}:
            numeric = _coerce_series(series)
            numeric_value = float(value)
            if op == ">":
                mask = numeric > numeric_value
            elif op == ">=":
                mask = numeric >= numeric_value
            elif op == "<":
                mask = numeric < numeric_value
            else:
                mask = numeric <= numeric_value
            result = result[mask.fillna(False)]
            continue
        if op == "==":
            mask = series.astype(str).str.strip().str.lower() == str(value).strip().lower()
            result = result[mask.fillna(False)]
            continue
        warnings.append(f"不支持的过滤操作: {op}")
    return result, warnings


def _build_column_profiles(frame) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    row_count = max(1, int(len(frame.index)))
    for column in list(frame.columns):
        series = frame[column]
        non_null = series.dropna()
        normalized = _coerce_series(series)
        numeric_non_null = normalized.dropna()
        is_numeric = int(len(numeric_non_null.index)) > 0 and (int(len(numeric_non_null.index)) / row_count) >= 0.6
        profiles.append(
            {
                "name": str(column),
                "kind": "numeric" if is_numeric else "categorical",
                "missing_ratio": round(float(series.isna().sum()) / float(row_count), 4),
                "unique_count": int(non_null.nunique(dropna=True)),
            }
        )
    return profiles


def _build_numeric_summaries(frame) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    row_count = max(1, int(len(frame.index)))
    for column in list(frame.columns):
        series = frame[column]
        numeric = _coerce_series(series).dropna()
        if int(len(numeric.index)) <= 0:
            continue
        if (int(len(numeric.index)) / row_count) < 0.6:
            continue
        summaries[str(column)] = {
            "min": _round_number(float(numeric.min())),
            "max": _round_number(float(numeric.max())),
            "mean": _round_number(float(numeric.mean())),
            "median": _round_number(float(numeric.median())),
        }
    return summaries


def _build_categorical_summaries(frame, *, top_n: int = 5) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    row_count = max(1, int(len(frame.index)))
    numeric_columns = set(_build_numeric_summaries(frame).keys())
    for column in list(frame.columns):
        if str(column) in numeric_columns:
            continue
        series = frame[column].dropna().astype(str).str.strip()
        series = series[series != ""]
        if int(len(series.index)) <= 0:
            continue
        counts = series.value_counts(dropna=True).head(top_n)
        top_values: list[dict[str, Any]] = []
        for value, count in counts.items():
            top_values.append(
                {
                    "value": str(value),
                    "count": int(count),
                    "ratio": round(float(count) / float(row_count), 4),
                }
            )
        summaries[str(column)] = {"top_values": top_values}
    return summaries


def _records_for_positions(frame, *, positions: list[int]) -> list[dict[str, Any]]:
    if frame is None or not positions:
        return []
    rows: list[dict[str, Any]] = []
    subset = frame.iloc[positions]
    for record in subset.to_dict(orient="records"):
        cleaned: dict[str, Any] = {}
        for key, value in record.items():
            cleaned[str(key)] = _to_plain_value(value)
        rows.append(cleaned)
    return rows


def _evenly_spaced_positions(*, row_count: int, limit: int) -> list[int]:
    if row_count <= 0 or limit <= 0:
        return []
    if row_count <= limit:
        return list(range(row_count))
    positions: list[int] = []
    for index in range(limit):
        scaled = round(index * (row_count - 1) / max(1, limit - 1))
        positions.append(int(scaled))
    return positions


def _build_representative_summary_rows(frame, *, limit: int = 5) -> list[dict[str, Any]]:
    row_count = int(len(frame.index)) if frame is not None else 0
    if row_count <= 0:
        return []
    if row_count <= limit:
        return _to_records(frame, limit=limit)

    candidate_positions: list[int] = []
    numeric_columns = list(_build_numeric_summaries(frame).keys())[:2]
    for column in numeric_columns:
        numeric = _coerce_series(frame[column]).dropna()
        if int(len(numeric.index)) <= 0:
            continue
        try:
            min_position = int(frame.index.get_loc(numeric.idxmin()))
            max_position = int(frame.index.get_loc(numeric.idxmax()))
        except Exception:
            continue
        candidate_positions.extend([min_position, max_position])

    numeric_column_set = set(numeric_columns)
    for column in list(frame.columns):
        column_name = str(column)
        if column_name in numeric_column_set:
            continue
        series = frame[column].dropna().astype(str).str.strip()
        series = series[series != ""]
        if int(len(series.index)) <= 0:
            continue
        counts = series.value_counts(dropna=True)
        if int(len(counts.index)) <= 0:
            continue
        for value in [str(counts.index[-1]), str(counts.index[0])]:
            matched = series[series == value]
            if int(len(matched.index)) <= 0:
                continue
            try:
                candidate_positions.append(int(frame.index.get_loc(matched.index[0])))
            except Exception:
                continue
        if len(candidate_positions) >= limit * 2:
            break

    candidate_positions.extend(_evenly_spaced_positions(row_count=row_count, limit=limit))
    deduped_positions: list[int] = []
    seen: set[int] = set()
    for position in candidate_positions:
        normalized = int(position)
        if normalized < 0 or normalized >= row_count or normalized in seen:
            continue
        seen.add(normalized)
        deduped_positions.append(normalized)
        if len(deduped_positions) >= limit:
            break

    return _records_for_positions(frame, positions=deduped_positions)


def execute_tabular_plan(*, workbook: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if str(plan.get("operation") or "") == "compound":
        subplans = [item for item in (plan.get("subplans") or []) if isinstance(item, dict)]
        subresults = [execute_tabular_plan(workbook=workbook, plan=subplan) for subplan in subplans]
        warnings: list[str] = []
        for subresult in subresults:
            warnings.extend([str(item) for item in (subresult.get("warnings") or []) if str(item)])
        return {
            "sheet_name": str(plan.get("sheet_name") or ""),
            "operation": "compound",
            "metric_column": "",
            "metric_columns": [],
            "group_column": "",
            "axis_column": "",
            "lookup_columns": [],
            "filters": plan.get("filters") or [],
            "row_count_before": 0,
            "row_count_after": len(subresults),
            "warnings": warnings,
            "result_rows": [],
            "summary_stats": {
                "subresult_count": len(subresults),
            },
            "subresults": subresults,
            "subquestions": [str(item) for item in (plan.get("subquestions") or []) if str(item)],
        }

    target_sheet = str(plan.get("sheet_name") or "")
    frame = None
    for sheet in workbook.get("sheets") or []:
        if str(sheet.get("sheet_name") or "") == target_sheet:
            frame = sheet.get("dataframe")
            break
    if frame is None:
        raise RuntimeError(f"sheet not found: {target_sheet}")

    row_count_before = int(len(frame.index))
    filtered_frame, filter_warnings = _apply_filters(frame, plan.get("filters") or [])
    row_count_after = int(len(filtered_frame.index))
    operation = str(plan.get("operation") or "summary")
    metric_columns = _resolved_metric_columns(plan)
    metric_column = metric_columns[0] if metric_columns else ""
    group_column = str(plan.get("group_column") or "")
    axis_column = str(plan.get("axis_column") or "")
    lookup_columns = [str(item) for item in (plan.get("lookup_columns") or []) if str(item)]
    result: dict[str, Any] = {
        "sheet_name": target_sheet,
        "operation": operation,
        "metric_column": metric_column,
        "metric_columns": metric_columns,
        "group_column": group_column,
        "axis_column": axis_column,
        "lookup_columns": lookup_columns,
        "filters": plan.get("filters") or [],
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
        "warnings": filter_warnings,
        "result_rows": [],
        "summary_stats": {},
    }

    if operation == "summary":
        focus_columns = [str(item) for item in (plan.get("focus_columns") or []) if str(item)]
        result["summary_stats"] = {
            "row_count": row_count_before,
            "column_count": int(len(filtered_frame.columns)),
            "columns": [str(col) for col in list(filtered_frame.columns)],
            "column_profiles": _build_column_profiles(filtered_frame),
            "numeric_summaries": _build_numeric_summaries(filtered_frame),
            "categorical_summaries": _build_categorical_summaries(filtered_frame),
            "sample_strategy": "representative_rows",
        }
        if focus_columns:
            result["summary_stats"]["focus_columns"] = focus_columns
        result["result_rows"] = _build_representative_summary_rows(filtered_frame, limit=5)
        return result

    if operation == "count_rows":
        result_rows, truncated_count = _finalize_rows(_to_records(filtered_frame, limit=5), total_count=row_count_after, limit=5)
        result["summary_stats"] = {"matched_count": row_count_after, "returned_count": len(result_rows), "truncated_count": truncated_count}
        if truncated_count > 0:
            result["warnings"].append(f"结果较多，仅展示前 {len(result_rows)} 条样例。")
        result["result_rows"] = result_rows
        return result

    if operation == "filter_rows":
        result_rows, truncated_count = _finalize_rows(_to_records(filtered_frame, limit=10), total_count=row_count_after, limit=10)
        result["summary_stats"] = {"matched_count": row_count_after, "returned_count": len(result_rows), "truncated_count": truncated_count}
        if truncated_count > 0:
            result["warnings"].append(f"筛选结果过多，仅展示前 {len(result_rows)} 条。")
        result["result_rows"] = result_rows
        return result

    if operation == "lookup":
        if not lookup_columns:
            raise RuntimeError("lookup column not found")
        for column in lookup_columns:
            if column not in filtered_frame.columns:
                raise RuntimeError(f"lookup column not found: {column}")
        result["summary_stats"] = {
            "lookup_columns": lookup_columns,
            "matched_count": row_count_after,
        }
        if row_count_after == 1 and len(lookup_columns) == 1:
            value = filtered_frame.iloc[0][lookup_columns[0]]
            if hasattr(value, "item"):
                try:
                    value = value.item()
                except Exception:
                    pass
            result["summary_stats"]["value"] = value
        result_rows, truncated_count = _finalize_rows(_to_records(filtered_frame[lookup_columns], limit=20), total_count=row_count_after, limit=20)
        result["summary_stats"]["returned_count"] = len(result_rows)
        result["summary_stats"]["truncated_count"] = truncated_count
        if truncated_count > 0:
            result["warnings"].append(f"命中多条记录，仅展示前 {len(result_rows)} 条。")
        result["result_rows"] = result_rows
        return result

    if operation == "trend":
        if not axis_column or axis_column not in filtered_frame.columns:
            raise RuntimeError(f"trend axis column not found: {axis_column}")
        if not metric_columns:
            raise RuntimeError("trend metric column not found")
        temp = filtered_frame.copy()
        sort_series = _coerce_series(filtered_frame[axis_column])
        if int(sort_series.notna().sum()) == 0:
            try:
                import pandas as pd
            except Exception as exc:
                raise RuntimeError(f"pandas not available: {exc}") from exc
            sort_series = pd.to_datetime(filtered_frame[axis_column], errors="coerce")
        temp["__trend_sort__"] = sort_series
        for current_metric_column in metric_columns:
            if current_metric_column not in filtered_frame.columns:
                raise RuntimeError(f"metric column not found: {current_metric_column}")
            temp[current_metric_column] = _coerce_series(filtered_frame[current_metric_column])
        temp = temp.sort_values(by="__trend_sort__", ascending=True, na_position="last")
        keep_columns = [axis_column] + metric_columns
        series_rows, truncated_count = _finalize_rows(_to_records(temp[keep_columns], limit=20), total_count=row_count_after, limit=20)
        trend_summary: dict[str, Any] = {
            "axis_column": axis_column,
            "metric_columns": metric_columns,
            "matched_count": row_count_after,
            "returned_count": len(series_rows),
            "truncated_count": truncated_count,
        }
        if truncated_count > 0:
            result["warnings"].append(f"趋势序列较长，仅展示前 {len(series_rows)} 个点。")
        for current_metric_column in metric_columns:
            numeric = _coerce_series(temp[current_metric_column]).dropna()
            if len(numeric.index) >= 2:
                start_value = float(numeric.iloc[0])
                end_value = float(numeric.iloc[-1])
                trend_summary[f"{current_metric_column}_start"] = start_value
                trend_summary[f"{current_metric_column}_end"] = end_value
                trend_summary[f"{current_metric_column}_delta"] = end_value - start_value
                trend_summary[f"{current_metric_column}_direction"] = (
                    "up" if end_value > start_value else ("down" if end_value < start_value else "flat")
                )
        result["summary_stats"] = trend_summary
        result["result_rows"] = series_rows
        return result

    if operation == "groupby":
        if group_column not in filtered_frame.columns:
            raise RuntimeError(f"group column not found: {group_column}")
        aggregate = str(plan.get("aggregate") or "count")
        grouped = filtered_frame.groupby(group_column, dropna=False)
        if aggregate == "count":
            frame_out = grouped.size().reset_index(name="count")
        else:
            temp = filtered_frame.copy()
            frame_out = None
            for current_metric_column in metric_columns:
                if current_metric_column not in filtered_frame.columns:
                    raise RuntimeError(f"metric column not found: {current_metric_column}")
                temp[current_metric_column] = _coerce_series(filtered_frame[current_metric_column])
                grouped_metric = temp.groupby(group_column, dropna=False)[current_metric_column]
                suffix = f"{current_metric_column}_{aggregate}"
                metric_frame = _aggregate_numeric(grouped_metric, aggregate).reset_index(name=suffix)
                if frame_out is None:
                    frame_out = metric_frame
                else:
                    frame_out = frame_out.merge(metric_frame, on=group_column, how="outer")
            if frame_out is None:
                raise RuntimeError("metric column not found")
        top_k = max(0, int(plan.get("top_k") or 0))
        if top_k > 0 and len(frame_out.columns) >= 2:
            sort_column = str(frame_out.columns[1])
            frame_out = frame_out.sort_values(by=sort_column, ascending=False, na_position="last").head(top_k)
        result["summary_stats"] = {
            "group_column": group_column,
            "aggregate": aggregate,
            "metric_columns": metric_columns,
            "group_count": int(len(frame_out.index)),
            "matched_count": row_count_after,
            "top_k": top_k,
        }
        result_rows, truncated_count = _finalize_rows(_to_records(frame_out, limit=20), total_count=int(len(frame_out.index)), limit=20)
        result["summary_stats"]["returned_count"] = len(result_rows)
        result["summary_stats"]["truncated_count"] = truncated_count
        if truncated_count > 0:
            result["warnings"].append(f"分组结果较多，仅展示前 {len(result_rows)} 组。")
        result["result_rows"] = result_rows
        return result

    for current_metric_column in metric_columns:
        if current_metric_column not in filtered_frame.columns:
            raise RuntimeError(f"metric column not found: {current_metric_column}")

    if operation == "aggregate":
        aggregate = str(plan.get("aggregate") or "mean")
        values: dict[str, Any] = {}
        for current_metric_column in metric_columns:
            value = _aggregate_numeric(_coerce_series(filtered_frame[current_metric_column]), aggregate)
            values[current_metric_column] = None if value != value else float(value)
        result["summary_stats"] = {
            "aggregate": aggregate,
            "metric_column": metric_column,
            "metric_columns": metric_columns,
            "value": values.get(metric_column),
            "value_map": values,
            "matched_count": row_count_after,
        }
        result_rows, truncated_count = _finalize_rows(_to_records(filtered_frame[metric_columns], limit=10), total_count=row_count_after, limit=10)
        result["summary_stats"]["returned_count"] = len(result_rows)
        result["summary_stats"]["truncated_count"] = truncated_count
        if truncated_count > 0:
            result["warnings"].append(f"原始样例较多，仅展示前 {len(result_rows)} 条。")
        result["result_rows"] = result_rows
        return result

    if operation in {"topk_desc", "topk_asc"}:
        numeric = _coerce_series(filtered_frame[metric_column])
        temp = filtered_frame.copy()
        temp[metric_column] = numeric
        ascending = operation == "topk_asc"
        temp = temp.sort_values(by=metric_column, ascending=ascending, na_position="last")
        top_k = max(1, int(plan.get("top_k") or 5))
        sliced = temp.head(top_k)
        result["summary_stats"] = {
            "metric_column": metric_column,
            "top_k": top_k,
            "sort_order": "asc" if ascending else "desc",
            "matched_count": row_count_after,
        }
        result_rows, truncated_count = _finalize_rows(_to_records(sliced, limit=top_k), total_count=int(len(sliced.index)), limit=top_k)
        result["summary_stats"]["returned_count"] = len(result_rows)
        result["summary_stats"]["truncated_count"] = truncated_count
        result["result_rows"] = result_rows
        return result

    raise RuntimeError(f"unsupported tabular operation: {operation}")


def execute_compare_plan(*, workbooks: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    aggregate = str(plan.get("aggregate") or "count")
    metric_columns = _resolved_metric_columns(plan)
    metric_column = metric_columns[0] if metric_columns else ""
    top_k = max(0, int(plan.get("top_k") or 0))
    sheet_map = plan.get("sheet_map") if isinstance(plan.get("sheet_map"), dict) else {}
    metric_column_map = plan.get("metric_column_map") if isinstance(plan.get("metric_column_map"), dict) else {}
    metric_column_maps = plan.get("metric_column_maps") if isinstance(plan.get("metric_column_maps"), dict) else {}
    group_column = str(plan.get("group_column") or "")
    group_column_map = plan.get("group_column_map") if isinstance(plan.get("group_column_map"), dict) else {}
    filter_map = plan.get("filter_map") if isinstance(plan.get("filter_map"), dict) else {}
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    grouped_compare = bool(group_column_map)
    group_rows: dict[str, dict[str, Any]] = {}
    for workbook in workbooks:
        file_id = int(workbook.get("file_id") or 0)
        target_sheet = str(sheet_map.get(file_id) or plan.get("sheet_name") or "")
        frame = None
        for sheet in workbook.get("sheets") or []:
            if str(sheet.get("sheet_name") or "") == target_sheet:
                frame = sheet.get("dataframe")
                break
        if frame is None:
            warnings.append(f"文件 {workbook.get('file_name')} 缺少工作表 {target_sheet}")
            continue
        effective_filters = filter_map.get(file_id) if file_id in filter_map else (plan.get("filters") or [])
        filtered_frame, filter_warnings = _apply_filters(frame, effective_filters or [])
        warnings.extend(filter_warnings)
        file_name = str(workbook.get("file_name") or "")
        if grouped_compare:
            current_group_column = str(group_column_map.get(file_id) or group_column)
            if current_group_column not in filtered_frame.columns:
                warnings.append(f"文件 {file_name} 缺少分组列 {current_group_column}")
                continue
            if aggregate == "count":
                grouped_frame = filtered_frame.groupby(current_group_column, dropna=False).size().reset_index(name="value")
                value_columns = ["value"]
            else:
                temp = filtered_frame.copy()
                grouped_frame = None
                value_columns: list[str] = []
                for base_metric_column in metric_columns:
                    current_metric_column = str(
                        (metric_column_maps.get(file_id) or {}).get(base_metric_column)
                        or metric_column_map.get(file_id)
                        or base_metric_column
                    )
                    if current_metric_column not in filtered_frame.columns:
                        warnings.append(f"文件 {file_name} 缺少列 {current_metric_column}")
                        continue
                    temp[current_metric_column] = _coerce_series(filtered_frame[current_metric_column])
                    grouped = temp.groupby(current_group_column, dropna=False)[current_metric_column]
                    value_column = (
                        "value"
                        if len(metric_columns) == 1
                        else f"{base_metric_column}_{aggregate}"
                    )
                    value_columns.append(value_column)
                    metric_frame = _aggregate_numeric(grouped, aggregate).reset_index(name=value_column)
                    if grouped_frame is None:
                        grouped_frame = metric_frame
                    else:
                        grouped_frame = grouped_frame.merge(metric_frame, on=current_group_column, how="outer")
                if grouped_frame is None:
                    continue
            for record in grouped_frame.to_dict(orient="records"):
                group_value = str(record.get(current_group_column))
                row = group_rows.setdefault(group_value, {group_column: group_value})
                for value_column in value_columns:
                    value = record.get(value_column)
                    if hasattr(value, "item"):
                        try:
                            value = value.item()
                        except Exception:
                            pass
                    output_key = file_name if len(metric_columns) == 1 and value_column == "value" else f"{file_name}:{value_column}"
                    row[output_key] = value
            continue
        row = {
            "file_name": file_name,
            "sheet_name": target_sheet,
            "matched_count": int(len(filtered_frame.index)),
        }
        if aggregate == "count":
            row["value"] = int(len(filtered_frame.index))
        else:
            for base_metric_column in metric_columns:
                current_metric_column = str(
                    (metric_column_maps.get(file_id) or {}).get(base_metric_column)
                    or metric_column_map.get(file_id)
                    or base_metric_column
                )
                if current_metric_column not in filtered_frame.columns:
                    warnings.append(f"文件 {workbook.get('file_name')} 缺少列 {current_metric_column}")
                    continue
                value = _aggregate_numeric(_coerce_series(filtered_frame[current_metric_column]), aggregate)
                output_value = None if value != value else float(value)
                if len(metric_columns) == 1:
                    row["value"] = output_value
                row[f"{base_metric_column}_{aggregate}"] = output_value
            if len(metric_columns) == 1 and "value" not in row:
                row["value"] = row.get(f"{metric_column}_{aggregate}")
        rows.append(row)
    if grouped_compare:
        rows = list(group_rows.values())
        if top_k > 0 and rows:
            def _group_sort_key(row: dict[str, Any]) -> float:
                numeric_values: list[float] = []
                for key, value in row.items():
                    if key == group_column:
                        continue
                    try:
                        numeric_values.append(float(value))
                    except (TypeError, ValueError):
                        continue
                if not numeric_values:
                    return float("-inf")
                return max(numeric_values)

            rows = sorted(rows, key=_group_sort_key, reverse=True)[:top_k]
    elif top_k > 0 and rows:
        rows = sorted(rows, key=lambda row: float(row.get("value") or float("-inf")), reverse=True)[:top_k]
    total_rows = len(rows)
    rows, truncated_count = _finalize_rows(rows, total_count=total_rows, limit=20)
    if truncated_count > 0:
        warnings.append(f"对比结果较多，仅展示前 {len(rows)} 条。")
    return {
        "sheet_name": str(plan.get("sheet_name") or ""),
        "operation": "compare_tables",
        "metric_column": metric_column,
        "metric_columns": metric_columns,
        "group_column": group_column,
        "filters": plan.get("filters") or [],
        "row_count_before": 0,
        "row_count_after": len(rows),
        "warnings": warnings,
        "result_rows": rows,
        "summary_stats": {
            "aggregate": aggregate,
            "metric_column": metric_column,
            "metric_columns": metric_columns,
            "group_column": group_column,
            "grouped_compare": int(grouped_compare),
            "top_k": top_k,
            "table_count": len(workbooks),
            "returned_count": len(rows),
            "truncated_count": truncated_count,
        },
    }


__all__ = ["execute_compare_plan", "execute_tabular_plan"]
