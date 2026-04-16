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
_AGGREGATE_MAX_KEYWORDS = ("最大", "最高", "max")
_AGGREGATE_MIN_KEYWORDS = ("最小", "最低", "min")
_GROUPED_AGGREGATE_KEYWORDS = ("统计", "均值", "平均", "总和", "合计", "sum", "count")


def _contains_any(question: str, keywords: tuple[str, ...]) -> bool:
    lowered = str(question or "").lower()
    return any(str(keyword).lower() in lowered for keyword in keywords)


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
        value = re.split(r"(?:时|且|并且|,|，|。|；|;|\s)", value, maxsplit=1)[0].strip()
        column = normalized_map.get(column_hint)
        if not column:
            candidates: list[tuple[int, int, str]] = []
            for normalized_name, original_name in normalized_map.items():
                if not normalized_name:
                    continue
                if normalized_name in column_hint:
                    candidates.append((column_hint.rfind(normalized_name), len(normalized_name), original_name))
                    continue
                if column_hint in normalized_name:
                    candidates.append((0, len(normalized_name), original_name))
            if candidates:
                candidates.sort(reverse=True)
                column = candidates[0][2]
        if not column or not value:
            continue
        filters.append({"column": column, "value": value})
    return filters


def _is_summary_intent(question: str) -> bool:
    return _contains_any(question, _SUMMARY_KEYWORDS)


def _is_lookup_intent(question: str) -> bool:
    lowered = str(question or "").lower()
    return any(keyword in lowered for keyword in _LOOKUP_KEYWORDS)


def _is_explicit_aggregate_intent(question: str) -> bool:
    return (
        _contains_any(question, _AGGREGATE_MEAN_KEYWORDS)
        or _contains_any(question, _AGGREGATE_SUM_KEYWORDS)
        or _contains_any(question, _AGGREGATE_COUNT_KEYWORDS)
        or _contains_any(question, _AGGREGATE_MAX_KEYWORDS)
        or _contains_any(question, _AGGREGATE_MIN_KEYWORDS)
    )


def _is_grouped_aggregate_intent(question: str, *, group_by: str) -> bool:
    return bool(group_by) and _contains_any(question, _GROUPED_AGGREGATE_KEYWORDS)


def _is_multi_table_compare_intent(question: str) -> bool:
    return _contains_any(question, _COMPARE_KEYWORDS)


def _pick_compare_aggregate(question: str) -> str:
    if _contains_any(question, _AGGREGATE_MEAN_KEYWORDS):
        return "mean"
    if _contains_any(question, _AGGREGATE_SUM_KEYWORDS):
        return "sum"
    if _contains_any(question, _AGGREGATE_MAX_KEYWORDS):
        return "max"
    if _contains_any(question, _AGGREGATE_MIN_KEYWORDS):
        return "min"
    return "count"


def _get_sheet_profile_by_name(profile: dict[str, Any], sheet_name: str) -> dict[str, Any] | None:
    for sheet in profile.get("sheets") or []:
        if str(sheet.get("sheet_name") or "") == str(sheet_name or ""):
            return sheet
    return None


def _resolve_column_hint(sheet: dict[str, Any], hint: str, *, numeric_only: bool | None = None) -> str:
    raw_hint = str(hint or "").strip()
    if not raw_hint:
        return ""
    normalized_hint = normalize_identifier(raw_hint)
    best_name = ""
    best_score = 0
    for column in sheet.get("columns") or []:
        name = str(column.get("name") or "").strip()
        if not name:
            continue
        if numeric_only is True and not column.get("is_numeric"):
            continue
        if numeric_only is False and column.get("is_numeric"):
            continue
        normalized_name = str(column.get("normalized_name") or normalize_identifier(name))
        score = 0
        if raw_hint == name:
            score += 10
        if normalized_hint and normalized_hint == normalized_name:
            score += 8
        if raw_hint.lower() in name.lower():
            score += 5
        if normalized_hint and normalized_hint in normalized_name:
            score += 4
        if name.lower() in raw_hint.lower():
            score += 2
        if score > best_score:
            best_score = score
            best_name = name
    return best_name if best_score > 0 else ""


