from __future__ import annotations

import re
from typing import Any

from .schema_profiler import normalize_identifier


_NUMERIC_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
_SEMANTIC_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("voltage", "volt", "电压"),
    ("temperature", "temp", "温度"),
    ("current", "电流"),
    ("capacity", "容量"),
    ("energy", "能量"),
    ("power", "功率"),
    ("time", "timestamp", "datetime", "date", "时间", "日期"),
    ("status", "state", "condition", "状态"),
    ("cycle", "cycles", "循环", "循环次数"),
    ("soc", "荷电状态"),
    ("soh", "健康状态"),
    ("resistance", "impedance", "内阻", "阻抗", "电阻"),
)
_FILTER_VALUE_STOPWORDS = {"多少", "几条", "几项", "多少条", "数量", "条数", "总数", "记录数"}
_FILTER_VALUE_SUFFIX_PATTERNS: tuple[str, ...] = (
    r"的?(?:有哪些|有哪几项|有哪些记录|有哪些数据|有哪些行)$",
    r"的?(?:记录|数据|结果|行)$",
)
_COMPOUND_SPLIT_PATTERN = re.compile(r"[？?；;]+")


def _question_text(question: str) -> str:
    return str(question or "").strip()


def _normalized_question(question: str) -> str:
    return normalize_identifier(question)


def _contains_any(question: str, keywords: list[str]) -> bool:
    text = _question_text(question).lower()
    return any(keyword in text for keyword in keywords)


def _score_name_match(question: str, name: str) -> int:
    raw_q = _question_text(question).lower()
    raw_name = str(name or "").strip().lower()
    normalized_q = _normalized_question(question)
    normalized_name = normalize_identifier(name)
    score = 0
    if raw_name and raw_name in raw_q:
        score += 6
    if normalized_name and normalized_name in normalized_q:
        score += 4
    if raw_name and any(token and token in raw_q for token in re.split(r"[_\s/\-]+", raw_name) if len(token) >= 2):
        score += 1
    score += _semantic_match_score(question, name)
    return score


def _extract_text_tokens(text: str) -> tuple[set[str], str, str]:
    raw = str(text or "").strip().lower()
    normalized = normalize_identifier(text)
    tokens = set(re.findall(r"[a-zA-Z]+|[\u4e00-\u9fff]{1,8}", raw))
    if normalized:
        tokens.add(normalized)
    return tokens, raw, normalized


def _semantic_alias_groups_for_text(text: str) -> set[int]:
    tokens, raw, normalized = _extract_text_tokens(text)
    matched: set[int] = set()
    for idx, group in enumerate(_SEMANTIC_ALIAS_GROUPS):
        for alias in group:
            alias_raw = str(alias or "").strip().lower()
            alias_normalized = normalize_identifier(alias_raw)
            if not alias_raw:
                continue
            if re.fullmatch(r"[a-z]+", alias_raw):
                if alias_raw in tokens or alias_normalized == normalized:
                    matched.add(idx)
                    break
            else:
                if alias_raw in raw or (alias_normalized and alias_normalized in normalized):
                    matched.add(idx)
                    break
    return matched


def _semantic_match_score(question: str, name: str) -> int:
    shared = _semantic_alias_groups_for_text(question) & _semantic_alias_groups_for_text(name)
    return 6 * len(shared)


def _extract_hint_token(question: str, patterns: list[str]) -> str:
    text = _question_text(question)
    for pattern in patterns:
        matched = re.search(pattern, text, flags=re.IGNORECASE)
        if not matched:
            continue
        token = str(matched.group(1) or "").strip()
        token = re.sub(r"[，。；,;:：].*$", "", token).strip()
        if token:
            return token
    return ""


def _resolve_column_hint(sheet_profile: dict[str, Any], hint: str, *, numeric_only: bool | None = None) -> str:
    raw_hint = str(hint or "").strip()
    if not raw_hint:
        return ""
    normalized_hint = normalize_identifier(raw_hint)
    best_name = ""
    best_score = 0
    for column in sheet_profile.get("columns") or []:
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
        if raw_hint and raw_hint.lower() in name.lower():
            score += 5
        if normalized_hint and normalized_hint in normalized_name:
            score += 4
        if name.lower() in raw_hint.lower():
            score += 2
        score += _semantic_match_score(raw_hint, name)
        if score > best_score:
            best_score = score
            best_name = name
    return best_name if best_score > 0 else ""


