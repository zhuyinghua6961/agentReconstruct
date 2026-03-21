from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any


def _render_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> str:
    if not rows:
        return "无"
    lines: list[str] = []
    for idx, row in enumerate(rows[:limit], start=1):
        parts = [f"{key}={value}" for key, value in row.items()]
        lines.append(f"{idx}. " + "; ".join(parts))
    return "\n".join(lines)


def build_tabular_result_context(*, file_name: str, plan: dict[str, Any], result: dict[str, Any]) -> str:
    if str(result.get("operation") or "") == "compound":
        parts: list[str] = [f"文件: {file_name}", "复合问题执行结果:"]
        subquestions = [str(item) for item in (result.get("subquestions") or plan.get("subquestions") or []) if str(item)]
        subresults = [item for item in (result.get("subresults") or []) if isinstance(item, dict)]
        for idx, subresult in enumerate(subresults, start=1):
            subplan = (plan.get("subplans") or [])[idx - 1] if idx - 1 < len(plan.get("subplans") or []) else {}
            label = subquestions[idx - 1] if idx - 1 < len(subquestions) else f"子问题{idx}"
            parts.append(f"子问题 {idx}: {label}")
            parts.append(build_tabular_result_context(file_name=file_name, plan=subplan, result=subresult))
        return "\n".join(parts).strip()

    lines = [
        f"文件: {file_name}",
        f"工作表: {result.get('sheet_name')}",
        f"操作: {result.get('operation')}",
        f"过滤前行数: {result.get('row_count_before')}",
        f"过滤后行数: {result.get('row_count_after')}",
    ]
    summary_stats = result.get("summary_stats") if isinstance(result.get("summary_stats"), dict) else {}
    if summary_stats:
        lines.append("执行结果:")
        for key, value in summary_stats.items():
            if key == "value_map" and isinstance(value, dict):
                for metric_name, metric_value in value.items():
                    lines.append(f"- {metric_name}: {metric_value}")
                continue
            lines.append(f"- {key}: {value}")
    filters = plan.get("filters") or []
    if filters:
        lines.append("过滤条件:")
        for item in filters:
            lines.append(f"- {item.get('column')} {item.get('op')} {item.get('value')}")
    lines.append("结果样例:")
    lines.append(_render_rows(result.get("result_rows") or [], limit=5))
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("注意事项:")
        for item in warnings[:5]:
            lines.append(f"- {item}")
    return "\n".join(lines).strip()


def _build_tabular_prompt(
    *,
    question: str,
    file_name: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    route_hint: str,
    pdf_evidence_context: str = "",
) -> tuple[str, str]:
    context_text = build_tabular_result_context(file_name=file_name, plan=plan, result=result)
    hybrid_mode = str(route_hint).strip().lower() == "hybrid_qa"
    if hybrid_mode:
        prompt = (
            "你是磷酸铁锂领域的混合文件分析助手。"
            "表格执行结果是真实计算结果，必须优先依据这些结果作答。"
            "文献证据只能用于解释和验证，不能覆盖表格结果。\n\n"
            f"用户问题:\n{question}\n\n"
            f"表格执行结果:\n{context_text}\n\n"
            f"文献证据:\n{pdf_evidence_context or '无可用文献证据'}\n\n"
            "请输出：1) 直接结论 2) 数据依据 3) 文献补充/不确定项"
        )
    else:
        prompt = (
            "你是磷酸铁锂领域的表格分析助手。"
            "下面的表格执行结果来自后端真实计算，不允许编造。"
            "请基于执行结果直接回答用户问题；若信息不足，要明确指出。\n\n"
            f"用户问题:\n{question}\n\n"
            f"表格执行结果:\n{context_text}\n\n"
            "请用简洁中文回答，并优先给出结论。"
        )
    return prompt, context_text


