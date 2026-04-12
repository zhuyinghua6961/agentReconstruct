from __future__ import annotations

import time
from typing import Any

from app.modules.graph_kb.classifier import classify_graph_kb_question
from app.modules.graph_kb.client import execute_graph_kb_plan, plan_graph_kb_query
from app.modules.graph_kb.models import GraphKbExecutionResult, GraphKbQueryPlan


def _unique_references(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: set[str] = set()
    refs: list[str] = []
    for row in rows:
        doi = str(row.get("doi") or "").strip()
        if not doi or doi in seen:
            continue
        seen.add(doi)
        refs.append(doi)
    return tuple(refs)


def _clean_items(values: Any, *, limit: int) -> list[str]:
    items: list[str] = []
    for item in list(values or []):
        text = str(item or "").strip()
        if not text or text in items:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def render_graph_kb_answer(plan: GraphKbQueryPlan, rows: list[dict[str, Any]]) -> tuple[str, tuple[str, ...]]:
    if not rows:
        return "", ()

    references = _unique_references(rows)
    if plan.template_id == "lookup_by_doi":
        row = rows[0]
        raw_materials = [str(item).strip() for item in list(row.get("raw_materials") or []) if str(item).strip()]
        answer = f"文献 DOI {row.get('doi') or plan.params.get('doi')} 的标题为《{row.get('title') or '未知标题'}》。"
        if raw_materials:
            answer += f" 图谱里关联到的原料包括：{'；'.join(raw_materials[:3])}。"
        return answer, references
    if plan.template_id == "expand_doi_context_by_doi":
        row = rows[0]
        answer = f"文献 DOI {row.get('doi') or plan.params.get('doi')} 的标题为《{row.get('title') or '未知标题'}》。"
        if bool(plan.params.get("include_testing")):
            testing_items = _clean_items(row.get("testing_items"), limit=3)
            if testing_items:
                answer += f" 图谱里关联到的测试/表征包括：{'；'.join(testing_items)}。"
        if bool(plan.params.get("include_process")):
            preparation_methods = _clean_items(row.get("preparation_methods"), limit=2)
            process_parameters = _clean_items(row.get("process_parameters"), limit=3)
            if preparation_methods:
                answer += f" 制备/工艺方法包括：{'；'.join(preparation_methods)}。"
            if process_parameters:
                answer += f" 关键工艺参数包括：{'；'.join(process_parameters)}。"
        if bool(plan.params.get("include_raw_materials")):
            raw_materials = _clean_items(row.get("raw_materials"), limit=3)
            if raw_materials:
                answer += f" 图谱里关联到的原料包括：{'；'.join(raw_materials)}。"
        return answer, references
    if plan.template_id == "list_by_material":
        material = str(plan.params.get("material_name") or "")
        items = [f"《{row.get('title') or row.get('doi') or '未知条目'}》" for row in rows]
        return f"关于 {material} 的图谱命中文献包括：{'；'.join(items)}。", references
    if plan.template_id == "list_by_raw_material":
        material = str(plan.params.get("material_name") or "")
        items: list[str] = []
        for row in rows:
            title = str(row.get("title") or row.get("doi") or "未知条目")
            matched_raw_materials = _clean_items(row.get("matched_raw_materials"), limit=2)
            if matched_raw_materials:
                items.append(f"《{title}》 (原料命中：{'；'.join(matched_raw_materials)})")
            else:
                items.append(f"《{title}》")
        return f"使用 {material} 作为原料的图谱命中文献包括：{'；'.join(items)}。", references
    if plan.template_id == "count_by_filter":
        material = str(plan.params.get("material_name") or "")
        return f"{material} 在当前图谱中的命中文献数量为 {rows[0].get('count', 0)} 篇。", references
    return "", references
def try_graph_kb_answer(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 3000,
    generation_runtime: Any | None = None,
) -> GraphKbExecutionResult:
    _ = generation_runtime
    decision = classify_graph_kb_question(question, conversation_context=conversation_context or {})
    if decision.decision != "try_graph":
        return GraphKbExecutionResult(handled=False, fallback_reason=decision.reason)

    plan = plan_graph_kb_query(question)
    if plan is None:
        return GraphKbExecutionResult(handled=False, fallback_reason="no_plan")

    started = time.perf_counter()
    try:
        rows = execute_graph_kb_plan(
            plan,
            neo4j_client=neo4j_client,
            max_rows=max_rows,
            timeout_ms=int(timeout_ms or 0),
        )
    except TimeoutError:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return GraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            result_count=0,
            latency_ms=latency_ms,
            fallback_reason="timeout",
        )
    latency_ms = (time.perf_counter() - started) * 1000.0
    if not rows:
        return GraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            result_count=0,
            latency_ms=latency_ms,
            fallback_reason="empty_result",
        )

    answer, references = render_graph_kb_answer(plan, rows)
    if not answer.strip():
        return GraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            result_count=len(rows),
            latency_ms=latency_ms,
            fallback_reason="render_empty",
        )

    return GraphKbExecutionResult(
        handled=True,
        answer=answer,
        references=references,
        query_mode="graph_kb",
        template_id=plan.template_id,
        result_count=len(rows),
        latency_ms=latency_ms,
        fallback_reason="",
    )