def _match_sheet(question: str, profile: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    sheets = profile.get("sheets") or []
    if not sheets:
        return None, None
    if len(sheets) == 1:
        return sheets[0], None
    index_match = re.search(r"第\s*(\d+)\s*(?:个)?(?:sheet|工作表)", _question_text(question), flags=re.IGNORECASE)
    if index_match:
        idx = int(index_match.group(1)) - 1
        if 0 <= idx < len(sheets):
            return sheets[idx], None
        return None, {
            "message": f"当前文件只有 {len(sheets)} 个工作表，无法命中第 {idx + 1} 个工作表。",
            "reason": "sheet_index_out_of_range",
        }

    candidates: list[tuple[int, dict[str, Any]]] = []
    for sheet in sheets:
        score = _score_name_match(question, sheet.get("sheet_name") or "")
        for column in sheet.get("columns") or []:
            col_score = _score_name_match(question, column.get("name") or "")
            if col_score > 0:
                score += min(4, col_score)
        if score > 0:
            candidates.append((score, sheet))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) == 1 or (len(candidates) >= 2 and candidates[0][0] > candidates[1][0]):
        return candidates[0][1], None

    if candidates:
        sheet_names = [str(item[1].get("sheet_name") or "") for item in candidates[:5]]
    else:
        sheet_names = [str(item.get("sheet_name") or "") for item in sheets[:5]]
    return None, {
        "message": "该文件包含多个工作表，请指定 sheet 名后再提问。可选: " + ", ".join(sheet_names),
        "candidates": sheet_names,
        "reason": "sheet_ambiguous",
    }


def _match_sheet_across_profiles(question: str, profiles: list[dict[str, Any]]) -> tuple[dict[int, str], dict[str, Any] | None]:
    sheet_map: dict[int, str] = {}
    common_sheet_names: dict[str, str] = {}
    initialized_common = False
    unresolved_profiles: list[dict[str, Any]] = []

    for profile in profiles:
        file_id = int(profile.get("file_id") or 0)
        sheets = profile.get("sheets") or []
        normalized_to_name = {
            str(item.get("normalized_sheet_name") or ""): str(item.get("sheet_name") or "")
            for item in sheets
            if str(item.get("normalized_sheet_name") or "")
        }
        if not initialized_common:
            common_sheet_names = dict(normalized_to_name)
            initialized_common = True
        else:
            common_sheet_names = {
                key: normalized_to_name[key]
                for key in common_sheet_names.keys()
                if key in normalized_to_name
            }

        matched_sheet, clarify = _match_sheet(question, profile)
        if matched_sheet is not None:
            sheet_map[file_id] = str(matched_sheet.get("sheet_name") or "")
            continue
        if len(sheets) == 1:
            sheet_map[file_id] = str(sheets[0].get("sheet_name") or "")
            continue
        unresolved_profiles.append(profile)

    if not unresolved_profiles:
        return sheet_map, None

    if len(common_sheet_names) == 1:
        shared_name = next(iter(common_sheet_names.values()))
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


def _match_columns(question: str, sheet_profile: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for column in sheet_profile.get("columns") or []:
        score = _score_name_match(question, column.get("name") or "")
        if score > 0:
            candidates.append((score, column))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates]


def _get_sheet_profile_by_name(profile: dict[str, Any], sheet_name: str) -> dict[str, Any] | None:
    for item in profile.get("sheets") or []:
        if str(item.get("sheet_name") or "") == str(sheet_name or ""):
            return item
    return None


def _resolve_column_across_profiles(
    *,
    profiles: list[dict[str, Any]],
    sheet_map: dict[int, str],
    base_column_name: str,
    numeric_only: bool | None,
) -> tuple[dict[int, str], dict[str, Any] | None]:
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
) -> tuple[dict[int, dict[str, str]], dict[str, Any] | None]:
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
            mapped_by_file.setdefault(int(file_id), {})[str(base_column_name)] = str(mapped_column)
    return mapped_by_file, None