def _iter_llm_text_chunks(stream_output: Any) -> Iterator[str]:
    if isinstance(stream_output, str):
        if stream_output:
            yield stream_output
        return
    if isinstance(stream_output, Iterable):
        for item in stream_output:
            content = getattr(item, "content", None)
            text = content if content is not None else item
            text = str(text or "")
            if text:
                yield text
        return
    content = getattr(stream_output, "content", None)
    text = content if content is not None else stream_output
    text = str(text or "")
    if text:
        yield text


def _append_truncation_note(text: str, summary: dict[str, Any]) -> str:
    truncated_count = int(summary.get("truncated_count") or 0)
    returned_count = int(summary.get("returned_count") or 0)
    if truncated_count <= 0:
        return text
    return text + f"\n当前仅展示前 {returned_count} 条/组样例，另有 {truncated_count} 条/组未展开。"


def build_tabular_answer(
    *,
    question: str,
    file_name: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    route_hint: str,
    llm: Any,
    pdf_evidence_context: str = "",
) -> str:
    prompt, _context_text = _build_tabular_prompt(
        question=question,
        file_name=file_name,
        plan=plan,
        result=result,
        route_hint=route_hint,
        pdf_evidence_context=pdf_evidence_context,
    )
    if llm is None:
        return _render_fallback_answer(question=question, file_name=file_name, result=result)
    if hasattr(llm, "invoke"):
        response = llm.invoke(prompt)
        content = getattr(response, "content", None)
        return str(content if content is not None else response).strip()
    if hasattr(llm, "predict"):
        return str(llm.predict(prompt)).strip()
    raise RuntimeError("unsupported llm interface for tabular answer")


def iter_tabular_answer(
    *,
    question: str,
    file_name: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    route_hint: str,
    llm: Any,
    pdf_evidence_context: str = "",
) -> Iterator[str]:
    prompt, _context_text = _build_tabular_prompt(
        question=question,
        file_name=file_name,
        plan=plan,
        result=result,
        route_hint=route_hint,
        pdf_evidence_context=pdf_evidence_context,
    )
    if llm is None:
        yield _render_fallback_answer(question=question, file_name=file_name, result=result)
        return
    if hasattr(llm, "stream"):
        yield from _iter_llm_text_chunks(llm.stream(prompt))
        return
    if hasattr(llm, "invoke"):
        response = llm.invoke(prompt)
        content = getattr(response, "content", None)
        yield str(content if content is not None else response).strip()
        return
    if hasattr(llm, "predict"):
        yield str(llm.predict(prompt)).strip()
        return
    raise RuntimeError("unsupported llm interface for tabular answer")