def _match_sheet_across_profiles(question: str, profiles: list[dict[str, Any]]) -> tuple[dict[int, str], dict[str, str] | None]:
    sheet_map: dict[int, str] = {}
    common_sheet_names: set[str] | None = None
    common_name_map: dict[str, str] = {}
    unresolved_profiles: list[dict[str, Any]] = []
    normalized_question = normalize_identifier(question)

    for profile in profiles:
        file_id = int(profile.get("file_id") or 0)
        sheets = [sheet for sheet in (profile.get("sheets") or []) if isinstance(sheet, dict)]
        if not sheets:
            continue

        normalized_to_name = {
            normalize_identifier(str(sheet.get("sheet_name") or "")): str(sheet.get("sheet_name") or "")
            for sheet in sheets
            if str(sheet.get("sheet_name") or "")
        }
        if common_sheet_names is None:
            common_sheet_names = set(normalized_to_name.keys())
            common_name_map = dict(normalized_to_name)
        else:
            common_sheet_names &= set(normalized_to_name.keys())
            for normalized_name, original_name in normalized_to_name.items():
                common_name_map.setdefault(normalized_name, original_name)

        if len(sheets) == 1:
            sheet_map[file_id] = str(sheets[0].get("sheet_name") or "")
            continue

        matched_names = [
            str(sheet.get("sheet_name") or "")
            for sheet in sheets
            if normalize_identifier(str(sheet.get("sheet_name") or "")) in normalized_question
        ]
        if len(matched_names) == 1:
            sheet_map[file_id] = matched_names[0]
            continue

        unresolved_profiles.append(profile)

    if not unresolved_profiles:
        return sheet_map, None

    if common_sheet_names and len(common_sheet_names) == 1:
        normalized_name = next(iter(common_sheet_names))
        shared_name = common_name_map.get(normalized_name) or normalized_name
        for profile in unresolved_profiles:
            file_id = int(profile.get("file_id") or 0)
            sheet_map[file_id] = shared_name
        return sheet_map, None

    candidate_rows: list[str] = []
    for profile in unresolved_profiles[:3]:
        file_name = str(profile.get("file_name") or "")
        sheet_names = [str(item.get("sheet_name") or "") for item in (profile.get("sheets") or [])[:5]]
        candidate_rows.append(f"{file_name}: {', '.join(sheet_names)}")
    return {}, {
        "message": "多表对比时未能唯一定位工作表，请指定 sheet 名。可选: " + " | ".join(candidate_rows),
        "reason": "sheet_compare_ambiguous",
    }


def _resolve_column_across_profiles(
    *,
    profiles: list[dict[str, Any]],
    sheet_map: dict[int, str],
    base_column_name: str,
    numeric_only: bool | None,
) -> tuple[dict[int, str], dict[str, str] | None]:
    column_map: dict[int, str] = {}
    for profile in profiles:
        file_id = int(profile.get("file_id") or 0)
        sheet_name = str(sheet_map.get(file_id) or "")
        sheet_profile = _get_sheet_profile_by_name(profile, sheet_name)
        if sheet_profile is None:
            return {}, {
                "message": f"文件 {profile.get('file_name')} 缺少工作表 {sheet_name}，无法执行多表对比。",
                "reason": "compare_sheet_missing",
            }
        matched = _resolve_column_hint(sheet_profile, base_column_name, numeric_only=numeric_only)
        if not matched:
            kind = "数值列" if numeric_only else "分组列"
            return {}, {
                "message": f"文件 {profile.get('file_name')} 缺少与 {base_column_name} 对应的{kind}，无法执行多表对比。",
                "reason": "compare_column_missing",
            }
        column_map[file_id] = matched
    return column_map, None


def _resolve_columns_across_profiles(
    *,
    profiles: list[dict[str, Any]],
    sheet_map: dict[int, str],
    base_column_names: list[str],
    numeric_only: bool | None,
) -> tuple[dict[int, dict[str, str]], dict[str, str] | None]:
    mapped_by_file: dict[int, dict[str, str]] = {}
    for base_column_name in base_column_names:
        column_map, clarify = _resolve_column_across_profiles(
            profiles=profiles,
            sheet_map=sheet_map,
            base_column_name=base_column_name,
            numeric_only=numeric_only,
        )
        if clarify:
            return {}, clarify
        for file_id, mapped_column in column_map.items():
            mapped_by_file.setdefault(file_id, {})[str(base_column_name)] = str(mapped_column)
    return mapped_by_file, None


def _resolve_filters_across_profiles(
    *,
    profiles: list[dict[str, Any]],
    sheet_map: dict[int, str],
    filters: list[dict[str, str]],
) -> tuple[dict[int, list[dict[str, str]]], dict[str, str] | None]:
    filter_map: dict[int, list[dict[str, str]]] = {
        int(profile.get("file_id") or 0): [] for profile in profiles
    }
    for filter_item in filters:
        base_column = str(filter_item.get("column") or "")
        if not base_column:
            continue
        column_map, clarify = _resolve_column_across_profiles(
            profiles=profiles,
            sheet_map=sheet_map,
            base_column_name=base_column,
            numeric_only=None,
        )
        if clarify:
            return {}, clarify
        for file_id, mapped_column in column_map.items():
            filter_map.setdefault(file_id, []).append(
                {
                    "column": mapped_column,
                    "value": str(filter_item.get("value") or ""),
                }
            )
    return filter_map, None