def _resolve_filters_across_profiles(
    *,
    profiles: list[dict[str, Any]],
    sheet_map: dict[int, str],
    filters: list[dict[str, Any]],
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any] | None]:
    filter_map: dict[int, list[dict[str, Any]]] = {
        int(profile.get("file_id") or 0): [] for profile in profiles
    }
    for filter_item in filters or []:
        base_column = str(filter_item.get("column") or "")
        op = str(filter_item.get("op") or "==")
        numeric_only = op in {">", ">=", "<", "<="}
        column_map, clarify = _resolve_column_across_profiles(
            profiles=profiles,
            sheet_map=sheet_map,
            base_column_name=base_column,
            numeric_only=True if numeric_only else None,
        )
        if clarify:
            return {}, clarify
        for file_id, mapped_column in column_map.items():
            filter_map.setdefault(int(file_id), []).append(
                {
                    "column": mapped_column,
                    "op": op,
                    "value": filter_item.get("value"),
                }
            )
    return filter_map, None


def _pick_metric_column(
    question: str,
    sheet_profile: dict[str, Any],
    *,
    excluded_columns: set[str] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    excluded = {str(item) for item in (excluded_columns or set()) if str(item)}
    explicit_hint = _extract_hint_token(
        question,
        [
            r"统计\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(?:平均值|均值|平均|求和|总和|合计|总计|最大值|最小值|最大|最小)",
            r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(?:平均值|均值|平均|求和|总和|合计|总计|最大值|最小值|最大|最小)",
        ],
    )
    explicit_match = _resolve_column_hint(sheet_profile, explicit_hint, numeric_only=True)
    if explicit_match and explicit_match not in excluded:
        return explicit_match, None
    matches = _match_columns(question, sheet_profile)
    numeric_matches = [
        item
        for item in matches
        if item.get("is_numeric") and str(item.get("name") or "") not in excluded
    ]
    if len(numeric_matches) == 1:
        return str(numeric_matches[0].get("name") or ""), None
    if len(numeric_matches) > 1:
        return None, {
            "message": "命中了多个数值列，请明确要统计的列名。候选: "
            + ", ".join(str(item.get("name") or "") for item in numeric_matches[:5]),
            "reason": "column_ambiguous",
        }
    numeric_columns = [str(name) for name in sheet_profile.get("numeric_columns") or [] if str(name) not in excluded]
    if len(numeric_columns) == 1:
        return numeric_columns[0], None
    if len(numeric_columns) > 1:
        return None, {
            "message": "当前表格存在多个数值列，请明确统计对象。候选: " + ", ".join(numeric_columns[:5]),
            "reason": "column_missing",
        }
    return None, {"message": "当前表格未识别到可用于统计的数值列。", "reason": "column_missing"}


def _pick_metric_columns(
    question: str,
    sheet_profile: dict[str, Any],
    *,
    excluded_columns: set[str] | None = None,
    allow_multiple: bool = False,
) -> tuple[list[str], dict[str, Any] | None]:
    excluded = {str(item) for item in (excluded_columns or set()) if str(item)}
    explicit_match, clarify = _pick_metric_column(
        question,
        sheet_profile,
        excluded_columns=excluded,
    )
    if clarify and not allow_multiple:
        return [], clarify

    scored_matches: list[tuple[int, str]] = []
    for column in sheet_profile.get("columns") or []:
        name = str(column.get("name") or "")
        if not name or not column.get("is_numeric") or name in excluded:
            continue
        score = _score_name_match(question, name)
        if score > 0:
            scored_matches.append((score, name))
    scored_matches.sort(key=lambda item: item[0], reverse=True)

    selected: list[str] = []
    if explicit_match:
        selected.append(str(explicit_match))
    if allow_multiple:
        for score, name in scored_matches:
            if score < 4 or name in selected:
                continue
            selected.append(name)
            if len(selected) >= 3:
                break
        if len(selected) >= 2:
            return selected, None
    if explicit_match:
        return [str(explicit_match)], None
    if clarify:
        return [], clarify
    return [], {"message": "当前表格未识别到可用于统计的数值列。", "reason": "column_missing"}


def _pick_lookup_columns(
    question: str,
    sheet_profile: dict[str, Any],
    *,
    excluded_columns: set[str] | None = None,
) -> tuple[list[str], dict[str, Any] | None]:
    excluded = {str(item) for item in (excluded_columns or set()) if str(item)}
    scored_matches: list[tuple[int, str]] = []
    for column in sheet_profile.get("columns") or []:
        name = str(column.get("name") or "")
        if not name or name in excluded:
            continue
        score = _score_name_match(question, name)
        if score > 0:
            scored_matches.append((score, name))
    scored_matches.sort(key=lambda item: item[0], reverse=True)
    selected: list[str] = []
    for score, name in scored_matches:
        if score < 4 or name in selected:
            continue
        selected.append(name)
        if len(selected) >= 3:
            break
    if selected:
        return selected, None
    remaining_columns = [
        str(item.get("name") or "")
        for item in (sheet_profile.get("columns") or [])
        if str(item.get("name") or "") and str(item.get("name") or "") not in excluded
    ]
    if len(remaining_columns) == 1:
        return remaining_columns, None
    return [], {
        "message": "未能明确要取值的列，请在问题中补充目标列名。",
        "reason": "lookup_column_missing",
    }


def _apply_selection_operation_guards(
    operation: str,
    extra: dict[str, Any],
    *,
    route_hint: str = "",
    table_file_count: int = 0,
) -> tuple[str, dict[str, Any], bool]:
    guard_applied = False
    route = str(route_hint or "").strip().lower()
    if route == "hybrid_qa" and operation == "compare_tables":
        guard_applied = True
        return "summary", {}, guard_applied
    if int(table_file_count or 0) < 2 and operation == "compare_tables":
        guard_applied = True
        return "summary", {}, guard_applied
    return operation, extra, guard_applied


def _detect_operation(question: str) -> tuple[str, dict[str, Any]]:
    text = _question_text(question).lower()
    top_k = 0
    k_match = re.search(r"前\s*(\d+)", text) or re.search(r"top\s*(\d+)", text)
    if k_match:
        top_k = max(1, min(int(k_match.group(1)), 20))
    if _contains_any(question, ["对比", "比较", "差异", "区别"]):
        aggregate = "count"
        if _contains_any(question, ["平均", "均值", "平均值"]):
            aggregate = "mean"
        elif _contains_any(question, ["求和", "总和", "合计", "总计"]):
            aggregate = "sum"
        elif _contains_any(question, ["最大值", "最高"]):
            aggregate = "max"
        elif _contains_any(question, ["最小值", "最低"]):
            aggregate = "min"
        return "compare_tables", {"aggregate": aggregate, "top_k": top_k}
    if _contains_any(question, ["按", "按照", "各", "每个"]) and _contains_any(question, ["统计", "分布", "数量", "均值", "平均", "求和", "总和"]):
        aggregate = "count"
        if _contains_any(question, ["平均", "均值", "平均值"]):
            aggregate = "mean"
        elif _contains_any(question, ["求和", "总和", "合计", "总计"]):
            aggregate = "sum"
        elif _contains_any(question, ["最大值", "最高"]):
            aggregate = "max"
        elif _contains_any(question, ["最小值", "最低"]):
            aggregate = "min"
        return "groupby", {"aggregate": aggregate, "top_k": top_k}
    if _contains_any(question, ["趋势", "变化趋势", "变化情况"]) or (
        _contains_any(question, ["变化", "随"]) and _contains_any(question, ["时间", "日期", "循环", "cycle", "time", "date"])
    ):
        return "trend", {}
    if _contains_any(question, ["前", "top", "最高", "最大"]):
        if _contains_any(question, ["前", "top", "最高"]):
            k_match = re.search(r"前\s*(\d+)", text) or re.search(r"top\s*(\d+)", text)
            top_k = int(k_match.group(1)) if k_match else 5
            return "topk_desc", {"top_k": max(1, min(top_k, 20))}
    if _contains_any(question, ["后", "最低", "最小"]):
        if _contains_any(question, ["后", "最低"]):
            k_match = re.search(r"后\s*(\d+)", text)
            top_k = int(k_match.group(1)) if k_match else 5
            return "topk_asc", {"top_k": max(1, min(top_k, 20))}
    if _contains_any(question, ["平均", "均值", "平均值"]):
        return "aggregate", {"aggregate": "mean"}
    if _contains_any(question, ["求和", "总和", "合计", "总计"]):
        return "aggregate", {"aggregate": "sum"}
    if _contains_any(question, ["最大值", "max"]):
        return "aggregate", {"aggregate": "max"}
    if _contains_any(question, ["最小值", "min"]):
        return "aggregate", {"aggregate": "min"}
    if _contains_any(question, ["是多少", "是什么", "对应值", "取值"]):
        return "lookup", {}
    if _contains_any(question, ["多少条", "几条", "条数", "总数", "数量", "多少个", "几个", "个数"]):
        return "count_rows", {}
    if _contains_any(question, ["筛选", "哪些", "列出", "满足", "符合", "大于", "小于", "等于", "为"]):
        return "filter_rows", {}
    return "summary", {}


def _coerce_filter_value(raw: str, *, numeric_preferred: bool = False) -> Any:
    value = str(raw or "").strip().strip("'\"")
    for pattern in _FILTER_VALUE_SUFFIX_PATTERNS:
        value = re.sub(pattern, "", value)
    value = value.strip()
    if numeric_preferred:
        matched = _NUMERIC_PATTERN.search(value)
        if matched:
            value = matched.group(0)
    number = _NUMERIC_PATTERN.fullmatch(value)
    if number:
        if "." in value:
            return float(value)
        return int(value)
    return value


def _split_compound_question(question: str) -> list[str]:
    text = str(question or "").strip()
    if not text:
        return []
    parts = [part.strip(" ,，。！？?;；\"'") for part in _COMPOUND_SPLIT_PATTERN.split(text)]
    cleaned = [part for part in parts if part]
    if len(cleaned) <= 1:
        return cleaned
    return cleaned[:3]


def _extract_filters(question: str, sheet_profile: dict[str, Any]) -> list[dict[str, Any]]:
    text = _question_text(question)
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    keyword_ops = [
        (r"大于等于|不少于|不低于|至少", ">="),
        (r"小于等于|不多于|不高于|至多", "<="),
        (r"大于|高于|超过|多于", ">"),
        (r"小于|低于|少于", "<"),
        (r"等于|为|是", "=="),
    ]
    symbol_pattern = r"\s*(>=|<=|=|>|<)\s*([^，。；,;\s]+)"
    for column in sheet_profile.get("columns") or []:
        column_name = str(column.get("name") or "")
        if not column_name:
            continue
        has_sample_match = False
        if column_name in text:
            for sample_value in column.get("sample_values") or []:
                sample_text = str(sample_value).strip()
                if not sample_text:
                    continue
                if re.fullmatch(r"\d+(?:\.\d+)?", sample_text) and re.search(
                    rf"(?:前|top)\s*{re.escape(sample_text)}(?:\D|$)",
                    text,
                    flags=re.IGNORECASE,
                ):
                    continue
                if sample_text in text:
                    marker = (column_name, "==", sample_text)
                    if marker not in seen:
                        seen.add(marker)
                        filters.append({"column": column_name, "op": "==", "value": sample_text})
                        has_sample_match = True
        name_pattern = re.escape(column_name)
        matched = re.search(name_pattern + symbol_pattern, text, flags=re.IGNORECASE)
        if matched:
            op = matched.group(1)
            if op == "=":
                op = "=="
            value = _coerce_filter_value(matched.group(2), numeric_preferred=(op in {">", ">=", "<", "<="}))
            if op == "==" and str(value).strip() in _FILTER_VALUE_STOPWORDS:
                continue
            marker = (column_name, op, str(value))
            if marker not in seen:
                seen.add(marker)
                filters.append({"column": column_name, "op": op, "value": value})
        for pattern, op in keyword_ops:
            matched = re.search(name_pattern + r"\s*(?:在)?\s*(?:数值)?\s*(?:上)?\s*(?:" + pattern + r")\s*([^，。；,;\s]+)", text, flags=re.IGNORECASE)
            if not matched:
                continue
            if op == "==" and has_sample_match:
                continue
            value = _coerce_filter_value(matched.group(1), numeric_preferred=(op in {">", ">=", "<", "<="}))
            if op == "==" and str(value).strip() in _FILTER_VALUE_STOPWORDS:
                continue
            marker = (column_name, op, str(value))
            if marker in seen:
                continue
            seen.add(marker)
            filters.append({"column": column_name, "op": op, "value": value})
    generic_patterns = [
        (r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(大于等于|不少于|不低于|至少)\s*([^，。；,;\s]+)", ">="),
        (r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(小于等于|不多于|不高于|至多)\s*([^，。；,;\s]+)", "<="),
        (r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(大于|高于|超过|多于)\s*([^，。；,;\s]+)", ">"),
        (r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(小于|低于|少于)\s*([^，。；,;\s]+)", "<"),
        (r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(等于|为|是)\s*([^，。；,;\s]+)", "=="),
        (r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(>=|<=|=|>|<)\s*([^，。；,;\s]+)", None),
    ]
    for pattern, op_hint in generic_patterns:
        for matched in re.finditer(pattern, text, flags=re.IGNORECASE):
            hint = str(matched.group(1) or "").strip()
            value_raw = str(matched.group(3) or "").strip()
            resolved_column = _resolve_column_hint(
                sheet_profile,
                hint,
                numeric_only=(op_hint in {">", ">=", "<", "<="}) if op_hint is not None else None,
            )
            if not resolved_column:
                continue
            op = op_hint
            if op is None:
                symbol = str(matched.group(2) or "=").strip()
                op = "==" if symbol == "=" else symbol
            value = _coerce_filter_value(value_raw, numeric_preferred=(op in {">", ">=", "<", "<="}))
            if op == "==" and str(value).strip() in _FILTER_VALUE_STOPWORDS:
                continue
            marker = (resolved_column, op, str(value))
            if marker in seen:
                continue
            seen.add(marker)
            filters.append({"column": resolved_column, "op": op, "value": value})
    return filters


def _pick_group_column(question: str, sheet_profile: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    explicit_hint = _extract_hint_token(
        question,
        [
            r"(?:按|按照)\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)",
            r"(?:各|每个)\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)",
        ],
    )
    explicit_match = _resolve_column_hint(sheet_profile, explicit_hint, numeric_only=False)
    if explicit_match:
        return explicit_match, None
    matches = _match_columns(question, sheet_profile)
    if len(matches) == 1:
        return str(matches[0].get("name") or ""), None
    for item in matches:
        if not item.get("is_numeric"):
            return str(item.get("name") or ""), None
    column_names = [str(item.get("name") or "") for item in sheet_profile.get("columns") or []]
    if len(column_names) == 1:
        return column_names[0], None
    return None, {
        "message": "未能明确分组列，请在问题中指定列名。",
        "reason": "group_column_missing",
    }


def _pick_trend_axis_column(question: str, sheet_profile: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    explicit_hint = _extract_hint_token(
        question,
        [
            r"(?:按|按照|随)\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(?:变化|趋势)",
            r"([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*(?:趋势|变化趋势)",
        ],
    )
    explicit_match = _resolve_column_hint(sheet_profile, explicit_hint, numeric_only=None)
    if explicit_match:
        return explicit_match, None

    time_like: list[str] = []
    for column in sheet_profile.get("columns") or []:
        name = str(column.get("name") or "")
        if not name:
            continue
        if _semantic_match_score("time date cycle 时间 日期 循环", name) > 0:
            time_like.append(name)
    if len(time_like) == 1:
        return time_like[0], None
    if len(time_like) > 1:
        return None, {
            "message": "命中了多个可能的趋势横轴列，请明确指定，例如 cycle、time 或 date。",
            "reason": "trend_axis_ambiguous",
        }
    non_numeric = [
        str(item.get("name") or "")
        for item in (sheet_profile.get("columns") or [])
        if str(item.get("name") or "") and not item.get("is_numeric")
    ]
    if len(non_numeric) == 1:
        return non_numeric[0], None
    return None, {
        "message": "未能识别趋势横轴列，请明确指定时间/循环/日期列。",
        "reason": "trend_axis_missing",
    }


def _plan_single_tabular_query(
    *,
    question: str,
    profile: dict[str, Any] | None = None,
    profiles: list[dict[str, Any]] | None = None,
    workbook_count: int,
    inherited_filters: list[dict[str, Any]] | None = None,
    route_hint: str = "",
    table_file_count: int = 0,
    selection_strategy: str = "",
) -> dict[str, Any]:
    profile_list = [item for item in (profiles or []) if isinstance(item, dict)]
    if not profile_list:
        if profile is None:
            return {
                "needs_clarification": True,
                "clarification_message": "未找到可用的表格结构信息。",
                "clarification_reason": "profile_missing",
            }
        profile_list = [profile]
    primary_profile = profile_list[0]

    operation, extra = _detect_operation(question)
    operation, extra, _guard_applied = _apply_selection_operation_guards(
        operation,
        extra,
        route_hint=route_hint,
        table_file_count=table_file_count or workbook_count,
    )
    sheet_map: dict[int, str] = {}
    if operation == "compare_tables" and len(profile_list) > 1:
        sheet_map, sheet_clarify = _match_sheet_across_profiles(question, profile_list)
        if not sheet_map and sheet_clarify:
            return {
                "needs_clarification": True,
                "clarification_message": sheet_clarify["message"],
                "clarification_reason": sheet_clarify["reason"],
            }
        first_sheet_name = sheet_map.get(int(primary_profile.get("file_id") or 0), "")
        sheet_profile = next(
            (
                item for item in (primary_profile.get("sheets") or [])
                if str(item.get("sheet_name") or "") == first_sheet_name
            ),
            None,
        )
    else:
        sheet_profile, sheet_clarify = _match_sheet(question, primary_profile)
        if sheet_profile is not None:
            sheet_map[int(primary_profile.get("file_id") or 0)] = str(sheet_profile.get("sheet_name") or "")
        if sheet_clarify:
            return {
                "needs_clarification": True,
                "clarification_message": sheet_clarify["message"],
                "clarification_reason": sheet_clarify["reason"],
            }
    if sheet_clarify:
        return {
            "needs_clarification": True,
            "clarification_message": sheet_clarify["message"],
            "clarification_reason": sheet_clarify["reason"],
        }
    if sheet_profile is None:
        return {
            "needs_clarification": True,
            "clarification_message": "未找到可用的工作表。",
            "clarification_reason": "sheet_missing",
        }

    filters = _extract_filters(question, sheet_profile)
    if not filters and inherited_filters:
        filters = [dict(item) for item in inherited_filters if isinstance(item, dict)]
    filter_columns = {str(item.get("column") or "") for item in filters if str(item.get("column") or "")}
    metric_column = None
    metric_columns: list[str] = []
    lookup_columns: list[str] = []
    group_column = None
    axis_column = None
    metric_column_map: dict[int, str] = {}
    metric_column_maps: dict[int, dict[str, str]] = {}
    group_column_map: dict[int, str] = {}
    filter_map: dict[int, list[dict[str, Any]]] = {}
    clarify = None
    if operation == "groupby":
        group_column, clarify = _pick_group_column(question, sheet_profile)
        if not clarify and str(extra.get("aggregate") or "count") != "count":
            metric_columns, clarify = _pick_metric_columns(
                question,
                sheet_profile,
                excluded_columns=filter_columns | {str(group_column or "")},
                allow_multiple=True,
            )
            if metric_columns:
                metric_column = metric_columns[0]
    if operation == "lookup" and not clarify:
        if not filters:
            clarify = {
                "message": "取值类表格问题需要先指定筛选条件，例如“status=hot 时 temperature 是多少”。",
                "reason": "lookup_filter_missing",
            }
        else:
            lookup_columns, clarify = _pick_lookup_columns(
                question,
                sheet_profile,
                excluded_columns=filter_columns,
            )
    if operation == "trend" and not clarify:
        axis_column, clarify = _pick_trend_axis_column(question, sheet_profile)
        if not clarify:
            metric_columns, clarify = _pick_metric_columns(
                question,
                sheet_profile,
                excluded_columns=filter_columns | {str(axis_column or "")},
                allow_multiple=True,
            )
            if metric_columns:
                metric_column = metric_columns[0]
    if operation == "compare_tables" and not clarify:
        compare_group_hint = _extract_hint_token(
            question,
            [
                r"(?:按|按照)\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)",
                r"(?:各|每个)\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)",
            ],
        )
        if compare_group_hint:
            group_column = _resolve_column_hint(sheet_profile, compare_group_hint, numeric_only=False)
            if not group_column:
                clarify = {
                    "message": f"未能识别分组列 {compare_group_hint}，请检查列名。",
                    "reason": "group_column_missing",
                }
            else:
                group_column_map, clarify = _resolve_column_across_profiles(
                    profiles=profile_list,
                    sheet_map=sheet_map,
                    base_column_name=group_column,
                    numeric_only=False,
                )
    if not clarify and operation in {"aggregate", "topk_desc", "topk_asc", "compare_tables"}:
        if str(extra.get("aggregate") or "") != "count":
            metric_columns, clarify = _pick_metric_columns(
                question,
                sheet_profile,
                excluded_columns=filter_columns | {str(group_column or "")},
                allow_multiple=(operation in {"aggregate", "compare_tables"}),
            )
            if metric_columns:
                metric_column = metric_columns[0]
    if not clarify and operation in {"topk_desc", "topk_asc"} and len(metric_columns) > 1:
        clarify = {
            "message": "排序类表格问题暂不支持一次指定多个数值列，请保留一个指标后再提问。",
            "reason": "topk_multi_metric_unsupported",
        }
    if operation == "compare_tables" and not clarify:
        if metric_columns:
            metric_column_maps, clarify = _resolve_columns_across_profiles(
                profiles=profile_list,
                sheet_map=sheet_map,
                base_column_names=metric_columns,
                numeric_only=True,
            )
            if metric_columns:
                first_metric = metric_columns[0]
                metric_column_map = {
                    int(file_id): str(mapped.get(first_metric) or "")
                    for file_id, mapped in metric_column_maps.items()
                    if str(mapped.get(first_metric) or "")
                }
        if not clarify and filters:
            filter_map, clarify = _resolve_filters_across_profiles(
                profiles=profile_list,
                sheet_map=sheet_map,
                filters=filters,
            )
    if clarify:
        return {
            "needs_clarification": True,
            "clarification_message": clarify["message"],
            "clarification_reason": clarify["reason"],
        }

    if workbook_count > 1 and operation != "compare_tables":
        return {
            "needs_clarification": True,
            "clarification_message": "当前命中了多个表格文件，请先明确要分析的文件编号。",
            "clarification_reason": "file_ambiguous",
        }

    return {
        "needs_clarification": False,
        "clarification_message": "",
        "clarification_reason": "",
        "operation": operation,
        "sheet_name": str(sheet_profile.get("sheet_name") or ""),
        "sheet_map": sheet_map,
        "metric_column": metric_column,
        "metric_columns": metric_columns,
        "metric_column_map": metric_column_map,
        "metric_column_maps": metric_column_maps,
        "lookup_columns": lookup_columns,
        "axis_column": axis_column,
        "group_column": group_column,
        "group_column_map": group_column_map,
        "filters": filters,
        "filter_map": filter_map,
        "top_k": int(extra.get("top_k") or 0),
        "aggregate": str(extra.get("aggregate") or ""),
    }


def plan_tabular_query(
    *,
    question: str,
    profile: dict[str, Any] | None = None,
    profiles: list[dict[str, Any]] | None = None,
    workbook_count: int,
    route_hint: str = "",
    table_file_count: int = 0,
    selection_strategy: str = "",
) -> dict[str, Any]:
    subquestions = _split_compound_question(question)
    if len(subquestions) <= 1:
        return _plan_single_tabular_query(
            question=question,
            profile=profile,
            profiles=profiles,
            workbook_count=workbook_count,
            route_hint=route_hint,
            table_file_count=table_file_count,
            selection_strategy=selection_strategy,
        )

    subplans: list[dict[str, Any]] = []
    inherited_filters: list[dict[str, Any]] = []
    for idx, subquestion in enumerate(subquestions):
        subplan = _plan_single_tabular_query(
            question=subquestion,
            profile=profile,
            profiles=profiles,
            workbook_count=workbook_count,
            inherited_filters=inherited_filters if idx > 0 else None,
            route_hint=route_hint,
            table_file_count=table_file_count,
            selection_strategy=selection_strategy,
        )
        if subplan.get("needs_clarification"):
            return subplan
        if not inherited_filters and isinstance(subplan.get("filters"), list) and subplan.get("filters"):
            inherited_filters = [dict(item) for item in subplan.get("filters") or [] if isinstance(item, dict)]
        subplans.append(subplan)

    first = subplans[0]
    return {
        "needs_clarification": False,
        "clarification_message": "",
        "clarification_reason": "",
        "operation": "compound",
        "sheet_name": str(first.get("sheet_name") or ""),
        "sheet_map": first.get("sheet_map") if isinstance(first.get("sheet_map"), dict) else {},
        "metric_column": None,
        "metric_columns": [],
        "metric_column_map": {},
        "metric_column_maps": {},
        "lookup_columns": [],
        "axis_column": None,
        "group_column": None,
        "group_column_map": {},
        "filters": inherited_filters,
        "filter_map": {},
        "top_k": 0,
        "aggregate": "",
        "subplans": subplans,
        "subquestions": subquestions,
    }


__all__ = ["plan_tabular_query"]