def _render_fallback_answer(*, question: str, file_name: str, result: dict[str, Any]) -> str:
    operation = str(result.get("operation") or "summary")
    summary = result.get("summary_stats") if isinstance(result.get("summary_stats"), dict) else {}
    if operation == "compound":
        subresults = [item for item in (result.get("subresults") or []) if isinstance(item, dict)]
        subquestions = [str(item) for item in (result.get("subquestions") or []) if str(item)]
        lines: list[str] = []
        for idx, subresult in enumerate(subresults, start=1):
            label = subquestions[idx - 1] if idx - 1 < len(subquestions) else f"子问题{idx}"
            lines.append(f"{idx}. {label}")
            lines.append(_render_fallback_answer(question=label, file_name=file_name, result=subresult))
        return "\n".join(lines).strip()
    if operation == "summary":
        return (
            f"已完成对表格《{file_name}》的概览。"
            f"当前工作表共有 {summary.get('row_count', 0)} 行、{summary.get('column_count', 0)} 列。"
        )
    if operation == "count_rows":
        return _append_truncation_note(f"根据全表执行结果，命中记录数为 {summary.get('matched_count', 0)} 条。", summary)
    if operation == "lookup":
        value = summary.get("value")
        lookup_columns = [str(item) for item in (summary.get("lookup_columns") or result.get("lookup_columns") or []) if str(item)]
        if value is not None and len(lookup_columns) == 1:
            return f"根据全表匹配结果，列 {lookup_columns[0]} 的取值为 {value}。"
        rows = result.get("result_rows") or []
        return _append_truncation_note(f"根据全表匹配结果，共命中 {summary.get('matched_count', 0)} 条记录。取值如下：\n" + _render_rows(rows, limit=8), summary)
    if operation == "trend":
        axis_column = str(summary.get("axis_column") or result.get("axis_column") or "横轴")
        metric_columns = [str(item) for item in (summary.get("metric_columns") or result.get("metric_columns") or []) if str(item)]
        parts: list[str] = []
        for metric in metric_columns:
            direction = str(summary.get(f"{metric}_direction") or "")
            delta = summary.get(f"{metric}_delta")
            if direction:
                direction_zh = "上升" if direction == "up" else ("下降" if direction == "down" else "基本持平")
                parts.append(f"{metric}{direction_zh}(delta={delta})")
        if parts:
            return _append_truncation_note(f"根据全表趋势分析，按 {axis_column} 排序后：" + "；".join(parts) + "。", summary)
        rows = result.get("result_rows") or []
        return _append_truncation_note(f"已按 {axis_column} 完成趋势整理，序列如下：\n" + _render_rows(rows, limit=8), summary)
    if operation == "aggregate":
        agg = str(summary.get("aggregate") or "统计")
        value_map = summary.get("value_map") if isinstance(summary.get("value_map"), dict) else {}
        if value_map:
            parts = [f"{col}={value}" for col, value in value_map.items()]
            return _append_truncation_note(f"根据全表执行结果，{agg}结果为：" + "；".join(parts) + "。", summary)
        col = str(summary.get("metric_column") or "目标列")
        value = summary.get("value")
        return _append_truncation_note(f"根据全表执行结果，列 {col} 的 {agg} 值为 {value}。", summary)
    if operation == "groupby":
        group_col = str(summary.get("group_column") or result.get("group_column") or "分组列")
        agg = str(summary.get("aggregate") or "count")
        rows = result.get("result_rows") or []
        top_k = int(summary.get("top_k") or 0)
        prefix = f"已按列 {group_col} 完成 {agg} 分组统计"
        if top_k > 0:
            prefix += f"（前 {top_k} 项）"
        prefix += "，结果如下：\n"
        return _append_truncation_note(prefix + _render_rows(rows, limit=8), summary)
    if operation in {"topk_desc", "topk_asc"}:
        rows = result.get("result_rows") or []
        return _append_truncation_note("已按全表执行结果完成排序，前几项如下：\n" + _render_rows(rows, limit=5), summary)
    if operation == "filter_rows":
        count = summary.get("matched_count", 0)
        rows = result.get("result_rows") or []
        return _append_truncation_note(f"根据全表筛选结果，共命中 {count} 条记录。样例如下：\n" + _render_rows(rows, limit=5), summary)
    if operation == "compare_tables":
        agg = str(summary.get("aggregate") or "count")
        metric_columns = [str(item) for item in (summary.get("metric_columns") or []) if str(item)]
        metric = ", ".join(metric_columns) if metric_columns else str(summary.get("metric_column") or "")
        group_col = str(summary.get("group_column") or result.get("group_column") or "")
        top_k = int(summary.get("top_k") or 0)
        rows = result.get("result_rows") or []
        prefix = "已完成多表对比"
        if group_col:
            prefix += f"（按 {group_col} 分组, {agg}"
        else:
            prefix += f"（{agg}"
        if metric:
            prefix += f", 列 {metric}"
        if top_k > 0:
            prefix += f", 前 {top_k} 项"
        prefix += "），结果如下：\n"
        return _append_truncation_note(prefix + _render_rows(rows, limit=8), summary)
    return f"已完成对表格《{file_name}》的执行型分析。"


__all__ = ["build_tabular_answer", "build_tabular_result_context", "iter_tabular_answer"]
