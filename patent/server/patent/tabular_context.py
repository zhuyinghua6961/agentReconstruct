from __future__ import annotations

import re
from typing import Any

from server.patent.pdf_contract import is_summary_question
from server.patent.tabular.renderer import build_tabular_result_context


def _collapse_whitespace(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: object, limit: int) -> str:
    text = _collapse_whitespace(value)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _to_float(value: object) -> float | None:
    normalized = str(value or "").strip().replace(",", "").replace("%", "")
    if not normalized:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def _find_sheet(workbook: dict[str, Any], sheet_name: str) -> dict[str, Any]:
    for sheet in workbook.get("sheets") or []:
        if str(sheet.get("sheet_name") or "") == str(sheet_name or ""):
            return dict(sheet)
    sheets = [dict(item) for item in (workbook.get("sheets") or []) if isinstance(item, dict)]
    return sheets[0] if sheets else {}


def _tokenize(value: object) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./+-]+|[\u4e00-\u9fff]{2,8}", str(value or "").lower()):
        clean = token.strip()
        if len(clean) > 1:
            tokens.add(clean)
    return tokens


def _score_row(question: str, row: dict[str, Any], fallback_index: int) -> tuple[float, int]:
    row_text = " ".join(f"{key}={value}" for key, value in dict(row or {}).items())
    q_tokens = _tokenize(question)
    row_tokens = _tokenize(row_text)
    overlap = len(q_tokens & row_tokens) if q_tokens and row_tokens else 0
    numeric_overlap = len(set(re.findall(r"\d+(?:\.\d+)?", question)) & set(re.findall(r"\d+(?:\.\d+)?", row_text)))
    return (overlap * 2.0 + numeric_overlap * 0.8, -fallback_index)


def _render_row(row: dict[str, Any], *, index: int) -> str:
    parts = [f"{key}={value}" for key, value in dict(row or {}).items()]
    return f"- 样例 {index}: {'; '.join(parts)}" if parts else ""


def _render_sheet_overview(workbook: dict[str, Any]) -> list[str]:
    sheets = [dict(item) for item in (workbook.get("sheets") or []) if isinstance(item, dict)]
    lines = [f"工作簿概览: 共 {len(sheets)} 个工作表"]
    for sheet in sheets[:3]:
        column_names = [str(item) for item in (sheet.get("column_names") or []) if str(item)]
        lines.append(
            f"- {sheet.get('sheet_name') or 'unknown'}: 行数={int(sheet.get('row_count') or 0)}, 列={', '.join(column_names[:8])}"
        )
    return lines


def _render_plan(plan: dict[str, Any]) -> list[str]:
    lines = [
        "执行计划:",
        f"- 工作表: {str(plan.get('sheet_name') or 'unknown')}",
        f"- 操作: {str(plan.get('operation') or 'summary')}",
        f"- 聚合: {str(plan.get('aggregate') or 'mean')}",
    ]
    metric_columns = [str(item) for item in (plan.get("metric_columns") or []) if str(item)]
    if metric_columns:
        lines.append("- 指标列: " + ", ".join(metric_columns))
    lookup_columns = [str(item) for item in (plan.get("lookup_columns") or []) if str(item)]
    if lookup_columns:
        lines.append("- 返回列: " + ", ".join(lookup_columns))
    group_by = str(plan.get("group_by") or "").strip()
    if group_by:
        lines.append(f"- 分组列: {group_by}")
    filters = [dict(item) for item in (plan.get("filters") or []) if isinstance(item, dict)]
    for item in filters:
        lines.append(f"- 过滤: {item.get('column')}={item.get('value')}")
    return lines


def _render_stats(sheet: dict[str, Any], result: dict[str, Any]) -> list[str]:
    lines = ["统计摘要:"]
    summary_stats = dict(result.get("summary_stats") or {})
    lines.append(f"- 命中行数: {int(summary_stats.get('source_row_count') or result.get('row_count') or 0)}")
    aggregate = str(summary_stats.get("aggregate") or result.get("operation") or "").strip()
    if aggregate:
        lines.append(f"- 聚合方式: {aggregate}")
    rows = [dict(item) for item in (sheet.get("rows") or []) if isinstance(item, dict)]
    numeric_columns = [str(item) for item in (sheet.get("numeric_columns") or []) if str(item)]
    for column in numeric_columns[:3]:
        values = [_to_float(row.get(column)) for row in rows]
        values = [value for value in values if value is not None]
        if not values:
            continue
        lines.append(
            f"- {column}: count={len(values)}, min={min(values):g}, max={max(values):g}, mean={sum(values) / len(values):g}"
        )
    return lines


def _pick_rows(question: str, rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    scored = [(_score_row(question, row, index), dict(row)) for index, row in enumerate(rows)]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def build_tabular_context_bundle(
    *,
    question: str,
    workbook: dict[str, Any],
    plan: dict[str, Any],
    result: dict[str, Any],
    file_name: str,
    compact_limit: int,
    answer_limit: int,
    synthesis_limit: int,
) -> dict[str, str]:
    compact_context = _truncate(
        build_tabular_result_context(file_name=file_name, plan=plan, result=result),
        compact_limit,
    )

    sheet = _find_sheet(workbook, str(result.get("sheet_name") or plan.get("sheet_name") or ""))
    sheet_rows = [dict(item) for item in (sheet.get("rows") or []) if isinstance(item, dict)]
    result_rows = [dict(item) for item in (result.get("rows") or []) if isinstance(item, dict)]
    summary_mode = is_summary_question(question)
    representative_limit = 5 if summary_mode else 3
    matched_limit = 5 if summary_mode else 3

    representative_rows = sheet_rows[:representative_limit]
    matched_rows = _pick_rows(question, result_rows or sheet_rows, limit=matched_limit)

    lines: list[str] = [
        f"文件: {file_name}",
        f"问题: {str(question or '').strip()}",
        "",
        *_render_sheet_overview(workbook),
        "",
        *_render_plan(plan),
        "",
        *_render_stats(sheet, result),
        "",
        "代表性行:",
    ]
    rendered_rows = [_render_row(row, index=index) for index, row in enumerate(representative_rows, start=1)]
    lines.extend([item for item in rendered_rows if item] or ["- 当前没有可展示的代表性行。"])
    lines.append("")
    lines.append("命中结果:")
    matched_rendered = [_render_row(row, index=index) for index, row in enumerate(matched_rows, start=1)]
    lines.extend([item for item in matched_rendered if item] or ["- 当前没有可展示的命中结果。"])

    full_context = "\n".join(lines).strip()
    return {
        "compact_evidence_context": compact_context,
        "answer_context": _truncate(full_context, answer_limit),
        "synthesis_context": _truncate(full_context, synthesis_limit),
    }


__all__ = ["build_tabular_context_bundle"]