def _build_single_table_plan(*, question: str, profile: dict[str, Any]) -> dict[str, Any]:
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
    elif _contains_any(question, _AGGREGATE_MAX_KEYWORDS):
        aggregate = "max"
    elif _contains_any(question, _AGGREGATE_MIN_KEYWORDS):
        aggregate = "min"
    else:
        aggregate = "mean"

    metric_columns = explicit_metric_columns if operation == "summary" else fallback_metric_columns

    return {
        "needs_clarification": False,
        "clarification_message": "",
        "clarification_reason": "",
        "operation": operation,
        "sheet_name": str(sheet.get("sheet_name") or ""),
        "sheet_map": {},
        "metric_column": metric_columns[0] if metric_columns else "",
        "metric_columns": metric_columns,
        "metric_column_map": {},
        "metric_column_maps": {},
        "focus_columns": focus_columns,
        "group_by": group_by,
        "group_column": group_by,
        "group_column_map": {},
        "lookup_columns": lookup_columns,
        "filters": filters,
        "filter_map": {},
        "aggregate": aggregate,
    }


def _plan_multi_table_compare(
    *,
    question: str,
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    sheet_map, clarify = _match_sheet_across_profiles(question, profiles)
    if clarify:
        return {
            "needs_clarification": True,
            "clarification_message": clarify["message"],
            "clarification_reason": clarify["reason"],
        }

    primary_profile = profiles[0]
    primary_file_id = int(primary_profile.get("file_id") or 0)
    primary_sheet_name = str(
        sheet_map.get(primary_file_id)
        or next(iter(sheet_map.values()), "")
    )
    primary_sheet = _get_sheet_profile_by_name(primary_profile, primary_sheet_name)
    if primary_sheet is None:
        return {
            "needs_clarification": True,
            "clarification_message": "未找到可用于多表对比的工作表。",
            "clarification_reason": "sheet_missing",
        }

    aggregate = _pick_compare_aggregate(question)
    filters = _extract_filters(question, primary_sheet)
    group_by = _pick_group_by(question, primary_sheet)
    metric_columns: list[str] = []
    metric_column_map: dict[int, str] = {}
    metric_column_maps: dict[int, dict[str, str]] = {}
    group_column_map: dict[int, str] = {}
    filter_map: dict[int, list[dict[str, str]]] = {}

    if group_by:
        group_column_map, clarify = _resolve_column_across_profiles(
            profiles=profiles,
            sheet_map=sheet_map,
            base_column_name=group_by,
            numeric_only=False,
        )
        if clarify:
            return {
                "needs_clarification": True,
                "clarification_message": clarify["message"],
                "clarification_reason": clarify["reason"],
            }

    if aggregate != "count":
        metric_columns = _pick_metric_columns(question, primary_sheet, allow_fallback=False) or _pick_metric_columns(
            question,
            primary_sheet,
        )
        metric_column_maps, clarify = _resolve_columns_across_profiles(
            profiles=profiles,
            sheet_map=sheet_map,
            base_column_names=metric_columns,
            numeric_only=True,
        )
        if clarify:
            return {
                "needs_clarification": True,
                "clarification_message": clarify["message"],
                "clarification_reason": clarify["reason"],
            }
        if metric_columns:
            first_metric = metric_columns[0]
            metric_column_map = {
                file_id: str(mapped.get(first_metric) or "")
                for file_id, mapped in metric_column_maps.items()
                if str(mapped.get(first_metric) or "")
            }

    if filters:
        filter_map, clarify = _resolve_filters_across_profiles(
            profiles=profiles,
            sheet_map=sheet_map,
            filters=filters,
        )
        if clarify:
            return {
                "needs_clarification": True,
                "clarification_message": clarify["message"],
                "clarification_reason": clarify["reason"],
            }

    return {
        "needs_clarification": False,
        "clarification_message": "",
        "clarification_reason": "",
        "operation": "compare_tables",
        "sheet_name": primary_sheet_name,
        "sheet_map": sheet_map,
        "metric_column": metric_columns[0] if metric_columns else "",
        "metric_columns": metric_columns,
        "metric_column_map": metric_column_map,
        "metric_column_maps": metric_column_maps,
        "focus_columns": [],
        "group_by": group_by,
        "group_column": group_by,
        "group_column_map": group_column_map,
        "lookup_columns": [],
        "filters": filters,
        "filter_map": filter_map,
        "aggregate": aggregate,
    }


def plan_tabular_query(
    *,
    question: str,
    profile: dict[str, Any],
    profiles: list[dict[str, Any]] | None = None,
    workbook_count: int = 1,
) -> dict[str, Any]:
    profile_list = [item for item in (profiles or []) if isinstance(item, dict)]
    if not profile_list:
        profile_list = [profile]

    if workbook_count >= 2 and len(profile_list) >= 2 and _is_multi_table_compare_intent(question):
        return _plan_multi_table_compare(question=question, profiles=profile_list)

    return _build_single_table_plan(question=question, profile=profile_list[0])


__all__ = ["plan_tabular_query"]
